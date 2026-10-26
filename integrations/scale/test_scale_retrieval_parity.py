"""Parity with SCALE's evidence search (scale.passages) on SCALE's public methanol-synthesis example: 55 public-domain
sources, their quotes and their page texts.

GRAPH's index, built with SCALE's words (``adapter.tokenizer``), must hold SCALE's vectors byte for byte and its
inverted index, compute the same BM25 and cosine scores bit for bit, and return the same hits in the same order for
every query, mode, k and filter tried. SCALE's text stages (the manuscript's questions that rank claims by their
quotes) must rank the same claims with the same scores, and the page texts must split into the same passages.
Everything is read; nothing in SCALE is written.
"""

from __future__ import annotations

import json
import random
from collections import Counter

import pytest

import scale_graph_adapter as adapter

np = pytest.importorskip("numpy")

MODES = ("hybrid", "vector", "keyword", "rrf")
FIXED_QUERIES = ["sulphur poisons the copper catalyst", "copper zinc oxide", "zinc oxide", "sulfur poisoning",
                 "catalyst", "Cu", "CO2 hydrogenation", "methanol synthesis at 300 atm", "Cu/ZnO/Al2O3",
                 "Kupfer Zinkoxyd", "250° C.", "", "xyzzy nothing", "CO and Co", "iron carbonyl poison"]


@pytest.fixture(scope="module")
def public(scale_repo):
    from scale import layout, pgraph
    from scale.passages import Index, evidence_items
    from scale.util import load_doc
    folder = scale_repo / "examples" / "methanol-synthesis-public"
    doc = load_doc(folder)
    manifest = layout.graph_manifests(scale_repo, experimental=True)["catalysis"]
    spec = pgraph.load_spec(pgraph.pinned(manifest, doc["graph"].get("schema_version"), doc))
    items = evidence_items(doc)
    return {"folder": folder, "doc": doc, "spec": spec, "items": items, "scale": Index.build(items),
            "graph": adapter.evidence_index(doc)}


def queries(items: list[dict], count: int = 40) -> list[str]:
    """The fixed queries, and word runs cut from quotes chosen with a fixed seed (queries the corpus can answer)."""
    rng = random.Random(1966)
    out = list(FIXED_QUERIES)
    for it in rng.sample(items, count):
        words = it["text"].split()
        if words:
            at = rng.randrange(len(words))
            out.append(" ".join(words[at:at + rng.randint(2, 7)]))
    return out


def same_index(scale_ix, graph_ix):
    assert graph_ix.vectors.tobytes() == scale_ix.vectors.tobytes()
    assert graph_ix.vocab == scale_ix.vocab
    for name in ("indptr", "post_item", "post_count", "lengths", "norms"):
        assert np.array_equal(getattr(graph_ix, name), getattr(scale_ix, name)), name
    assert graph_ix.avg == scale_ix.avg


def test_the_same_vectors_and_inverted_index(public):
    assert len(public["items"]) > 9000
    same_index(public["scale"], public["graph"])
    assert [dict(it) for it in public["graph"].items] == public["items"]


def test_the_same_scores_bit_for_bit(public):
    s, g = public["scale"], public["graph"]
    for q in queries(public["items"]):
        assert np.array_equal(g.bm25(q), s.bm25(q)), q
        assert np.array_equal(g.cosine(q), s.cosine(q)), q


def test_the_same_hits_in_every_mode_and_with_filters(public):
    s, g, doc = public["scale"], public["graph"], public["doc"]
    years = {n["id"]: n["props"].get("year") for n in doc["nodes"] if n["label"] == "Source"}
    before = lambda it: years.get(it.get("source")) is not None and years[it["source"]] < 1928    # noqa: E731
    claims = sorted(n["id"] for n in doc["nodes"] if n["label"] == "Claim")
    rng = random.Random(7)
    some_claims = set(rng.sample(claims, len(claims) // 3))
    claim_quotes = lambda it: it["record"] in some_claims                                        # noqa: E731
    tally: Counter = Counter()
    for q in queries(public["items"]):
        for mode in MODES:
            for k in (1, 10, 200):
                for where, records in ((None, None), (before, None), (claim_quotes, some_claims)):
                    want = s.search(q, k=k, mode=mode, where=where)
                    assert adapter.search(g, q, k=k, mode=mode, where=where) == want, (q, mode, k)
                    if records is not None:            # SCALE's filter as GRAPH's declarative one: the same hits
                        assert adapter.search(g, q, k=k, mode=mode, records=records) == want, (q, mode, k)
                    tally["searches"] += 1
                    tally["hits"] += len(want)
    assert tally["searches"] >= 1500 and tally["hits"] >= 50000
    print(f"\nSCALE search parity: {dict(tally)}")


def test_the_same_claims_ranked_by_a_plans_text_stage(public):
    """The manuscript's questions that rank claims by their quotes (match Claim, a text stage, published_before): SCALE's
    rows before the text stage, ranked by GRAPH, are SCALE's answer rows with the same text scores."""
    from scale import pgraph_query
    from scale.passages import graph_sha256
    from scale.util import load_yaml
    doc, spec = public["doc"], public["spec"]
    native = public["scale"]
    native.graph_sha256 = graph_sha256(doc)
    checked = 0
    for path in sorted((public["folder"] / "queries").glob("*.yaml")):
        plan = load_yaml(path)
        if not plan.get("text"):
            continue
        root = plan["match"]["as"]
        answer = pgraph_query.run(doc, spec, plan, native)
        before_text = {k: v for k, v in plan.items() if k != "text"}
        before_text["limit"] = 10 ** 9
        rows = pgraph_query.run(doc, spec, before_text, None)["rows"]
        ranked = adapter.text_ranked(public["graph"], [r["bound"][root] for r in rows], plan["text"])
        want = [(r["bound"][root], r["text_score"]) for r in answer["rows"]]
        assert ranked[:plan.get("limit", 200)] == want, path.name
        assert len(want) > 0
        checked += 1
    assert checked == 3


def test_page_texts_split_into_the_same_passages_and_search_alike(public):
    from graph_runtime.retrieval import split_text
    from scale.passages import Index, passages_of, split_page
    passages, pages = [], 0
    for path in sorted((public["folder"] / "texts").glob("*.json")):
        text = json.loads(path.read_text(encoding="utf-8"))
        for page in text["pages"]:
            assert split_text(page["text"]) == split_page(page["text"]), (path.name, page["page"])
            pages += 1
        passages += passages_of(text["slug"], text["slug"], text["pages"])
    assert pages >= 230 and len(passages) >= 1000, (pages, len(passages))
    s = Index.build(passages)
    from graph_runtime.retrieval import HashEmbedder, Index as GraphIndex
    words = adapter.tokenizer()
    g = GraphIndex.build(passages, tokenizer=words, embedder=HashEmbedder(words))
    same_index(s, g)
    for q in queries(passages, 15):
        for mode in MODES:
            assert adapter.search(g, q, k=20, mode=mode) == s.search(q, k=20, mode=mode), (q, mode)
    print(f"\npassages: {pages} pages, {len(passages)} passages")
