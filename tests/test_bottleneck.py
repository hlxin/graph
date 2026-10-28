"""Bottleneck paths: the path whose weakest edge is strongest (max_min, as the fastest rate-limiting step of a
reaction pathway) or whose highest edge is lowest (min_max, as the lowest highest barrier), with the fewest hops among
those, certified by a threshold closure. Checked against a threshold-sweep oracle, under limits, against forged and
outdated certificates, and through revalidation."""

import random

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime._canonical import digest, thaw
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from oracle import RELATIONS, TYPES, bottleneck, node_id, random_graph, random_scope

NODES = ["A", "I1", "I2", "I3", "P", "X"]
EDGES = [{"id": "r1", "source": "A", "target": "I1", "relation": "STEP"},
         {"id": "r2", "source": "I1", "target": "P", "relation": "STEP"},
         {"id": "r3", "source": "A", "target": "I2", "relation": "STEP"},
         {"id": "r4", "source": "I2", "target": "I3", "relation": "STEP"},
         {"id": "r5", "source": "I3", "target": "P", "relation": "STEP"},
         {"id": "r6", "source": "A", "target": "X", "relation": "STEP"}]
RATES = {"by_edge": {"r1": 10, "r2": 2, "r3": 5, "r4": 3, "r5": 8, "r6": 100}}


def run(p, nodes, edges, **params):
    res = execute(p, p.request("bottleneck_path", **params))
    assert verify_result(res, nodes=nodes, edges=edges) == []
    assert Result.from_dict(res.to_dict()).result_id == res.result_id
    return res


def test_the_fastest_rate_limiting_step_and_the_lowest_highest_barrier():
    p = Projection.build(nodes=NODES, edges=EDGES)
    fast = run(p, NODES, EDGES, sources=["A"], targets=["P"], scope=[("STEP", "forward")], weights=RATES,
               certificate=True)
    assert fast.answer["value"] == 3 and [h["edge"] for h in fast.witness] == ["r3", "r4", "r5"]
    cert = fast.answer["certificate"]
    assert (cert["kind"], cert["threshold"], cert["reached"]) == ("threshold", 3, ("A", "I1", "I2", "X"))
    # every hop but r2 (2) is at least 3: A, then I1, I2 and X, then I3, and P at 3 hops
    assert [tuple(e.values()) for e in cert["layers"]] == [("A", 0), ("I1", 1), ("I2", 1), ("I3", 2), ("X", 1)]
    low = run(p, NODES, EDGES, sources=["A"], targets=["P"], scope=[("STEP", "forward")], weights=RATES,
              objective="min_max", certificate=True)
    assert low.answer["value"] == 8 and [h["edge"] for h in low.witness] == ["r3", "r4", "r5"]
    # at most 8: r2, r3, r4 and r5; A reaches only I2, then I3, then P
    assert [tuple(e.values()) for e in low.answer["certificate"]["layers"]] == [("A", 0), ("I2", 1), ("I3", 2)]
    none = run(p, NODES, EDGES, sources=["P"], targets=["A"], scope=[("STEP", "forward")], weights=RATES,
               certificate=True)
    assert none.found is False and dict(none.answer["certificate"]) == {"kind": "closure", "reached": ("P",)}
    here = run(p, NODES, EDGES, sources=["A"], targets=["A", "P"], scope=[("STEP", "forward")], weights=RATES,
               certificate=True)
    assert here.answer["value"] is None and here.answer["witness"]["length"] == 0 and here.answer["certificate"] is None


def test_the_fewest_hops_among_the_best_paths():
    nodes = ["s", "a", "b", "c", "t"]
    edges = [{"id": "e1", "source": "s", "target": "a", "relation": "R"},
             {"id": "e2", "source": "a", "target": "b", "relation": "R"},
             {"id": "e3", "source": "b", "target": "t", "relation": "R"},
             {"id": "e4", "source": "s", "target": "c", "relation": "R"},
             {"id": "e5", "source": "c", "target": "t", "relation": "R"}]
    w = {"by_edge": {"e1": 9, "e2": 9, "e3": 5, "e4": 5, "e5": 7}}      # both paths have bottleneck 5
    p = Projection.build(nodes=nodes, edges=edges)
    res = run(p, nodes, edges, sources=["s"], targets=["t"], scope=[("R", "forward")], weights=w)
    assert res.answer["value"] == 5 and [h["edge"] for h in res.witness] == ["e4", "e5"]


def test_random_graphs_match_the_threshold_sweep():
    rng = random.Random("bottleneck")
    seen = set()
    for _ in range(800):
        nodes, edges = random_graph(rng, max_nodes=8, max_edges=18)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(n) for n in nodes]
        scope = random_scope(rng)
        weights = rng.choice([{"by_relation": {r: rng.choice([-2, 0, 1, 3, 2.5]) for r in RELATIONS}},
                              {"by_edge": {e["id"]: rng.randint(-3, 9) for e in edges}}])
        objective = rng.choice(["max_min", "min_max"])
        sources, targets = rng.sample(ids, 1), rng.sample(ids, min(2, len(ids)))
        res = run(p, nodes, edges, sources=sources, targets=targets, scope=scope, weights=weights,
                  objective=objective, certificate=True)
        want = bottleneck(nodes, edges, sources, targets, scope, weights, objective)
        assert res.status == COMPLETED
        if want is None:
            assert res.found is False
            seen.add("none")
        else:
            assert res.found is True and (res.answer["value"], len(res.witness)) == want
            seen.add("zero" if want[1] == 0 else "path")
    assert seen == {"none", "zero", "path"}


def test_an_interrupted_search_bounds_the_best_value():
    rng = random.Random("bottleneck-limits")
    bounded = 0
    for _ in range(500):
        nodes, edges = random_graph(rng, max_nodes=8, max_edges=20)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(n) for n in nodes]
        scope, objective = random_scope(rng), rng.choice(["max_min", "min_max"])
        weights = {"by_relation": {r: rng.randint(0, 5) for r in RELATIONS}}
        sources, targets = rng.sample(ids, 1), rng.sample(ids, 1)
        res = run(p, nodes, edges, sources=sources, targets=targets, scope=scope, weights=weights,
                  objective=objective, certificate=True, limits={"max_expansions": rng.randint(0, 4)})
        if res.status != INTERRUPTED:
            continue
        want = bottleneck(nodes, edges, sources, targets, scope, weights, objective)
        bound, value = res.answer["bound"], res.answer["value"]
        better = (lambda a, b: a > b) if objective == "max_min" else (lambda a, b: a < b)
        if want is not None and want[0] is not None and bound is not None:
            assert not better(want[0], bound)                      # the best is no better than the bound
            bounded += 1
        if value is not None and want is not None:
            assert not better(value, want[0])                      # a tentative value is a real path's
    assert bounded >= 40


def test_a_threshold_made_without_an_edge_never_passes_where_the_edge_improves_the_answer():
    rng = random.Random("bottleneck-soundness")
    improved = 0
    for _ in range(2500):
        nodes, edges = random_graph(rng, max_nodes=6, max_edges=20)
        if not edges:
            continue
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        cut = rng.randrange(len(edges))
        less = edges[:cut] + edges[cut + 1:]
        q = Projection.build(nodes=nodes, edges=less, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(n) for n in nodes]
        raw = {"sources": rng.sample(ids, 1), "targets": rng.sample(ids, 1), "scope": random_scope(rng),
               "weights": {"by_relation": {r: rng.randint(0, 5) for r in RELATIONS}},
               "objective": rng.choice(["max_min", "min_max"]), "certificate": True}
        full = execute(p, p.request("bottleneck_path", **raw))
        part = execute(q, q.request("bottleneck_path", **raw))
        if (full.found, full.answer["value"]) != (part.found, part.answer["value"]):
            improved += 1
            assert verify_result(part, nodes=nodes, edges=edges), raw
            assert revalidate(part, p, diff=diff(q, p)).verdict != HOLDS
    assert improved >= 60


def test_revalidation_of_bottleneck_answers_agrees_with_fresh_runs():
    rng = random.Random("bottleneck-revalidate")
    verdicts = set()
    for _ in range(400):
        nodes, edges = random_graph(rng, max_nodes=8, max_edges=16)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(n) for n in nodes]
        raw = {"sources": rng.sample(ids, 1), "targets": rng.sample(ids, 1), "scope": random_scope(rng),
               "weights": {"by_relation": {r: rng.randint(0, 5) for r in RELATIONS}},
               "objective": rng.choice(["max_min", "min_max"]), "certificate": True}
        old = execute(p, p.request("bottleneck_path", **raw))
        edited = list(edges)
        if edited and rng.random() < 0.5:
            edited.pop(rng.randrange(len(edited)))
        edited.append({"id": f"new-{rng.randrange(10**6)}", "source": rng.choice(ids), "target": rng.choice(ids),
                       "relation": rng.choice(RELATIONS)})
        q = Projection.build(nodes=nodes, edges=edited, relations=RELATIONS, node_types=TYPES)
        full, fast = revalidate(old, q), revalidate(old, q, diff=diff(p, q))
        assert (full.verdict, full.reasons) == (fast.verdict, fast.reasons)
        fresh = execute(q, q.request("bottleneck_path", **raw))
        if full.verdict == HOLDS:
            assert (fresh.found, fresh.answer["value"]) == (old.found, old.answer["value"])
            assert not old.found or fresh.answer["witness"]["length"] == old.answer["witness"]["length"]
            assert verify_result(old, nodes=nodes, edges=edited) == []
        elif full.verdict == FAILS:
            assert verify_result(old, nodes=nodes, edges=edited)
        verdicts.add(full.verdict)
    assert verdicts == {"holds", "fails", "unknown"}


def test_a_shorter_path_at_the_same_value_breaks_the_certificate():
    """s-a-t, then a direct s-t at the same weight. The threshold proves only
    that nothing is strictly better, so the old two-hop answer was said to hold, by certificate and by diff, and a
    resealed copy on the new records passed every check. The layers below the witness now prove its fewest hops."""
    nodes = ["s", "a", "t"]
    edges = [{"id": "sa", "source": "s", "target": "a", "relation": "R"},
             {"id": "at", "source": "a", "target": "t", "relation": "R"}]
    more = edges + [{"id": "st", "source": "s", "target": "t", "relation": "R"}]
    p, q = Projection.build(nodes=nodes, edges=edges), Projection.build(nodes=nodes, edges=more)
    for objective in ("max_min", "min_max"):
        raw = {"sources": ["s"], "targets": ["t"], "scope": [("R", "forward")], "weights": {"by_relation": {"R": 1}},
               "objective": objective, "certificate": True}
        old = run(p, nodes, edges, **raw)
        assert [tuple(e.values()) for e in old.answer["certificate"]["layers"]] == [("a", 1), ("s", 0)]
        assert run(q, nodes, more, **raw).answer["witness"]["length"] == 1
        assert revalidate(old, q).verdict != HOLDS and revalidate(old, q, diff=diff(p, q)).verdict != HOLDS
        data = old.to_dict()
        request = q.request("bottleneck_path", **raw)
        data.update(projection=q.identity, request=request.to_dict(), request_id=request.request_id)
        data["result_id"] = digest({k: v for k, v in data.items() if k not in ("result_id", "timing")})
        assert any("does not list at 1 hops" in x for x in verify_result(data, nodes=nodes, edges=more))
        data = thaw(old.to_dict())                          # layers that leave out the second node
        data["answer"]["certificate"]["layers"] = [{"node": "s", "hops": 0}]
        data["result_id"] = digest({k: v for k, v in data.items() if k not in ("result_id", "timing")})
        assert verify_result(data, nodes=nodes, edges=edges)


@pytest.mark.parametrize("params, code", [
    ({"weights": {"by_relation": {"STEP": float("nan")}}}, "non_finite_cost"),
    ({"weights": {"by_relation": {"STEP": "fast"}}}, "invalid_cost"),
    ({"weights": {"by_edge": {"r1": 1}}}, "missing_cost"),
    ({"weights": {"by_relation": {"STEP": 1}}, "objective": "max_sum"}, "invalid_request"),
    ({"weights": {"by_relation": {"STEP": 1, "NOPE": 1}}}, "unknown_relation"),
])
def test_bad_bottleneck_requests_fail_in_the_result(params, code):
    p = Projection.build(nodes=NODES, edges=EDGES, relations=["STEP", "OTHER"])
    res = execute(p, {"operation": "bottleneck_path", "projection": p.identity, "sources": ["A"], "targets": ["P"],
                      "scope": [("STEP", "forward")], **params})
    assert res.status == "failed" and res.error["code"] == code
