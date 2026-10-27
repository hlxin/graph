"""Certified personalized PageRank: every listed node comes with an interval proven to hold its PageRank, the top of
the ranking is certified where intervals separate, and the certificate (the estimates) is checked by recomputing the
residuals exactly. The oracle solves the PageRank linear system exactly, with fractions, on small random graphs."""

import random
from fractions import Fraction

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.revalidate import diff, revalidate
from oracle import RELATIONS, TYPES, hops_of, node_id, random_graph, random_scope
from test_revalidate import edited


def exact_pagerank(nodes, edges, sources, scope, alpha, relation_parents=None):
    """pi solving pi = alpha s + (1 - alpha) P^T pi, by Gaussian elimination over fractions: P moves uniformly over
    a node's hops in scope (each parallel edge counts), a node with none ends the walk, s is uniform over the
    sources."""
    from oracle import expand_scope
    ids = [node_id(n) for n in nodes]
    scope = expand_scope(scope, relation_parents or {}, set(RELATIONS) | {e["relation"] for e in edges})
    out = {u: [] for u in ids}
    for u, v, _ in hops_of(edges, scope):
        out[u].append(v)
    index = {u: i for i, u in enumerate(ids)}
    n = len(ids)
    m = [[Fraction(int(i == j)) for j in range(n)] + [Fraction(0)] for i in range(n)]
    for u in ids:
        for v in out[u]:
            m[index[v]][index[u]] -= (1 - alpha) / len(out[u])
    for s in sources:
        m[index[s]][n] += alpha / len(sources)
    for c in range(n):                                    # the matrix is diagonally dominant: no pivoting needed
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / m[c][c]
                m[r] = [a - f * b for a, b in zip(m[r], m[c])]
    return {u: m[index[u]][n] / m[index[u]][index[u]] for u in ids}


def exact(entry, field):
    return Fraction(entry[f"{field}_exact"])


def cases(rng, n, **graph):
    for _ in range(n):
        nodes, edges = random_graph(rng, **graph)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(x) for x in nodes]
        raw = {"operation": "personalized_pagerank", "sources": rng.sample(ids, rng.randint(1, min(3, len(ids)))),
               "scope": random_scope(rng), "alpha": rng.choice(["3/20", "1/2", 0.25, "1/10"]),
               "tolerance": rng.choice(["1/100", "1/10000", 1e-6]), "top": rng.randint(1, 8),
               "certificate": rng.random() < 0.7}
        yield nodes, edges, p, raw


def test_every_interval_holds_the_exact_pagerank():
    rng = random.Random("pagerank")
    certified = 0
    for nodes, edges, p, raw in cases(rng, 500):
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        alpha = Fraction(res.request["alpha"])
        pi = exact_pagerank(nodes, edges, raw["sources"], raw["scope"], alpha)
        listed = {e["node"]: (exact(e, "lower"), exact(e, "upper")) for e in res.answer["ranked"]}
        for v, (lo, hi) in listed.items():
            assert lo <= pi[v] <= hi, (raw, v)
        residual = exact(res.answer, "residual")
        assert residual <= Fraction(res.request["tolerance"])
        for v in pi:                                       # nodes outside the list: at most the best unlisted upper
            if v not in listed:
                assert pi[v] <= max([hi for lo, hi in listed.values()] + [residual])
        m = res.answer["certified_top"]
        if m:
            certified += 1
            best = sorted(pi, key=lambda v: -pi[v])
            top = {e["node"] for e in res.answer["ranked"][:m]}
            assert all(pi[v] > pi[w] for v in top for w in pi if w not in top), raw
    assert certified >= 200


def test_an_interrupted_push_still_proves_its_intervals():
    rng = random.Random("pagerank-limits")
    stopped = 0
    for nodes, edges, p, raw in cases(rng, 400, max_nodes=10, max_edges=30):
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 6)}})
        assert verify_result(res, nodes=nodes, edges=edges) == []
        if res.status != INTERRUPTED:
            continue
        stopped += 1
        pi = exact_pagerank(nodes, edges, raw["sources"], raw["scope"], Fraction(res.request["alpha"]))
        for e in res.answer["ranked"]:
            assert exact(e, "lower") <= pi[e["node"]] <= exact(e, "upper")
    assert stopped >= 100


def forged(res, change):
    from test_certificates import reseal
    data = res.to_dict()
    change(data)
    return reseal(data)


def test_a_forged_estimate_is_refused():
    p = Projection.build(nodes=["a", "b", "c"], edges=[{"id": "ab", "source": "a", "target": "b", "relation": "R"},
                                                       {"id": "bc", "source": "b", "target": "c", "relation": "R"},
                                                       {"id": "ca", "source": "c", "target": "a", "relation": "R"}])
    res = execute(p, p.request("personalized_pagerank", sources=["a"], scope=[("R", "forward")], certificate=True))
    records = p.to_dict()
    assert verify_result(res, nodes=records["nodes"], edges=records["edges"]) == []

    def inflate(data):
        e = data["answer"]["certificate"]["values"][1]
        e["value_exact"] = str(Fraction(e["value_exact"]) * 2)
        e["value"] = float(Fraction(e["value_exact"]))
    assert verify_result(forged(res, inflate), nodes=records["nodes"], edges=records["edges"])

    def overclaim(data):
        e = data["answer"]["ranked"][0]
        e["lower_exact"] = e["upper_exact"]
        e["lower"] = e["upper"]
    assert verify_result(forged(res, overclaim), nodes=records["nodes"], edges=records["edges"])


def test_sources_that_do_not_divide_the_units_share_the_mass_exactly():
    """Three sources: 2**64 units do not split three ways, so the push counts in thirds of them; handing the first
    source the remainder would prove it a lower bound above its PageRank of 1/6."""
    p = Projection.build(nodes=["a", "b", "c"], edges=[], relations=["R"])
    res = execute(p, p.request("personalized_pagerank", sources=["a", "b", "c"], scope=[("R", "forward")],
                               alpha="1/2", certificate=True))
    records = p.to_dict()
    assert verify_result(res, nodes=records["nodes"], edges=records["edges"]) == []
    assert [(e["node"], exact(e, "lower"), exact(e, "upper")) for e in res.answer["ranked"]] == [
        (n, Fraction(1, 6), Fraction(1, 6)) for n in ["a", "b", "c"]]
    assert exact(res.answer, "residual") == 0


def test_revalidation_refuses_a_residual_the_edit_made_false():
    """s to t, pushed once: residual 1/2 waits at t. Without the hop, s's estimate leaves no residual at all, so the
    answer's exact residual and upper bounds are no longer the ones its certificate proves."""
    nodes = ["s", "t"]
    p = Projection.build(nodes=nodes, edges=[{"id": "st", "source": "s", "target": "t", "relation": "R"}])
    old = execute(p, p.request("personalized_pagerank", sources=["s"], scope=[("R", "forward")], alpha="1/2",
                               tolerance="3/4", certificate=True))
    assert exact(old.answer, "residual") == Fraction(1, 2)
    q = Projection.build(nodes=nodes, edges=[], relations=["R"])
    assert verify_result(old, nodes=nodes, edges=[])
    check = revalidate(old, q, diff=diff(p, q))
    assert check.verdict == "unknown" and check.reasons[0]["code"] == "certificate_broken"


def test_a_ranking_around_a_claim():
    nodes = ["claim", "m1", "m2", "d1", "s1", "other"]
    edges = [{"id": "e1", "source": "claim", "target": "m1", "relation": "SUPPORTED_BY"},
             {"id": "e2", "source": "claim", "target": "m2", "relation": "SUPPORTED_BY"},
             {"id": "e3", "source": "m1", "target": "d1", "relation": "IN"},
             {"id": "e4", "source": "m2", "target": "d1", "relation": "IN"},
             {"id": "e5", "source": "d1", "target": "s1", "relation": "FROM"},
             {"id": "e6", "source": "other", "target": "s1", "relation": "FROM"}]
    p = Projection.build(nodes=nodes, edges=edges)
    scope = [("SUPPORTED_BY", "forward"), ("IN", "forward"), ("FROM", "forward")]
    res = execute(p, p.request("personalized_pagerank", sources=["claim"], scope=scope, alpha="1/2",
                               tolerance="1/1000000", top=6, certificate=True))
    pi = exact_pagerank(nodes, edges, ["claim"], scope, Fraction(1, 2))
    assert pi == {"claim": Fraction(1, 2), "m1": Fraction(1, 8), "m2": Fraction(1, 8), "d1": Fraction(1, 8),
                  "s1": Fraction(1, 16), "other": 0}
    assert [e["node"] for e in res.answer["ranked"]][:2] == ["claim", "d1"] or \
        [e["node"] for e in res.answer["ranked"]][0] == "claim"
    assert res.answer["certified_top"] >= 1 and "other" not in [e["node"] for e in res.answer["ranked"]]


def test_revalidation_keeps_a_ranking_while_its_proof_stands():
    rng = random.Random("pagerank-revalidate")
    tally = {}
    for nodes, edges, p, raw in cases(rng, 300, max_nodes=8, max_edges=16):
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES)
        old = execute(p, {**raw, "projection": p.identity})
        verdict = revalidate(old, q, diff=diff(p, q)).verdict
        if verdict == "holds":
            assert verify_result(old, nodes=nodes2, edges=edges2) == []
            pi = exact_pagerank(nodes2, edges2, raw["sources"], raw["scope"], Fraction(old.request["alpha"]))
            for e in old.answer["ranked"]:
                assert exact(e, "lower") <= pi[e["node"]] <= exact(e, "upper")
        tally[verdict] = tally.get(verdict, 0) + 1
    assert tally.get("holds", 0) >= 30 and tally.get("unknown", 0) + tally.get("fails", 0) >= 30, tally


@pytest.mark.parametrize("alpha", [0, 1, "1/0", "x", -0.5, True, 1.5])
def test_alpha_is_a_probability_strictly_between_0_and_1(alpha):
    p = Projection.build(nodes=["a"], edges=[], relations=["R"])
    res = execute(p, {"operation": "personalized_pagerank", "projection": p.identity, "sources": ["a"],
                      "scope": [("R", "forward")], "alpha": alpha})
    assert res.status == "failed" and res.error["code"] == "invalid_request"


def test_a_listed_node_outranked_by_an_unlisted_one_is_refused():
    """a -> b with alpha 3/4 and top 1 (a deep Codex review's case): a's PageRank is 3/4 and b's 3/16. Listing b
    instead of a, with its true interval and nothing certified, passed every check: the intervals hold, but b is
    not the top node by lower bound."""
    import copy
    from graph_runtime._canonical import thaw
    from test_certificates import reseal
    nodes, edges = ["a", "b"], [{"id": "e", "source": "a", "target": "b", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("personalized_pagerank", sources=["a"], scope=[("R", "forward")], alpha="3/4", top=1,
                               certificate=True))
    assert [e["node"] for e in res.answer["ranked"]] == ["a"] and verify_result(res, nodes=nodes, edges=edges) == []
    data = copy.deepcopy(thaw(res.to_dict()))
    data["answer"]["ranked"] = [{"node": "b", "lower": 0.1875, "upper": 0.1875, "lower_exact": "3/16",
                                 "upper_exact": "3/16"}]
    data["answer"]["certified_top"] = 0
    problems = verify_result(reseal(data), nodes=nodes, edges=edges)
    assert any("outranks a ranked node" in x for x in problems), problems
    data = copy.deepcopy(thaw(res.to_dict()))
    data["request"] = {**data["request"], "top": 2}
    assert verify_result(reseal(data), nodes=nodes, edges=edges)          # b fits within top 2 but is left out
