"""Text retrieval: tokens, deterministic int8 vectors, exact rankings that are the head of a full sort, fusions,
filters, saved indexes tied to their items by digests, and hits that name the records a traversal starts from."""

import json
import math
from collections import Counter

import pytest

np = pytest.importorskip("numpy")

from graph_runtime import Projection, execute  # noqa: E402
from graph_runtime.errors import RetrievalError  # noqa: E402
from graph_runtime import retrieval  # noqa: E402
from graph_runtime.retrieval import (RRF_DEPTH, RRF_K, TERMS_MAGIC, HashEmbedder, Index, Tokenizer, quantize,  # noqa: E402
                                     records, seeds, split_text)

TEXTS = ["copper zinc oxide catalyst", "zinc oxide", "copper catalyst", "copper zinc oxide catalyst",
         "sulphur poisons the copper catalyst", "chromium oxide on pumice", "zinc chromite", "methanol from CO2",
         "zinc oxide", "the copper catalyst was reduced", "", "iron carbonyl"]
CHEMISTRY = Tokenizer(aliases={"Cu": "copper", "ZnO": "zinc oxide", "CO": "carbon monoxide", "Co": "cobalt",
                               "CO2": "carbon dioxide"},
                      variants={"sulphur": "sulfur", "aluminium": "aluminum"})


def items_of(texts, repeat=1):
    return [{"id": f"ex/r{j:02d}#0", "record": f"ex/r{j:02d}", "text": t, "source": f"ex/s{j % 3}"}
            for j, t in enumerate(texts * repeat)]


@pytest.fixture(scope="module")
def ix():
    return Index.build(items_of(TEXTS, 3), tokenizer=CHEMISTRY, embedder=HashEmbedder(CHEMISTRY))


# ------------------------------------------------------------------------------------------ tokens and vectors
def test_tokens_add_an_applications_aliases_and_variants():
    t = CHEMISTRY.tokens("Cu-ZnO; CO and Co; sulphur")
    assert {"copper", "zinc", "oxide", "carbon", "monoxide", "cobalt", "sulfur"} <= set(t)
    assert "cobalt" not in CHEMISTRY.tokens("CO") and "carbon" not in CHEMISTRY.tokens("Co")    # case decides
    assert CHEMISTRY.tokens("CO2") == ["co2", "carbon", "dioxide"]       # the longest alias wins


def test_default_words_are_letters_and_digits_of_any_script():
    assert Tokenizer().tokens("Straße 2.5 μm 0,1 東京 x_y") == ["straße", "2.5", "μm", "0,1", "東京", "x", "y"]


def test_a_tokenizers_id_is_its_settings():
    assert Tokenizer().id == Tokenizer().id != CHEMISTRY.id
    assert Tokenizer(variants={"a": "b"}).id != Tokenizer(variants={"a": "c"}).id
    assert Tokenizer(aliases={"Cu": "copper", "Zn": "zinc"}).id == Tokenizer(aliases={"Zn": "zinc", "Cu": "copper"}).id


@pytest.mark.parametrize("kwargs", [{"word": "(a)+"}, {"word": "[a-"}, {"variants": {"Sulphur": "sulfur"}},
                                    {"aliases": {"": "x"}}, {"aliases": {"Cu": 1}}, {"variants": ["a"]}])
def test_a_tokenizer_that_could_not_work_is_refused(kwargs):
    with pytest.raises(RetrievalError) as e:
        Tokenizer(**kwargs)
    assert e.value.code == "invalid_tokenizer"


def test_vectors_are_deterministic_int8(tmp_path, ix):
    again = Index.build(items_of(TEXTS, 3), tokenizer=CHEMISTRY, embedder=HashEmbedder(CHEMISTRY))
    assert ix.vectors.dtype.name == "int8" and ix.vectors.shape == (36, 512)
    assert ix.vectors.tobytes() == again.vectors.tobytes() and ix.index_id == again.index_id
    assert not ix.vectors.flags.writeable
    ix.save(tmp_path / "ix")
    back = Index.load(tmp_path / "ix")
    assert back.vectors.tobytes() == ix.vectors.tobytes() and back.items == ix.items and back.rebuilt == ()
    assert back.index_id == ix.index_id and back.embedder_id == ix.embedder_id == HashEmbedder(CHEMISTRY).id
    assert back.search("sulfur poisoning", k=5) == ix.search("sulfur poisoning", k=5)


def test_hash_features_are_words_their_four_grams_and_word_pairs():
    f = HashEmbedder().features("Zinc oxide zinc")
    assert f["w:zinc"] == 2.0 and f["w:oxide"] == 1.0
    assert f["c:<zin"] == f["c:inc>"] == 1.0 and f["c:<oxi"] == 0.5
    assert f["b:zinc oxide"] == f["b:oxide zinc"] == 1.0
    assert not any(k.startswith("c:") and "cu" in k for k in HashEmbedder().features("cu"))  # "<cu>": no 4-grams


def test_quantized_rows_keep_their_direction():
    m = np.array([[3.0, -4.0, 0.0], [0.0, 0.0, 0.0], [1e-9, 2e-9, 0.0]])
    q = quantize(m)
    assert q.dtype.name == "int8" and q[0].tolist() == [95, -127, 0] and q[1].tolist() == [0, 0, 0]
    assert q[2].tolist() == [64, 127, 0]


def test_huge_finite_vectors_keep_their_direction():
    """The norm of [1e308, 0] overflowed, dividing by infinity zeroed the row,
    and its item dropped out of vector search though it points the same way as [1, 0]."""
    q = quantize(np.array([[1e308, 0.0], [1.0, 0.0], [-1e308, 1e308], [3e-320, 0.0]]))
    assert q.tolist() == [[127, 0], [127, 0], [-127, 127], [127, 0]]

    class Huge:
        id = "finite-vectors"

        def encode(self, texts):
            return np.array([[1e308, 0.0] if t == "huge" else [1.0, 0.0] for t in texts])
    ix = Index.build([{"id": "a", "text": "huge"}, {"id": "b", "text": "unit"}], embedder=Huge())
    assert ix.cosine("query").tolist() == [1.0, 1.0]
    assert [h["id"] for h in ix.search("query", mode="vector")] == ["a", "b"]


# ------------------------------------------------------------------------------------------ exact rankings
def reference(ix, query, k, mode, keep=None):
    """The plain definitions: BM25 and the cosine of every item, a full sort by score and item id, positive scores of
    the kept items only."""
    tf = [Counter(ix.tokenizer.tokens(i["text"])) for i in ix.items]
    n, lens = len(tf), [sum(c.values()) for c in tf]
    avg, kw = sum(lens) / n, np.zeros(n)
    for w in sorted(set(ix.tokenizer.tokens(query))):
        df = sum(1 for c in tf if c.get(w))
        if df:
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, c in enumerate(tf):
                if c.get(w):
                    kw[i] += idf * c[w] * 2.2 / (c[w] + 1.2 * (0.25 + 0.75 * lens[i] / avg))
    m = ix.vectors.astype(np.float64)
    qv = quantize(ix.embedder.encode([query])).astype(np.float64)[0]
    norms = np.linalg.norm(m, axis=1)
    norms[norms == 0] = 1.0
    vc = m @ qv / (norms * (np.linalg.norm(qv) or 1.0))
    kwn = kw / kw.max() if kw.max() > 0 else kw
    score = {"hybrid": 0.5 * kwn + 0.5 * np.clip(vc, 0, None), "vector": vc, "keyword": kw}[mode]
    order = sorted(range(n), key=lambda i: (-score[i], ix.items[i]["id"]))
    return [ix.items[i]["id"] for i in order if score[i] > 0 and (keep is None or keep(ix.items[i]))][:k]


def test_a_search_is_exactly_the_head_of_a_full_sort(ix):
    """The inverted index, the blocked cosine and the partial sort return what the plain definitions return, ties
    (repeated texts) broken by item id, with or without a filter."""
    odd = lambda it: it["source"] != "ex/s1"                                          # noqa: E731
    for q in ("copper zinc oxide", "zinc oxide", "sulfur poisoning", "nothing matches xyzzy", "catalyst", "Cu"):
        for mode in ("hybrid", "vector", "keyword"):
            for k, keep in ((1, None), (5, None), (100, None), (2, odd), (7, odd)):
                got = [h["id"] for h in ix.search(q, k=k, mode=mode, where=keep)]
                assert got == reference(ix, q, k, mode, keep), (q, mode, k)


def test_a_blocked_cosine_is_the_cached_one(monkeypatch, ix):
    kept = ix.cosine("zinc oxide catalyst")
    monkeypatch.setattr(retrieval, "CACHE_BYTES", 0)
    monkeypatch.setattr(retrieval, "BLOCK", 7)
    blocked = Index.build(items_of(TEXTS, 3), tokenizer=CHEMISTRY, embedder=HashEmbedder(CHEMISTRY))
    assert blocked._converted is None and np.array_equal(kept, blocked.cosine("zinc oxide catalyst"))
    assert np.array_equal(blocked.norms, ix.norms)


def test_hits_carry_their_item_and_the_scores_the_mode_computes(ix):
    h = ix.search("copper", k=1, mode="keyword")[0]
    assert set(h) == {"id", "record", "text", "source", "score", "keyword", "cosine"}
    assert h["score"] == h["keyword"] > 0 and h["cosine"] is None
    v = ix.search("copper", k=1, mode="vector")[0]
    assert v["score"] == v["cosine"] and v["keyword"] is None
    both = ix.search("copper", k=1)[0]
    assert both["keyword"] is not None and both["cosine"] is not None
    both["text"] = "changed"
    assert ix.search("copper", k=1)[0]["text"] != "changed"               # a hit is a copy


def test_rrf_fuses_the_keyword_and_vector_rankings(ix):
    q = "copper zinc oxide"
    hits = ix.search(q, k=50, mode="rrf")
    kw = {ix.items[i]["id"]: r for r, i in enumerate(ix.ranked(ix.bm25(q), 1000), 1)}
    vc = {ix.items[i]["id"]: r for r, i in enumerate(ix.ranked(ix.cosine(q), 1000), 1)}
    assert {h["id"] for h in hits} == set(kw) | set(vc)
    for h in hits:
        assert h["score"] == pytest.approx(sum(1 / (RRF_K + r[h["id"]]) for r in (kw, vc) if h["id"] in r), abs=0)
        assert h["score"] <= 2 / (RRF_K + 1)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    assert hits == ix.search(q, k=50, mode="rrf")


def test_rrf_with_a_filter_ranks_only_the_items_it_keeps():
    """With a filter, rrf fuses the rankings of the items the filter keeps: an eligible match past the fusion depth
    of all items is still found, as keyword search finds it."""
    items = [{"id": f"ex/r{j:05d}#0", "record": f"ex/r{j:05d}", "text": "copper catalyst"} for j in range(RRF_DEPTH + 1)]
    ix = Index.build(items)
    last = items[-1]["id"]
    only = lambda it: it["id"] == last                                              # noqa: E731
    assert [h["id"] for h in ix.search("copper", k=5, mode="keyword", where=only)] == [last]
    for kept in ({"where": only}, {"records": [items[-1]["record"]]}):
        hits = ix.search("copper", k=5, mode="rrf", **kept)
        assert [h["id"] for h in hits] == [last] and hits[0]["score"] == 2 / 61


def test_a_records_filter_keeps_what_the_same_where_keeps(ix):
    chosen = {"ex/r03", "ex/r09", "ex/r15", "ex/r21", "ex/r27", "nowhere"}
    for mode in ("hybrid", "vector", "keyword", "rrf"):
        for k in (1, 3, 40):
            by_records = ix.search("copper zinc catalyst", k=k, mode=mode, records=chosen)
            by_where = ix.search("copper zinc catalyst", k=k, mode=mode, where=lambda it: it["record"] in chosen)
            assert by_records == by_where and {h["record"] for h in by_records} <= chosen
    assert ix.search("copper", records=[]) == []
    with pytest.raises(RetrievalError, match="not one id"):
        ix.search("copper", records="ex/r03")


@pytest.mark.parametrize("k", [0, -1, True, 2.5, None])
def test_a_search_asks_for_at_least_one_hit(ix, k):
    with pytest.raises(RetrievalError, match="at least 1") as e:
        ix.search("copper", k=k, mode="keyword")
    assert e.value.code == "invalid_k"


def test_a_search_that_matches_nothing_has_no_hits(ix):
    assert ix.search("xyzzy nothing", k=1, mode="keyword") == [] and ix.search("xyzzy", k=3, mode="rrf") == []
    assert len(ix.ranked(ix.bm25("copper"), 0)) == 0
    assert Index.build([]).search("copper") == [] and len(Index.build([])) == 0


def test_bad_queries_and_modes_are_refused(ix):
    for kwargs, code in (({"mode": "fuzzy"}, "invalid_mode"), ({"where": "x"}, "invalid_where")):
        with pytest.raises(RetrievalError) as e:
            ix.search("copper", **kwargs)
        assert e.value.code == code
    for q in (None, b"copper", "\ud800"):
        with pytest.raises(RetrievalError) as e:
            ix.search(q)
        assert e.value.code == "invalid_query"


# ------------------------------------------------------------------------------------------ items and embedders
@pytest.mark.parametrize("bad, code", [
    ([{"id": "a", "text": "x"}, {"id": "a", "text": "y"}], "duplicate_item"),
    ([{"id": "a"}], "invalid_items"),
    ([{"id": 1, "text": "x"}], "invalid_items"),
    ([{"id": "a", "text": "x", "record": 3}], "invalid_items"),
    ([{"id": "a", "text": "x", "score": 1}], "invalid_items"),
    ([{"id": "a", "text": "x", "when": float("nan")}], "invalid_items"),
    ([{"id": "a", "text": "\ud800"}], "invalid_items"),
    ({"id": "a", "text": "x"}, "invalid_items"),
])
def test_items_that_are_not_valid_are_refused(bad, code):
    with pytest.raises(RetrievalError) as e:
        Index.build(bad)
    assert e.value.code == code


class Constant:
    """A stand-in for a neural embedder: an id and encode."""

    def __init__(self, ident="constant-v1", dim=4):
        self.id, self.dim = ident, dim

    def encode(self, texts):
        return np.array([[1.0 + len(t) % 3, 1.0, 0.5, 0.0][:self.dim] for t in texts])


def test_another_embedder_is_named_and_never_mixed(tmp_path):
    ix = Index.build(items_of(TEXTS), embedder=Constant())
    assert ix.embedder_id == "constant-v1" and ix.tokenizer.id == Tokenizer().id and ix.dim == 4
    assert ix.search("zinc", k=2, mode="vector")
    with pytest.raises(RetrievalError) as e:
        ix.search("zinc", mode="vector", embedder=Constant("other-v1"))
    assert e.value.code == "embedder_mismatch"
    ix.save(tmp_path / "ix")
    bare = Index.load(tmp_path / "ix")                       # vectors as saved; queries need the embedder
    assert bare.search("zinc", k=3, mode="keyword") == ix.search("zinc", k=3, mode="keyword")
    with pytest.raises(RetrievalError) as e:
        bare.search("zinc", mode="vector")
    assert e.value.code == "needs_embedder"
    assert Index.load(tmp_path / "ix", embedder=Constant()).search("zinc", k=3) == ix.search("zinc", k=3)
    assert bare.search("zinc", k=3, embedder=Constant()) == ix.search("zinc", k=3)
    with pytest.raises(RetrievalError) as e:
        Index.load(tmp_path / "ix", embedder=Constant("other-v1"))
    assert e.value.code == "embedder_mismatch"
    for bad in (object(), type("NoId", (), {"encode": lambda self, t: None})()):
        with pytest.raises(RetrievalError) as e:
            Index.build(items_of(TEXTS), embedder=bad)
        assert e.value.code == "invalid_embedder"


def test_an_embedder_that_returns_no_vectors_is_refused():
    class Broken(Constant):
        def encode(self, texts):
            return np.full((len(texts), 4), np.nan)
    with pytest.raises(RetrievalError) as e:
        Index.build(items_of(TEXTS), embedder=Broken())
    assert e.value.code == "invalid_embedding"


def test_the_index_id_names_items_embedder_and_tokenizer():
    base = Index.build(items_of(TEXTS))
    assert Index.build(items_of(TEXTS)).index_id == base.index_id
    changed = items_of(TEXTS)
    changed[0]["text"] = "copper zinc oxide catalysts"
    assert Index.build(changed).index_id != base.index_id
    assert Index.build(items_of(TEXTS), tokenizer=CHEMISTRY).index_id != base.index_id
    assert Index.build(items_of(TEXTS), embedder=HashEmbedder(dim=256)).index_id != base.index_id
    assert len({Index.build(items_of(TEXTS)[::-1]).index_id, base.index_id}) == 2      # the order is the items'


# ------------------------------------------------------------------------------------------ saved indexes
def saved(tmp_path, texts=TEXTS):
    a = Index.build(items_of(texts), tokenizer=CHEMISTRY, embedder=HashEmbedder(CHEMISTRY))
    a.save(tmp_path / "ix")
    return a


def test_saved_term_statistics_are_used_only_with_their_items(tmp_path):
    a = saved(tmp_path)
    b = Index.load(tmp_path / "ix")
    assert b.rebuilt == () and b.vocab == a.vocab and all(np.array_equal(x, y) for x, y in (
        (a.indptr, b.indptr), (a.post_item, b.post_item), (a.post_count, b.post_count), (a.lengths, b.lengths)))
    j = tmp_path / "ix.jsonl"                      # the items change; the saved vectors and statistics do not
    j.write_text(j.read_text().replace("zinc chromite", "iron oxide sludge"))
    c = Index.load(tmp_path / "ix")
    assert c.rebuilt == ("vectors", "terms") and "sludge" in c.vocab and "chromite" not in c.vocab
    assert c.search("sludge", k=1, mode="vector")[0]["text"] == "iron oxide sludge"
    fresh = items_of(TEXTS)
    fresh[6]["text"] = "iron oxide sludge"
    assert c.index_id == Index.build(fresh, tokenizer=CHEMISTRY, embedder=HashEmbedder(CHEMISTRY)).index_id
    (tmp_path / "ix.terms").unlink()
    assert Index.load(tmp_path / "ix").rebuilt == ("vectors", "terms")


def test_saved_term_statistics_that_were_altered_are_rebuilt(tmp_path):
    """The arrays of a saved .terms file carry their digest: two postings swapped (the file's size and header
    unchanged) are not read; the statistics are rebuilt from the items, and the search answers as before."""
    a = saved(tmp_path)
    expect = a.search("copper", k=5, mode="keyword")
    data = bytearray((tmp_path / "ix.terms").read_bytes())
    at = len(TERMS_MAGIC)
    at += 8 + int.from_bytes(data[at:at + 8], "little") + 8 * (len(a.vocab) + 1)     # the postings' rows
    rows = np.frombuffer(bytes(data[at:at + 4 * len(a.post_item)]), dtype=np.int32).copy()
    i, j = next((i, j) for i in range(len(rows)) for j in range(len(rows)) if rows[i] != rows[j])
    rows[i], rows[j] = rows[j], rows[i]
    data[at:at + 4 * len(rows)] = rows.tobytes()
    (tmp_path / "ix.terms").write_bytes(bytes(data))
    b = Index.load(tmp_path / "ix")
    assert b.rebuilt == ("terms",) and np.array_equal(b.post_item, a.post_item)
    assert b.search("copper", k=5, mode="keyword") == expect


def test_saved_term_statistics_whose_words_were_altered_are_rebuilt(tmp_path):
    a = saved(tmp_path)
    expect = a.search("copper", k=5, mode="keyword")
    data = (tmp_path / "ix.terms").read_bytes()
    at = len(TERMS_MAGIC)
    size = int.from_bytes(data[at:at + 8], "little")
    head = json.loads(data[at + 8:at + 8 + size])
    head["vocab"][head["vocab"].index("copper")] = "coppes"
    assert head["vocab"] == sorted(set(head["vocab"]))
    new = json.dumps(head, ensure_ascii=False, sort_keys=True).encode()
    (tmp_path / "ix.terms").write_bytes(TERMS_MAGIC + len(new).to_bytes(8, "little") + new + data[at + 8 + size:])
    b = Index.load(tmp_path / "ix")
    assert b.rebuilt == ("terms",) and "copper" in b.vocab and b.search("copper", k=5, mode="keyword") == expect


def test_term_statistics_saved_with_another_tokenizer_are_rebuilt(tmp_path):
    saved(tmp_path)
    other = Index.build(items_of(TEXTS))                  # the same items, the default tokenizer
    other.save(tmp_path / "other")
    (tmp_path / "ix.terms").write_bytes((tmp_path / "other.terms").read_bytes())
    b = Index.load(tmp_path / "ix")
    assert b.rebuilt == ("terms",) and "sulfur" in b.vocab


def test_vectors_that_are_not_the_ones_saved_with_the_items_are_rebuilt(tmp_path):
    """Two rows swapped (the file's size unchanged) are not read, so a search for copper never returns another
    quote with cosine 1; the vectors are rebuilt from the items."""
    a = saved(tmp_path, ["copper catalyst", "banana bread", "zinc oxide"])
    vec = np.frombuffer((tmp_path / "ix.i8").read_bytes(), dtype=np.int8).reshape(3, -1).copy()
    vec[[0, 1]] = vec[[1, 0]]
    (tmp_path / "ix.i8").write_bytes(vec.tobytes())
    b = Index.load(tmp_path / "ix")
    assert b.rebuilt == ("vectors",) and np.array_equal(b.vectors, a.vectors)
    assert b.search("copper", k=1, mode="vector")[0]["text"] == "copper catalyst"


def test_vectors_that_cannot_be_rebuilt_are_refused(tmp_path):
    Index.build(items_of(TEXTS), embedder=Constant()).save(tmp_path / "ix")
    (tmp_path / "ix.i8").write_bytes(b"\0" * len((tmp_path / "ix.i8").read_bytes()))
    with pytest.raises(RetrievalError) as e:
        Index.load(tmp_path / "ix")
    assert e.value.code == "stale_vectors"
    assert Index.load(tmp_path / "ix", embedder=Constant()).rebuilt == ("vectors",)


@pytest.mark.parametrize("damage", ["", "{}\n", "not json\n", '{"kind": "graph_runtime_index", "version": 2}\n',
                                    '{"kind": "graph_runtime_index", "version": 1}\n'])
def test_a_file_that_is_not_a_saved_index_is_refused(tmp_path, damage):
    (tmp_path / "ix.jsonl").write_text(damage)
    with pytest.raises(RetrievalError) as e:
        Index.load(tmp_path / "ix")
    assert e.value.code == "unreadable_index"


# ------------------------------------------------------------------------------------------ search, then traverse
def test_hits_name_the_nodes_a_traversal_starts_from(ix):
    nodes = [f"ex/r{j:02d}" for j in range(12)] + ["ex/claim"]
    edges = [{"id": f"d{j}", "source": "ex/claim", "target": f"ex/r{j:02d}", "relation": "CITES"} for j in range(12)]
    p = Projection.build(nodes=nodes, edges=edges)
    hits = ix.search("sulphur poisoning of the copper catalyst", k=8)
    assert records(hits) == list(dict.fromkeys(h["record"] for h in hits))
    starts = seeds(hits, p)
    assert starts and set(starts) <= set(nodes) and starts == [r for r in records(hits) if r in nodes]
    assert seeds(hits, Projection.build(nodes=["elsewhere"], edges=[])) == []
    res = execute(p, p.request("dependency_impact", changed=starts[:1], scope=[("CITES", "reverse")]))
    assert [a["node"] for a in res.answer["affected"]] == ["ex/claim"]
    # a traversal's records restrict a search in turn
    cited = [e["target"] for e in edges[:4]]
    assert {h["record"] for h in ix.search("copper", k=20, records=cited)} <= set(cited)


def test_an_index_id_bound_into_a_projection_makes_its_results_stale_when_the_index_changes(ix):
    p = Projection.build(nodes=["a"], edges=[], bindings={"evidence_index": ix.index_id})
    assert p.stale_bindings({"evidence_index": ix.index_id}) == ()
    other = Index.build(items_of(TEXTS))
    assert p.stale_bindings({"evidence_index": other.index_id}) == ("evidence_index",)


# ------------------------------------------------------------------------------------------ passages
def test_passages_join_paragraphs_and_cut_long_ones_at_sentence_ends():
    text = "First paragraph about copper.\n\nSecond paragraph about zinc oxide."
    assert split_text(text) == [(0, len(text))]
    assert [text[a:b] for a, b in split_text(text, limit=40)] == ["First paragraph about copper.",
                                                                  "Second paragraph about zinc oxide."]
    long = "A " + "long sentence. " * 100
    spans = split_text(long)
    assert len(spans) >= 2 and all(b - a <= 900 for a, b in spans)
    assert all(long[b - 1] == "." for a, b in spans[:-1])                 # cut at a sentence end
    assert split_text("") == [] and split_text("   \n\n  ") == []
    unbroken = "x" * 2000
    assert split_text(unbroken) == [(0, 900), (900, 1800), (1800, 2000)]
    with pytest.raises(RetrievalError):
        split_text(text, limit=2)
