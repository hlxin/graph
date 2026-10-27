"""Minimum spanning forests: Kruskal's algorithm in the order of (weight, edge ID). The forest is unique in that order,
so an independent Kruskal over the records gives its edges exactly, networkx gives its weight, and the verifier proves
it from the records by the cycle property; forged forests are refused."""

import random
from fractions import Fraction

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, normalize_request, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from test_certificates import reseal
from oracle import RELATIONS, TYPES, node_id, random_graph, random_scope
from test_revalidate import edited


def links_of(edges, scope):
    """The edges a scope follows either way, loops aside: the forest's candidate links."""
    rels = {(s["relation"] if isinstance(s, dict) else s[0]) for s in scope}
    return [e for e in edges if e["relation"] in rels and e["source"] != e["target"]]


def kruskal(nodes, edges, scope, weights, objective):
    """The forest, by plain Kruskal over (signed weight, edge ID) with a dictionary union-find."""
    kind, table = next(iter(weights.items())) if weights else (None, None)
    rational = table is not None and any(isinstance(x, float) for x in table.values())

    def w(e):
        if table is None:
            return 1
        x = table[e["relation"] if kind == "by_relation" else e["id"]]
        return Fraction(x) if rational else x
    sign = -1 if objective == "max" else 1
    boss = {node_id(n): node_id(n) for n in nodes}

    def find(x):
        while boss[x] != x:
            x = boss[x]
        return x
    kept = []
    for e in sorted(links_of(edges, scope), key=lambda e: (sign * w(e), e["id"])):
        a, b = find(e["source"]), find(e["target"])
        if a != b:
            boss[a] = b
            kept.append(e["id"])
    return sorted(kept), sum((w(e) for e in edges if e["id"] in set(kept)), Fraction(0) if rational else 0)


def random_weights(rng, edges):
    pick = (lambda: rng.randint(-3, 5)) if rng.random() < 0.7 else (lambda: rng.choice([-1.5, 0.25, 2, 3.75]))
    if rng.random() < 0.3:
        return None
    if rng.random() < 0.5:
        return {"by_relation": {r: pick() for r in RELATIONS}}
    return {"by_edge": {e["id"]: pick() for e in edges}}


def run(rng, n=9, m=20):
    nodes, edges = random_graph(rng, max_nodes=n, max_edges=m)
    p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
    raw = {"operation": "spanning_forest", "scope": random_scope(rng), "weights": random_weights(rng, edges),
           "objective": rng.choice(["min", "max"])}
    return nodes, edges, p, raw


def test_the_forest_is_kruskals_and_proves_itself():
    rng = random.Random("spanning forest")
    shapes = set()
    for _ in range(800):
        nodes, edges, p, raw = run(rng)
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        want, total = kruskal(nodes, edges, raw["scope"], raw["weights"], raw["objective"])
        a = res.answer
        assert list(a["edges"]) == want and a["count"] == len(want) and a["trees"] == len(nodes) - len(want)
        assert (Fraction(a["weight_exact"]) if "weight_exact" in a else a["weight"]) == total
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        shapes.add((len(want) > 0, a["trees"] > 1, "weight_exact" in a, raw["objective"]))
    assert len(shapes) >= 6


def test_the_weight_is_networkx_minimum_and_maximum():
    nx = pytest.importorskip("networkx")
    rng = random.Random("networkx forest")
    for _ in range(40):
        n, nodes = 60, [f"v{i:02d}" for i in range(60)]
        edges = [{"id": f"e{k:03d}", "source": rng.choice(nodes), "target": rng.choice(nodes), "relation": "R"}
                 for k in range(150)]
        table = {e["id"]: rng.randint(-20, 50) for e in edges}
        p = Projection.build(nodes=nodes, edges=edges)
        g = nx.MultiGraph()
        g.add_nodes_from(nodes)
        for e in edges:
            if e["source"] != e["target"]:
                g.add_edge(e["source"], e["target"], key=e["id"], weight=table[e["id"]])
        for objective, edges_of in (("min", nx.minimum_spanning_edges), ("max", nx.maximum_spanning_edges)):
            res = execute(p, p.request("spanning_forest", scope=[("R", "forward")], weights={"by_edge": table},
                                       objective=objective))
            want = sum(d["weight"] for *_, d in edges_of(g, keys=True, data=True))
            assert res.answer["weight"] == want
            assert res.answer["trees"] == nx.number_connected_components(g)


def test_forged_forests_are_refused():
    nodes = ["a", "b", "c", "d"]
    edges = [{"id": "ab", "source": "a", "target": "b", "relation": "R"},
             {"id": "bc", "source": "b", "target": "c", "relation": "R"},
             {"id": "ac", "source": "a", "target": "c", "relation": "R"},
             {"id": "cd", "source": "c", "target": "d", "relation": "R"},
             {"id": "dd", "source": "d", "target": "d", "relation": "R"}]
    weights = {"by_edge": {"ab": 1, "bc": 2, "ac": 2, "cd": 5, "dd": -9}}
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("spanning_forest", scope=[("R", "forward")], weights=weights))
    assert list(res.answer["edges"]) == ["ab", "ac", "cd"] and res.answer["weight"] == 8   # ac before bc: by ID

    def forged(**change):
        data = thaw(res.to_dict())
        data["answer"] = {**data["answer"], **change}
        return verify_result(reseal(data), nodes=nodes, edges=edges)
    assert forged(edges=["ab", "bc", "cd"])                      # as light, but not first in edge ID among ties
    assert forged(edges=["ab", "bc"], count=2, trees=2, weight=3)   # cd left out: two trees where one was joined
    assert forged(edges=["ab", "ac", "bc", "cd"], count=4, trees=0, weight=10)   # a cycle
    assert forged(edges=["ab", "bc", "cd", "dd"], count=4, trees=0, weight=-1)   # a loop
    assert forged(weight=7)
    data = thaw(res.to_dict())
    data["request"] = {**data["request"], "objective": "max"}      # the same forest is not the maximum one
    data["request_id"] = normalize_request(data["request"]).request_id
    assert verify_result(reseal(data), nodes=nodes, edges=edges)


def test_an_interrupted_search_claims_nothing():
    rng = random.Random("forest interrupted")
    seen = set()
    for _ in range(200):
        nodes, edges, p, raw = run(rng)
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 8)}})
        assert verify_result(res, nodes=nodes, edges=edges) == []
        if res.status == INTERRUPTED:
            assert all(res.answer[k] is None for k in ("edges", "count", "trees", "weight"))
        seen.add(res.status)
    assert seen == {COMPLETED, INTERRUPTED}


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("forest revalidation")
    tally = {}
    for _ in range(400):
        nodes, edges, p, raw = run(rng)
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES)
        if raw["weights"] and "by_edge" in raw["weights"]:
            raw["weights"] = {"by_edge": {**{e["id"]: 1 for e in edges2}, **raw["weights"]["by_edge"]}}
        old = execute(p, {**raw, "projection": p.identity})
        verdict = revalidate(old, q, diff=diff(p, q)).verdict
        fresh = execute(q, {**raw, "projection": q.identity})
        if verdict == HOLDS:
            assert fresh.status == COMPLETED and fresh.answer == old.answer
        elif verdict == FAILS:
            assert fresh.status != COMPLETED or fresh.answer != old.answer
        tally[verdict] = tally.get(verdict, 0) + 1
    assert tally.get(HOLDS, 0) >= 20 and tally.get(FAILS, 0) >= 20, tally


def test_forest_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from graph_runtime import json_schema
    from graph_runtime._validation import shape_problems
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("forest schema")
    for _ in range(60):
        nodes, edges, p, raw = run(rng)
        for limits in ({}, {"max_expansions": 1}):
            data = json.loads(json.dumps(thaw(execute(p, {**raw, "projection": p.identity, "limits": limits})
                                              .to_dict())))
            assert [e.message for e in validator.iter_errors(data)] == [] and shape_problems("result", data) == []


@pytest.mark.parametrize("added", [{"id": "z", "relation": "S"}, {"id": "a0", "relation": "R"}])
def test_a_frozen_answer_is_verified_and_revalidated_like_any_other(added):
    """A lighter parallel link (S weighs 1, R 2), or one as heavy and earlier in edge ID, makes another forest: the old
    answer fails, whether it comes as a Result, a plain dictionary, or a dictionary holding the Result's frozen
    answer."""
    nodes = ["a", "b"]
    edges = [{"id": "m", "source": "a", "target": "b", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges, relations=["R", "S"])
    old = execute(p, p.request("spanning_forest", scope=[("R", "forward"), ("S", "forward")],
                               weights={"by_relation": {"R": 2, "S": 1}}))
    assert list(old.answer["edges"]) == ["m"]
    edges2 = edges + [{"id": added["id"], "source": "a", "target": "b", "relation": added["relation"]}]
    q = Projection.build(nodes=nodes, edges=edges2, relations=["R", "S"])
    frozen = {**old.to_dict(), "answer": old.answer}
    for form in (old, thaw(old.to_dict()), frozen):
        assert verify_result(form, nodes=nodes, edges=edges) == []
        assert verify_result(form, nodes=nodes, edges=edges2)
        assert revalidate(form, q).verdict == FAILS
