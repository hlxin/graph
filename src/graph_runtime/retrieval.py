"""Exact text retrieval over items that name a projection's records: keyword (BM25) scores read from an inverted
index, the cosine of hashed int8 vectors, and two fusions of them, each ranking exact, ties broken by item id.

It is the text half of "search, then traverse": a hit names its record (``record``), and the nodes the hits name can
start a traversal (:func:`seeds`); a traversal's records can in turn restrict a search (``records``). Relevance is a
heuristic ranking, not evidence. What is exact is the computation: the same items, query and settings give the same
hits, in the same order, with the same scores, on every machine.

Needs numpy (``pip install 'graph-runtime[retrieval]'``). Nothing else in GRAPH imports it.

* :class:`Tokenizer`: lower-cased words, plus what an application adds for its vocabulary. Aliases are
  case-sensitive (a formula and its name: CO is carbon monoxide, Co cobalt) and variants are spellings. Its id is
  the digest of its settings.
* :class:`HashEmbedder`: the default vectors. They need no model and are deterministic. Each word, its character
  4-grams (robust to OCR errors) and each pair of adjacent words are hashed into ``dim`` signed dimensions with
  sublinear weights. Any object with an ``id`` and ``encode(texts) -> float array`` can embed instead (a neural
  sentence model), and a search refuses to mix embedders.
* :class:`Index`: the items, their int8 vectors (each row L2-normalized, then scaled to its largest entry), their
  BM25 statistics as an inverted index, and ``index_id``, a digest of all of them. It is saved as ``<stem>.jsonl``
  (a header and the items), ``<stem>.i8`` (the vectors) and ``<stem>.terms`` (the inverted index). Digests tie each
  part to the items it was built from, and a part that does not match them is rebuilt from the items, or refused
  when it cannot be.

A search reads each item once. BM25 reads only the postings of the query's words. The cosine is the int8 product
summed in float32 blocks, which is exact because every partial sum of int8 products stays below 2**24, over norms
computed once. Only the best candidates are sorted. Scores and order are those of the plain definitions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from ._canonical import canonical_json, digest, freeze, sha256_text, thaw
from .errors import RetrievalError

WORD = r"[^\W_]+(?:[.,][0-9]+)?"     # letters and digits of any script, with a decimal part ("2.5", "0,1")
MODES = ("hybrid", "vector", "keyword", "rrf")
RRF_K = 60             # reciprocal rank fusion: an item scores 1 / (RRF_K + its rank) in each ranking it is in
RRF_DEPTH = 1000       # how deep each ranking is read for rrf (at least twice the hits asked for)
BLOCK = 65536          # rows of the vectors converted at a time for the cosine, bounding its working memory
CACHE_BYTES = 2 << 30  # vectors up to this size (as float32) are converted once and kept for the next queries
TERMS_MAGIC = b"GRAPH-TERMS-1\n"
INDEX_KIND = "graph_runtime_index"
SCORES = ("score", "keyword", "cosine")      # what a hit adds to its item, so no item may hold them


def _np():
    try:
        import numpy
    except ImportError as e:  # pragma: no cover - the extra is installed wherever the tests run
        raise RetrievalError("needs_numpy", "retrieval needs numpy: pip install 'graph-runtime[retrieval]'") from e
    return numpy


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _text_map(value: Any, what: str, lower: bool) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RetrievalError("invalid_tokenizer", f"{what} is a mapping of words to the text added after them")
    for k, v in value.items():
        if not isinstance(k, str) or not k.strip() or not isinstance(v, str):
            raise RetrievalError("invalid_tokenizer", f"{what}: {k!r} -> {v!r} is not a word mapped to text")
        if lower and k != k.lower():
            raise RetrievalError("invalid_tokenizer", f"{what}: {k!r} is not lower-cased, so no word would match it")
    return dict(sorted(value.items()))


class Tokenizer:
    """The words of a text, lower-cased, with what an application adds for its vocabulary.

    ``aliases`` maps a case-sensitive whole word (a formula, a symbol) to text added after it: with
    ``{"Cu": "copper"}``, "Cu" also matches "copper". ``variants`` maps a lower-cased word to words added after it,
    such as a spelling (``{"sulphur": "sulfur"}``). ``word`` is the regular expression of one word, matched without
    regard to case and without capturing groups. ``id`` is the digest of all three, and an index records it."""

    def __init__(self, *, aliases: Mapping[str, str] | None = None, variants: Mapping[str, str] | None = None,
                 word: str = WORD):
        self.aliases = _text_map(aliases, "aliases", lower=False)
        self.variants = _text_map(variants, "variants", lower=True)
        try:
            self._word = re.compile(word, re.I)
        except (re.error, TypeError) as e:
            raise RetrievalError("invalid_tokenizer", f"word is not a regular expression: {e}") from e
        if self._word.groups:
            raise RetrievalError("invalid_tokenizer", "word has capturing groups; use (?:...)")
        self.word = word
        self._alias = (re.compile(r"\b(" + "|".join(map(re.escape, sorted(self.aliases, key=len, reverse=True)))
                                  + r")\b") if self.aliases else None)
        self.config = {"word": word, "aliases": self.aliases, "variants": self.variants}
        self.id = digest({"kind": "graph_runtime_tokenizer", "version": 1, **self.config})

    def tokens(self, text: str) -> list[str]:
        """The words of ``text``, each lower-cased and followed by what its alias or variant adds."""
        if self._alias is not None:
            text = self._alias.sub(lambda m: f"{m.group(1)} {self.aliases[m.group(1)]}", text)
        out = []
        for w in self._word.findall(text):
            lw = w.lower()
            out.append(lw)
            alt = self.variants.get(lw)
            if alt and alt != lw:
                out.extend(alt.split())
        return out

    def __repr__(self) -> str:
        return f"Tokenizer({len(self.aliases)} aliases, {len(self.variants)} variants, {self.id[:19]})"


class HashEmbedder:
    """Vectors that need no model and are the same on every machine.

    The features are each word (weight 1), its character 4-grams with < and > marking its ends (0.5 each) and each
    pair of adjacent words (1). Each is hashed (BLAKE2b) to one of ``dim`` dimensions and a sign, with a sublinear
    weight (1 + log of a weight of at least 1). The weights are multiples of 0.5, so their sums are exact in any
    order. ``id`` names the dimension and the tokenizer."""

    def __init__(self, tokenizer: Tokenizer | None = None, dim: int = 512):
        if not isinstance(dim, int) or isinstance(dim, bool) or not 1 <= dim <= 65536:
            raise RetrievalError("invalid_embedder", f"dim is a whole number from 1 to 65536 (got {dim!r})")
        if tokenizer is not None and not isinstance(tokenizer, Tokenizer):
            raise RetrievalError("invalid_embedder", "tokenizer is a graph_runtime.retrieval.Tokenizer")
        self.tokenizer = tokenizer or Tokenizer()
        self.dim = dim
        self.id = f"graph-hash-v1/{dim}/{self.tokenizer.id}"

    def features(self, text: str, grams: dict | None = None) -> Counter:
        """The weighted features of ``text``. ``grams`` caches each word's 4-grams across calls."""
        ws = self.tokenizer.tokens(text)
        grams = {} if grams is None else grams
        f: Counter = Counter()
        for w, c in Counter(ws).items():
            f["w:" + w] += float(c)
            g = grams.get(w)
            if g is None:
                padded = f"<{w}>"
                g = grams[w] = ["c:" + padded[i:i + 4] for i in range(len(padded) - 3)] if len(padded) > 4 else []
            for x in g:
                f[x] += 0.5 * c
        for a, b in zip(ws, ws[1:]):
            f["b:" + a + " " + b] += 1.0
        return f

    def encode(self, texts: list[str]):
        np = _np()
        out = np.zeros((len(texts), self.dim), dtype=np.float64)
        slots: dict[str, tuple[int, float]] = {}     # each feature hashed once per call (words and 4-grams recur)
        grams: dict[str, list[str]] = {}
        done: dict[str, int] = {}                    # each text encoded once (quotes recur on several records)
        log = math.log
        for r, t in enumerate(texts):
            first = done.setdefault(t, r)
            if first != r:
                out[r] = out[first]
                continue
            row = [0.0] * self.dim                   # summed as Python floats: the same IEEE doubles as numpy's
            for feat, tf in sorted(self.features(t, grams).items()):
                slot = slots.get(feat)
                if slot is None:
                    h = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
                    slot = slots[feat] = (int.from_bytes(h[:4], "little") % self.dim, 1.0 if h[4] & 1 else -1.0)
                idx, sign = slot
                row[idx] += sign * (1.0 + log(tf)) if tf >= 1 else sign * tf
            out[r] = row
        return out


def quantize(mat):
    """Row-wise L2 normalization, then int8 (a cosine is unchanged by the row's scale). Each row is first scaled by
    a power of two that brings its largest entry into [1/2, 1): exact, so the result is the same as without it, and the
    norm of a row of huge finite entries cannot overflow to infinity (which would zero the row)."""
    np = _np()
    mat = np.asarray(mat, dtype=np.float64)
    _, exponent = np.frexp(np.abs(mat).max(axis=1, keepdims=True))
    mat = np.ldexp(mat, -exponent)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = mat / norms
    peak = np.abs(unit).max(axis=1, keepdims=True)
    peak[peak == 0] = 1.0
    return np.rint(unit / peak * 127).astype(np.int8)


def split_text(text: str, limit: int = 900) -> list[tuple[int, int]]:
    """Character spans of the passages of a text: paragraphs joined up to ``limit`` characters, and a paragraph
    longer than the limit cut at sentence ends (or at the limit when no sentence ends in its last two thirds)."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 3:
        raise RetrievalError("invalid_limit", f"limit is a whole number of at least 3 (got {limit!r})")
    spans, start, end = [], None, None
    pieces = []
    for m in re.finditer(r"\S(?:.*?\S)?(?=\s*\n\s*\n|\s*\Z)", text, re.S):
        a, b = m.start(), m.end()
        while b - a > limit:
            cut = text.rfind(". ", a, a + limit)
            cut = cut + 1 if cut > a + limit // 3 else a + limit
            pieces.append((a, cut))
            a = cut
            while a < b and text[a].isspace():
                a += 1
        if b > a:
            pieces.append((a, b))
    for a, b in pieces:
        if start is None:
            start, end = a, b
        elif b - start <= limit:
            end = b
        else:
            spans.append((start, end))
            start, end = a, b
    if start is not None:
        spans.append((start, end))
    return spans


def _checked_items(items: Iterable[Any]) -> tuple:
    """The items, frozen, after checking each is a JSON object with a unique text ``id``, a ``text`` and, when
    present, a text ``record``, holding none of the fields a hit adds."""
    if isinstance(items, (str, bytes, Mapping)):
        raise RetrievalError("invalid_items", "items is a list of {id, text, ...} objects")
    out, seen = [], set()
    for n, it in enumerate(items):
        if not isinstance(it, Mapping) or not isinstance(it.get("id"), str) or not isinstance(it.get("text"), str):
            raise RetrievalError("invalid_items", f"item {n} is not an object with a text id and a text")
        if it["id"] in seen:
            raise RetrievalError("duplicate_item", f"two items have the id {it['id']!r}")
        if it.get("record") is not None and not isinstance(it["record"], str):
            raise RetrievalError("invalid_items", f"item {it['id']!r}: record is the id of a record, as text")
        held = [k for k in SCORES if k in it]
        if held:
            raise RetrievalError("invalid_items", f"item {it['id']!r} holds {held}, which a hit adds")
        try:
            canonical_json(it)
            it["text"].encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as e:
            raise RetrievalError("invalid_items", f"item {it['id']!r} is not JSON text: {e}") from e
        seen.add(it["id"])
        out.append(freeze(it))
    return tuple(out)


def _items_digest(items: tuple) -> str:
    return sha256_text("".join(canonical_json(thaw(it)) + "\n" for it in items))


def _encoded(embedder: Any, texts: list[str]):
    np = _np()
    out = np.asarray(embedder.encode(texts), dtype=np.float64)
    if out.ndim != 2 or out.shape[0] != len(texts) or not np.isfinite(out).all():
        raise RetrievalError("invalid_embedding", f"the embedder {embedder.id!r} did not return one finite vector "
                                                  "per text")
    return out


def _embedder_id(embedder: Any) -> str:
    eid = getattr(embedder, "id", None)
    if not isinstance(eid, str) or not eid or not callable(getattr(embedder, "encode", None)):
        raise RetrievalError("invalid_embedder", "an embedder has a text id and encode(texts) -> float array")
    return eid


def _terms(items: tuple, tokenizer: Tokenizer):
    """The BM25 statistics of ``items`` as an inverted index: the words in sorted order, and for each word the items
    that hold it (in item order) with their counts (``indptr`` delimits each word's run), and each item's length."""
    np = _np()
    first: dict[str, int] = {}
    words, rows, counts = [], [], []
    lengths = np.zeros(len(items), dtype=np.int64)
    counted: dict[str, Counter] = {}                 # each text tokenized once
    for i, it in enumerate(items):
        c = counted.get(it["text"])
        if c is None:
            c = counted[it["text"]] = Counter(tokenizer.tokens(it["text"]))
        lengths[i] = sum(c.values())
        for w, f in c.items():
            words.append(first.setdefault(w, len(first)))
            rows.append(i)
            counts.append(f)
    vocab = sorted(first)
    remap = np.empty(len(first), dtype=np.int64)
    for new_id, w in enumerate(vocab):
        remap[first[w]] = new_id
    term = remap[np.asarray(words, dtype=np.int64)]
    rows_a = np.asarray(rows, dtype=np.int32)
    order = np.lexsort((rows_a, term))
    indptr = np.zeros(len(vocab) + 1, dtype=np.int64)
    np.cumsum(np.bincount(term, minlength=len(vocab)), out=indptr[1:])
    return vocab, indptr, rows_a[order], np.asarray(counts, dtype=np.int32)[order], lengths


def _terms_digest(vocab: list, payload: bytes) -> str:
    """The digest of a .terms file's words and arrays together: the words say which postings are whose."""
    return _sha256(json.dumps(vocab, ensure_ascii=False).encode("utf-8") + b"\0" + payload)


def _read_terms(stem: Path, items_sha256: str, tokenizer_id: str, count: int):
    """The inverted index saved at ``<stem>.terms``, or None (then it is rebuilt from the items) when there is none,
    it was saved with other items or another tokenizer, its arrays do not match the digest it records, or they do not
    form an inverted index of these items (offsets that run up to the postings, rows within the items, counts above
    zero)."""
    np = _np()
    p = Path(f"{stem}.terms")
    if not p.exists():
        return None
    data = p.read_bytes()
    if not data.startswith(TERMS_MAGIC):
        return None
    at = len(TERMS_MAGIC)
    try:
        size = int.from_bytes(data[at:at + 8], "little")
        head = json.loads(data[at + 8:at + 8 + size].decode("utf-8"))
        words, postings, vocab = int(head["words"]), int(head["postings"]), head["vocab"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None
    if (head.get("count") != count or head.get("items_sha256") != items_sha256
            or head.get("tokenizer") != tokenizer_id or words < 0 or postings < 0):
        return None
    at += 8 + size
    if not isinstance(vocab, list) or head.get("payload_sha256") != _terms_digest(vocab, data[at:]):
        return None                       # the words or the arrays changed since they were saved
    out = []
    for t, length in ((np.int64, words + 1), (np.int32, postings), (np.int32, postings), (np.int64, count)):
        nbytes = length * np.dtype(t).itemsize
        out.append(np.frombuffer(data[at:at + nbytes], dtype=t))
        at += nbytes
    indptr, rows, counts, lengths = out
    if at != len(data) or len(vocab) != words or not all(isinstance(w, str) for w in vocab) \
            or vocab != sorted(set(vocab)):
        return None
    if words + 1 != len(indptr) or indptr[0] != 0 or indptr[-1] != postings or (np.diff(indptr) < 0).any():
        return None
    if postings and (rows.min() < 0 or rows.max() >= count or counts.min() < 1):
        return None
    return (vocab, *out)


class Index:
    """Items (``{id, text, record?, ...}``) with BM25 statistics and int8 vectors, searched exactly.

    Make one with :meth:`build` or :meth:`load`. ``index_id`` is the digest of the items, the embedder, the
    tokenizer and the vectors: bind it into a projection (``bindings``) to make results stale when the index
    changes. ``rebuilt`` names the saved parts :meth:`load` rebuilt because they did not match the items."""

    def __init__(self, items: tuple, vectors, *, embedder_id: str, tokenizer: Tokenizer, embedder: Any = None,
                 terms=None, embedder_config: dict | None = None, items_sha256: str | None = None,
                 rebuilt: tuple[str, ...] = ()):
        np = _np()
        self.items, self.vectors, self.embedder_id = items, vectors, embedder_id
        self.tokenizer, self.embedder, self.rebuilt = tokenizer, embedder, rebuilt
        self._embedder_config = embedder_config
        self.vocab, self.indptr, self.post_item, self.post_count, self.lengths = terms or _terms(items, tokenizer)
        self.term_id = {w: i for i, w in enumerate(self.vocab)}
        n = len(items)
        self.avg = (int(self.lengths.sum()) / n) if n else 0.0
        self.norms = np.ones(n)
        for lo in range(0, n, BLOCK):
            self.norms[lo:lo + BLOCK] = np.linalg.norm(vectors[lo:lo + BLOCK].astype(np.float64), axis=1)
        self.norms[self.norms == 0] = 1.0
        for a in (self.vectors, self.indptr, self.post_item, self.post_count, self.lengths, self.norms):
            a.setflags(write=False)
        self._items_sha256 = items_sha256
        self._index_id = None
        self._id_rank = None
        self._converted = None                      # the vectors as float32, kept after a first cosine when they fit
        self._by_record = None

    @classmethod
    def build(cls, items: Iterable[Mapping], *, embedder: Any = None, tokenizer: Tokenizer | None = None) -> "Index":
        """The index of ``items``. ``embedder`` defaults to a :class:`HashEmbedder` over ``tokenizer``; ``tokenizer``
        (for BM25 and queries) defaults to the hash embedder's, else a default :class:`Tokenizer`."""
        np = _np()
        if tokenizer is not None and not isinstance(tokenizer, Tokenizer):
            raise RetrievalError("invalid_tokenizer", "tokenizer is a graph_runtime.retrieval.Tokenizer")
        items = _checked_items(items)
        embedder = HashEmbedder(tokenizer) if embedder is None else embedder
        eid = _embedder_id(embedder)
        tokenizer = tokenizer or (embedder.tokenizer if isinstance(embedder, HashEmbedder) else Tokenizer())
        if items:
            vectors = quantize(_encoded(embedder, [it["text"] for it in items]))
        else:
            vectors = np.zeros((0, getattr(embedder, "dim", 1)), dtype=np.int8)
        config = ({"dim": embedder.dim, "tokenizer": embedder.tokenizer.config}
                  if isinstance(embedder, HashEmbedder) else None)
        return cls(items, vectors, embedder_id=eid, tokenizer=tokenizer, embedder=embedder, embedder_config=config)

    def __len__(self) -> int:
        return len(self.items)

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1])

    @property
    def items_sha256(self) -> str:
        """The digest of the items, one canonical JSON line each."""
        if self._items_sha256 is None:
            self._items_sha256 = _items_digest(self.items)
        return self._items_sha256

    @property
    def index_id(self) -> str:
        """The digest of the items, the embedder, the tokenizer and the vectors."""
        if self._index_id is None:
            self._index_id = digest({"kind": INDEX_KIND, "version": 1, "items": self.items_sha256,
                                     "embedder": self.embedder_id, "tokenizer": self.tokenizer.id,
                                     "vectors": _sha256(_np().ascontiguousarray(self.vectors).tobytes())})
        return self._index_id

    def __repr__(self) -> str:
        return f"Index({len(self.items)} items, {self.embedder_id}, {self.index_id[:19]})"

    # ------------------------------------------------------------------------------------------ persistence
    def save(self, stem: str | Path) -> None:
        """Write ``<stem>.jsonl``, ``<stem>.i8`` and ``<stem>.terms``."""
        np = _np()
        stem = Path(stem)
        stem.parent.mkdir(parents=True, exist_ok=True)
        vec_bytes = np.ascontiguousarray(self.vectors, dtype=np.int8).tobytes()
        head = {"kind": INDEX_KIND, "version": 1, "embedder": self.embedder_id, "count": len(self.items),
                "dim": self.dim, "tokenizer": self.tokenizer.config, "items_sha256": self.items_sha256,
                "vectors_sha256": _sha256(vec_bytes), "index_id": self.index_id}
        if self._embedder_config is not None:
            head["hash_embedder"] = self._embedder_config
        raw = "".join(canonical_json(x) + "\n" for x in [head, *map(thaw, self.items)]).encode("utf-8")
        Path(f"{stem}.jsonl").write_bytes(raw)
        Path(f"{stem}.i8").write_bytes(vec_bytes)
        payload = b"".join(np.ascontiguousarray(a, dtype=t).tobytes() for a, t in (
            (self.indptr, np.int64), (self.post_item, np.int32), (self.post_count, np.int32), (self.lengths, np.int64)))
        terms_head = json.dumps({"items_sha256": self.items_sha256, "tokenizer": self.tokenizer.id,
                                 "count": len(self.items), "words": len(self.vocab),
                                 "postings": int(len(self.post_item)), "vocab": self.vocab,
                                 "payload_sha256": _terms_digest(self.vocab, payload)},
                                ensure_ascii=False, sort_keys=True).encode("utf-8")
        with open(f"{stem}.terms", "wb") as f:
            f.write(TERMS_MAGIC + len(terms_head).to_bytes(8, "little") + terms_head + payload)

    @classmethod
    def load(cls, stem: str | Path, *, embedder: Any = None) -> "Index":
        """The index saved at ``stem``. A hash embedder is rebuilt from the saved settings; any other embedder is
        passed as ``embedder`` and must have the saved id. Vectors that were not saved with these items are rebuilt
        with the embedder, or refused without it (``stale_vectors``). An inverted index that was not saved with them
        is rebuilt."""
        np = _np()
        try:
            lines = Path(f"{stem}.jsonl").read_bytes().decode("utf-8").splitlines()
            head = json.loads(lines[0])
            raw_items = [json.loads(x) for x in lines[1:]]
        except (OSError, UnicodeDecodeError, ValueError, IndexError) as e:
            raise RetrievalError("unreadable_index", f"{stem}.jsonl is not a saved index: {e}") from e
        if not isinstance(head, dict) or head.get("kind") != INDEX_KIND or head.get("version") != 1:
            raise RetrievalError("unreadable_index", f"{stem}.jsonl is not a saved index of this version")
        try:
            tokenizer = Tokenizer(**head["tokenizer"])
            saved_id, count, dim = head["embedder"], int(head["count"]), int(head["dim"])
            hash_config = head.get("hash_embedder")
            if embedder is None and hash_config is not None:
                embedder = HashEmbedder(Tokenizer(**hash_config["tokenizer"]), hash_config["dim"])
        except (KeyError, TypeError, ValueError) as e:
            raise RetrievalError("unreadable_index", f"{stem}.jsonl has a malformed header: {e}") from e
        if embedder is not None and _embedder_id(embedder) != saved_id:
            raise RetrievalError("embedder_mismatch", f"the index was built with {saved_id}, not {embedder.id}")
        items = _checked_items(raw_items)
        items_sha256 = _items_digest(items)
        rebuilt = []
        vec_path = Path(f"{stem}.i8")
        vec_bytes = vec_path.read_bytes() if vec_path.exists() else b""
        if (head.get("items_sha256") == items_sha256 and head.get("vectors_sha256") == _sha256(vec_bytes)
                and count == len(items) and len(vec_bytes) == count * dim):
            raw_vec = np.frombuffer(vec_bytes, dtype=np.int8)
            vectors = raw_vec.reshape(count, dim) if count else raw_vec.reshape(0, max(dim, 1))
        elif embedder is None:
            raise RetrievalError("stale_vectors", f"the vectors saved at {stem}.i8 are not those of its items, and "
                                                  f"rebuilding them needs the embedder {saved_id}")
        else:
            rebuilt.append("vectors")
            vectors = (quantize(_encoded(embedder, [it["text"] for it in items])) if items
                       else np.zeros((0, max(dim, 1)), dtype=np.int8))
        terms = _read_terms(Path(stem), items_sha256, tokenizer.id, len(items))
        if terms is None:
            rebuilt.append("terms")
        return cls(items, vectors, embedder_id=saved_id, tokenizer=tokenizer, embedder=embedder, terms=terms,
                   embedder_config=hash_config, items_sha256=items_sha256, rebuilt=tuple(rebuilt))

    # ------------------------------------------------------------------------------------------ scores
    def bm25(self, query: str, k1: float = 1.2, b: float = 0.75):
        """Each item's BM25 score for ``query`` (its words in sorted order, each once)."""
        np = _np()
        n = len(self.items)
        s = np.zeros(n)
        for w in sorted(set(self.tokenizer.tokens(query))):
            t = self.term_id.get(w)
            if t is None:
                continue
            lo, hi = int(self.indptr[t]), int(self.indptr[t + 1])
            df = hi - lo
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            rows, f = self.post_item[lo:hi], self.post_count[lo:hi].astype(np.float64)
            s[rows] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lengths[rows] / (self.avg or 1)))
        return s

    def cosine(self, query: str, embedder: Any = None):
        """Each item's cosine with ``query``, both as int8 vectors."""
        np = _np()
        emb = embedder if embedder is not None else self.embedder
        if emb is None:
            raise RetrievalError("needs_embedder", f"the index was built with {self.embedder_id}: pass that embedder")
        if _embedder_id(emb) != self.embedder_id:
            raise RetrievalError("embedder_mismatch", f"the index was built with {self.embedder_id}, the query "
                                                      f"embedder is {emb.id}")
        qv = quantize(_encoded(emb, [query]))[0]
        n, dim = self.vectors.shape
        if qv.shape[0] != dim:
            raise RetrievalError("invalid_embedding", f"the query vector has {qv.shape[0]} dimensions, the index {dim}")
        # int8 products summed in float32 are exact while every partial sum stays below 2**24
        exact = np.float32 if dim * 127 * 127 < 2 ** 24 else np.float64
        q = qv.astype(exact)
        if self._converted is None and exact is np.float32 and n * dim * 4 <= CACHE_BYTES:
            self._converted = self.vectors.astype(np.float32)
        if self._converted is not None:
            dots = (self._converted @ q).astype(np.float64)
        else:
            dots = np.empty(n)
            for lo in range(0, n, BLOCK):
                dots[lo:lo + BLOCK] = self.vectors[lo:lo + BLOCK].astype(exact) @ q
        return dots / (self.norms * (np.linalg.norm(qv.astype(np.float64)) or 1.0))

    def _ids(self):
        """Each item's position in item-id order (the tie-break of every ranking), computed once."""
        np = _np()
        if self._id_rank is None:
            order = sorted(range(len(self.items)), key=lambda i: self.items[i]["id"])
            rank = np.empty(len(self.items), dtype=np.int64)
            rank[order] = np.arange(len(self.items))
            self._id_rank = rank
        return self._id_rank

    def ranked(self, score, m: int):
        """The (at most) ``m`` items of positive score, best first and ties by item id: exactly the head of a full
        sort, since every item tied with the m-th best is a candidate before the candidates are sorted."""
        np = _np()
        pos = np.flatnonzero(score > 0)
        if m < 1:
            return pos[:0]
        if m < len(pos):
            cut = np.partition(score[pos], len(pos) - m)[len(pos) - m]
            pos = pos[score[pos] >= cut]
        return pos[np.lexsort((self._ids()[pos], -score[pos]))][:m]

    def _kept(self, records: Iterable[str] | None):
        """The items whose record is one of ``records``, as a mask (None: every item)."""
        np = _np()
        if records is None:
            return None
        if isinstance(records, (str, bytes, Mapping)):
            raise RetrievalError("invalid_records", "records is a collection of record ids, not one id")
        if self._by_record is None:
            by: dict[str, list[int]] = {}
            for i, it in enumerate(self.items):
                if it.get("record") is not None:
                    by.setdefault(it["record"], []).append(i)
            self._by_record = {r: np.asarray(v, dtype=np.int64) for r, v in by.items()}
        keep = np.zeros(len(self.items), dtype=bool)
        for r in records:
            if not isinstance(r, str):
                raise RetrievalError("invalid_records", f"records holds {r!r}, which is not a record id")
            at = self._by_record.get(r)
            if at is not None:
                keep[at] = True
        return keep

    # ------------------------------------------------------------------------------------------ search
    def search(self, query: str, *, k: int = 10, mode: str = "hybrid", records: Iterable[str] | None = None,
               where: Callable[[Mapping], Any] | None = None, embedder: Any = None) -> list[dict]:
        """The best ``k`` items for ``query``, each a copy of the item with ``score``, ``keyword`` (BM25) and
        ``cosine`` (None when the mode does not compute it).

        Modes: ``hybrid``, the mean of BM25 scaled to its best over all items and the cosine (a negative cosine
        counted as 0); ``vector`` and ``keyword``, one of them; ``rrf``, reciprocal rank fusion of the BM25 and cosine
        rankings, each read to RRF_DEPTH (or twice ``k``), an item scoring the sum of 1 / (RRF_K + its rank), at most
        2 / 61. Only items of positive score are hits, best first, ties by item id.

        ``records`` keeps the items whose record is one of them, and ``where`` the items it is true of. With either,
        an rrf fuses the rankings of the kept items alone, so the items left out never crowd out the ones kept; in
        the other modes an item's score does not depend on them."""
        np = _np()
        if not isinstance(query, str):
            raise RetrievalError("invalid_query", "query is text")
        try:
            query.encode("utf-8")
        except UnicodeEncodeError as e:
            raise RetrievalError("invalid_query", f"query is not valid text: {e}") from e
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            raise RetrievalError("invalid_k", f"k is how many hits to return, a whole number of at least 1 (got {k!r})")
        if mode not in MODES:
            raise RetrievalError("invalid_mode", f"mode is one of {', '.join(MODES)} (got {mode!r})")
        if where is not None and not callable(where):
            raise RetrievalError("invalid_where", "where is a function of an item")
        keep = self._kept(records)
        if not self.items:
            return []
        n = len(self.items)
        kw = self.bm25(query) if mode != "vector" else None
        vc = self.cosine(query, embedder) if mode != "keyword" else None
        if mode == "rrf":
            if where is not None:
                passed = np.fromiter((bool(where(it)) for it in self.items), dtype=bool, count=n)
                keep, where = (passed if keep is None else keep & passed), None
            score = np.zeros(n)
            for s in (kw, vc):
                top = self.ranked(s if keep is None else np.where(keep, s, 0.0), max(RRF_DEPTH, 2 * k))
                score[top] += 1.0 / (RRF_K + np.arange(1, len(top) + 1))
        elif mode == "hybrid":
            peak = kw.max()
            score = 0.5 * (kw / peak if peak > 0 else kw) + 0.5 * np.clip(vc, 0, None)
        else:
            score = vc if mode == "vector" else kw
        if keep is not None:
            score = np.where(keep, score, 0.0)
        m = k
        while True:                                 # widen until k items pass where, or every positive item is read
            top = self.ranked(score, m)
            out = []
            for i in top:
                it = self.items[i]
                if where is not None and not where(it):
                    continue
                out.append({**thaw(it), "score": float(score[i]), "keyword": None if kw is None else float(kw[i]),
                            "cosine": None if vc is None else float(vc[i])})
                if len(out) >= k:
                    return out
            if len(top) < m:
                return out
            m *= 4


def records(hits: Iterable[Mapping]) -> list[str]:
    """The records the hits name, each once, in the order of its best hit."""
    out, seen = [], set()
    for h in hits:
        r = h.get("record")
        if r is not None and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def seeds(hits: Iterable[Mapping], projection: Any) -> list[str]:
    """The nodes of ``projection`` the hits name, each once, best first: the sources of a traversal that starts where
    a search found its text. A record that is not a node of the projection (an edge, or a record it does not hold) is
    left out."""
    return [r for r in records(hits) if projection.has_node(r)]
