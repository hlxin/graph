"""Revalidation by certificate: components, biconnectivity, maximum flows, matchings and dominators hold on an edited
projection exactly when their certificate still checks against its records. Whenever revalidation says a result
holds, a fresh run on the edited projection gives the same answer (what is canonical of it: the component sets,
articulation points, bridges and blocks, the flow's value and its cut nearest the sources, the matching's size, the
dominator tree); a result that names a node or edge that is gone fails."""

import random

from graph_runtime import Projection, execute, verify_result
from graph_runtime.revalidate import diff, revalidate
from oracle import RELATIONS, TYPES, node_id, random_graph, random_scope
from test_revalidate import edited


def request(rng, op, ids):
    scope = random_scope(rng)
    if op == "components":
        return dict(scope=scope, connectivity=rng.choice(["weak", "strong"]), min_size=rng.choice([1, 2]))
    if op == "biconnectivity":
        return dict(scope=scope, blocks=True)
    if op == "max_flow":
        picks = rng.sample(ids, min(len(ids), rng.randint(2, 3)))
        return dict(sources=picks[:1], targets=picks[1:], scope=scope, node_capacity=rng.choice([None, 1]),
                    capacities=rng.choice([None, {"by_relation": {r: rng.randint(1, 3) for r in RELATIONS}}]))
    if op == "matching":
        return dict(left={"node_types": rng.sample(TYPES, 1)}, scope=scope)
    if op == "incidence":
        rows = rng.sample(ids, rng.randint(1, len(ids) - 1))
        return dict(rows={"nodes": rows}, columns={"nodes": [n for n in ids if n not in rows]}, scope=scope,
                    coefficients={"by_relation": {r: rng.choice([-2, -1, 1, 3]) for r in RELATIONS}},
                    kernels=rng.choice([[], ["left"], ["right"], ["left", "right"]]))
    return dict(sources=rng.sample(ids, 1), scope=scope, targets=rng.choice([None, rng.sample(ids, 1)]))


def canonical(op, answer):
    if op == "components":
        return [c["nodes"] for c in answer["components"]], answer["total"]
    if op == "biconnectivity":
        return answer["articulation_points"], answer["bridges"], answer["blocks"], answer["components"]
    if op == "max_flow":
        return answer["value"], answer["cut"], answer["cut_nodes"]
    if op == "matching":
        return answer["size"], answer["left_count"], answer["deficiency"]
    if op == "incidence":
        return answer["rank"], answer["left_kernel"], answer["right_kernel"]
    return answer["reached"], answer["tree"], answer["chains"]


def test_a_result_holds_exactly_when_its_certificate_still_checks():
    rng = random.Random("revalidate-checked")
    tally: dict = {}
    for _ in range(720):
        nodes, edges = random_graph(rng, max_nodes=9, max_edges=18)
        ids = [node_id(n) for n in nodes]
        if len(ids) < 2:
            continue
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        op = rng.choice(["components", "biconnectivity", "max_flow", "matching", "dominators", "incidence"])
        raw = request(rng, op, ids)
        old = execute(p, p.request(op, certificate=rng.random() < 0.85, **raw))
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES)
        verdict = revalidate(old, q, diff=diff(p, q))
        assert revalidate(old, q).to_dict() == verdict.to_dict()
        if verdict.verdict == "holds":
            assert verify_result(old, nodes=nodes2, edges=edges2) == []
            fresh = execute(q, q.request(op, **raw))
            assert canonical(op, fresh.answer) == canonical(op, old.answer), (op, raw)
        codes = {r["code"] for r in verdict.reasons}
        if old.answer.get("certificate") is None and old.complete and verdict.verdict != "fails":
            assert codes <= {"no_certificate"}
        tally[(op, verdict.verdict)] = tally.get((op, verdict.verdict), 0) + 1
    for op in ("components", "biconnectivity", "max_flow", "matching", "dominators", "incidence"):
        assert tally.get((op, "holds"), 0) >= 10, tally
        assert tally.get((op, "unknown"), 0) + tally.get((op, "fails"), 0) >= 10, tally


def test_a_stale_nearest_cut_and_a_broken_kernel_are_refuted():
    """Removing an unused parallel edge leaves the cached flow a maximum flow and its cut a minimum cut, but no longer
    the cut nearest the sources: the answer fails. A requested kernel vector that no longer multiplies to zero
    fails too, rather than leaving the answer unknown."""
    nodes = ["s", "a", "t"]
    edges = [{"id": "e1", "source": "s", "target": "a", "relation": "R"},
             {"id": "e2", "source": "s", "target": "a", "relation": "R"},
             {"id": "e3", "source": "a", "target": "t", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    old = execute(p, p.request("max_flow", sources=["s"], targets=["t"], scope=[("R", "forward")], certificate=True))
    assert [h["edge"] for h in old.answer["cut"]] == ["e3"]
    q = Projection.build(nodes=nodes, edges=edges[:1] + edges[2:])
    assert [h["edge"] for h in execute(q, q.request("max_flow", sources=["s"], targets=["t"],
                                                    scope=[("R", "forward")])).answer["cut"]] == ["e1"]
    verdict = revalidate(old, q)
    assert verdict.verdict == "fails" and {r["code"] for r in verdict.reasons} == {"answer_refuted"}

    nodes = [{"id": "x", "types": ["Row"]}, {"id": "y", "types": ["Row"]}, {"id": "c1", "types": ["Column"]},
             {"id": "c2", "types": ["Column"]}]
    before = [{"id": "m1", "source": "x", "target": "c1", "relation": "R"},
              {"id": "m2", "source": "x", "target": "c2", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=before)
    old = execute(p, p.request("incidence", rows={"node_types": ["Row"]}, columns={"node_types": ["Column"]},
                               scope=[("R", "forward")], coefficients={"by_relation": {"R": 1}}, kernels=["right"]))
    assert [[(e["node"], e["value"]) for e in v] for v in old.answer["right_kernel"]] == [[("c1", -1), ("c2", 1)]]
    q = Projection.build(nodes=nodes, edges=before + [{"id": "m3", "source": "y", "target": "c1", "relation": "R"}])
    verdict = revalidate(old, q)
    assert verdict.verdict == "fails" and {r["code"] for r in verdict.reasons} == {"answer_refuted"}
