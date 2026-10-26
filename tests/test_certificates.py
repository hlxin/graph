"""Certificates: complete answers that prove themselves against the records without a second search. A complete
reachability or impact answer is closed; a typed path's negative comes with the closed states it reached; a shortest
path's optimum or lower bound with potentials, and its negative with a closure. verify_result checks each, and a
certificate made on a graph that lacks an edge must not pass on the graph that has it, whenever that edge changes
the answer."""

import copy
import random
from fractions import Fraction

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.result import contract_problems
from oracle import RELATIONS, TYPES, least_cost, least_depths, node_id, random_graph, random_scope, typed_ends


def requests(rng, ids):
    scope = random_scope(rng)
    costs = rng.choice([{"by_relation": {r: rng.randint(0, 3) for r in RELATIONS}},
                        {"by_relation": {r: rng.choice([0.5, 1, 2.25]) for r in RELATIONS}}])
    return [
        {"operation": "reachability", "sources": rng.sample(ids, 1), "scope": scope,
         "max_depth": rng.choice([None, 1, 2])},
        {"operation": "dependency_impact", "changed": rng.sample(ids, min(2, len(ids))), "scope": scope},
        {"operation": "typed_path", "sources": rng.sample(ids, 1), "certificate": True,
         "targets": rng.choice([None, rng.sample(ids, 1)]),
         "pattern": [{"scope": random_scope(rng), "node_types": rng.choice([None, ["A"]])}
                     for _ in range(rng.randint(1, 3))]},
        {"operation": "shortest_path", "sources": rng.sample(ids, 1), "targets": rng.sample(ids, 1),
         "scope": scope, "costs": costs, "certificate": True},
    ]


def answer_of(res):
    """What an answer claims, apart from which of equally good witnesses it gives."""
    a = res.answer
    if res.operation in ("reachability", "dependency_impact"):
        return {e["node"]: e["depth"] for e in a["reached" if res.operation == "reachability" else "affected"]}
    if res.operation == "typed_path":
        return a["found"]
    return a["found"], (Fraction(a["cost_exact"]) if "cost_exact" in a else a["cost"]) if a["found"] else None


def test_certificates_appear_exactly_when_asked_and_needed():
    rng = random.Random("certificates")
    kinds = set()
    for _ in range(400):
        nodes, edges = random_graph(rng)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(n) for n in nodes]
        for raw in requests(rng, ids)[2:]:
            for certify in (True, False):
                res = execute(p, {**raw, "certificate": certify, "projection": p.identity})
                assert verify_result(res, nodes=nodes, edges=edges) == []
                assert Result.from_dict(res.to_dict()).result_id == res.result_id
                cert = res.answer["certificate"]
                if not certify:
                    assert cert is None
                    continue
                if res.operation == "typed_path":
                    assert (cert is not None) == (res.answer["found"] is False)
                else:
                    assert cert is not None
                    assert cert["kind"] == ("potentials" if res.answer["found"] else "closure")
                if cert is not None:
                    kinds.add((res.operation, cert["kind"]))
    assert kinds == {("typed_path", "closed_states"), ("shortest_path", "potentials"),
                     ("shortest_path", "closure")}


def test_an_interrupted_shortest_path_certifies_its_lower_bound():
    rng = random.Random("lower-bounds")
    seen = 0
    for _ in range(400):
        nodes, edges = random_graph(rng, max_nodes=8, max_edges=20)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(n) for n in nodes]
        raw = requests(rng, ids)[3]
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 3)}})
        assert verify_result(res, nodes=nodes, edges=edges) == []
        if res.status == INTERRUPTED and res.answer["certificate"] is not None:
            seen += 1
            best = least_cost(nodes, edges, raw["sources"], raw["targets"], raw["scope"], raw["costs"])
            bound = res.answer["certificate"]["bound"]
            bound = Fraction(bound) if isinstance(bound, str) else bound
            assert best is None or best >= bound
    assert seen >= 50


def test_a_certificate_without_an_edge_never_passes_where_the_edge_changes_the_answer():
    """Soundness: the answer and certificate made on the graph without one edge are checked against the records with
    it. Whenever the edge changes what the answer says, the check must find a problem: the certificate (or the closure
    of a reachability answer) no longer proves it."""
    rng = random.Random("soundness")
    changed = 0
    for _ in range(1500):
        nodes, edges = random_graph(rng, max_nodes=8, max_edges=16)
        if not edges:
            continue
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        cut = rng.randrange(len(edges))
        less = edges[:cut] + edges[cut + 1:]
        q = Projection.build(nodes=nodes, edges=less, relations=RELATIONS, node_types=TYPES)
        for raw in requests(rng, [node_id(n) for n in nodes]):
            full = execute(p, {**raw, "projection": p.identity})
            part = execute(q, {**raw, "projection": q.identity})
            assert full.status == part.status == COMPLETED
            assert verify_result(part, nodes=nodes, edges=less) == []
            if answer_of(full) != answer_of(part):
                changed += 1
                assert verify_result(part, nodes=nodes, edges=edges), (raw, answer_of(full), answer_of(part))
    assert changed >= 300


def reseal(data):
    """A forged result with a result_id that matches it, so that only the forgery itself can be caught."""
    from graph_runtime._canonical import digest
    data["result_id"] = digest({k: v for k, v in data.items() if k not in ("result_id", "timing")})
    return data


def test_forged_certificates_are_refused():
    nodes = ["a", "b", "c", "d"]
    edges = [{"id": "e1", "source": "a", "target": "b", "relation": "R"},
             {"id": "e2", "source": "b", "target": "c", "relation": "R"},
             {"id": "e3", "source": "a", "target": "c", "relation": "R"},
             {"id": "e4", "source": "c", "target": "d", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    typed = execute(p, p.request("typed_path", sources=["a"], targets=["a"], certificate=True,
                                 pattern=[{"scope": [("R", "forward")]}] * 2)).to_dict()
    assert typed["answer"]["found"] is False and verify_result(typed, nodes=nodes, edges=edges) == []
    states = typed["answer"]["certificate"]["states"]
    for k in range(1, len(states)):              # without any state but the source's, the states are not closed
        forged = copy.deepcopy(typed)
        del forged["answer"]["certificate"]["states"][k]
        assert contract_problems(reseal(forged)) == []
        assert any("does not list" in m for m in verify_result(forged, nodes=nodes, edges=edges))
    short = execute(p, p.request("shortest_path", sources=["a"], targets=["d"], scope=[("R", "forward")],
                                 costs={"by_edge": {"e1": 1, "e2": 1, "e3": 5, "e4": 1}}, certificate=True)).to_dict()
    assert short["answer"]["cost"] == 3 and verify_result(short, nodes=nodes, edges=edges) == []
    forged = copy.deepcopy(short)
    forged["answer"]["certificate"]["potentials"] = [{"node": "a", "value": 0}]     # b would then count 3, not 1
    assert contract_problems(reseal(forged)) == []
    assert verify_result(forged, nodes=nodes, edges=edges) == [
        "certificate: the hop e1 from 'a' (0) costs 1 but leads to 'b' at 3"]
    forged = copy.deepcopy(short)
    del forged["answer"]["certificate"]["potentials"][0]                             # the source must count 0
    assert contract_problems(reseal(forged))
    closure = execute(p, p.request("shortest_path", sources=["d"], targets=["a"], scope=[("R", "forward")],
                                   costs={"by_relation": {"R": 1}}, certificate=True)).to_dict()
    assert closure["answer"]["certificate"] == {"kind": "closure", "reached": ["d"]}
    forged = copy.deepcopy(short)
    forged["answer"].update(found=False, optimal=None, cost=None, witness=None,
                            certificate={"kind": "closure", "reached": ["a", "b"]})
    assert contract_problems(reseal(forged)) == []
    assert sorted(verify_result(forged, nodes=nodes, edges=edges)) == [
        "certificate: the hop e2 leads from 'b' to 'c', which it does not list",
        "certificate: the hop e3 leads from 'a' to 'c', which it does not list"]


def test_an_incomplete_reachability_answer_is_caught_by_its_closure():
    nodes = ["a", "b", "c"]
    edges = [{"id": "e1", "source": "a", "target": "b", "relation": "R"},
             {"id": "e2", "source": "b", "target": "c", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("reachability", sources=["a"], scope=[("R", "forward")]))
    assert verify_result(res, nodes=nodes, edges=edges) == []
    more = edges + [{"id": "e3", "source": "a", "target": "c", "relation": "R"}]
    problems = verify_result(res, nodes=nodes, edges=more)              # c is one hop from a, not two
    assert problems == ["incomplete: 'c' is one hop (e3) from 'a' at depth 0, but is listed at depth 2"]
    capped = execute(p, p.request("reachability", sources=["a"], scope=[("R", "forward")], max_depth=1))
    assert verify_result(capped, nodes=nodes, edges=edges) == []       # b at the depth limit is not expanded


def test_certificate_requests_normalize():
    p = Projection.build(nodes=["a"], edges=[])
    base = {"operation": "typed_path", "sources": ["a"], "pattern": [{"scope": [("R", "forward")]}],
            "projection": p.identity}
    res = execute(p, {**base, "certificate": "yes"})
    assert res.status == "failed" and res.error["code"] == "invalid_request"
    from graph_runtime import normalize_request
    assert normalize_request(base)["certificate"] is False
    assert normalize_request({**base, "certificate": True}) != normalize_request(base)
    with pytest.raises(Exception):
        normalize_request({**base, "operation": "reachability", "scope": [("R", "forward")], "certificate": True})
