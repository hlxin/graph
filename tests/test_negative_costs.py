"""Shortest paths with negative costs: Bellman-Ford-Moore with subtree disassembly over the nodes on some walk from a
source to a target. Its least costs agree with an independent fixpoint (oracle.signed_least_cost), a negative cycle on
such a walk is reported with three walks that prove it, a negative cycle elsewhere changes nothing, feasible
potentials prove an optimum, and nothing is claimed that an interrupted search has not shown."""

import copy
import random
from fractions import Fraction

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from test_certificates import reseal
from oracle import RELATIONS, TYPES, node_id, random_graph, random_scope, signed_fewest_hops, signed_least_cost
from test_revalidate import edited


def signed_costs(rng, edges):
    """A cost table with at least one negative cost: by relation or by edge, integers or (sometimes) floats."""
    pick = (lambda: rng.choice([-2, -1, 0, 1, 2, 3])) if rng.random() < 0.7 else \
        (lambda: rng.choice([-1.5, -0.25, 0, 0.5, 2.75]))
    if rng.random() < 0.5 or not edges:
        table = {r: pick() for r in RELATIONS}
        if all(x >= 0 for x in table.values()):
            table[rng.choice(RELATIONS)] = -1
        return {"by_relation": table}
    table = {e["id"]: pick() for e in edges}
    if all(x >= 0 for x in table.values()):
        table[rng.choice(edges)["id"]] = -1
    return {"by_edge": table}


def cost_of(answer):
    return Fraction(answer["cost_exact"]) if "cost_exact" in answer else answer["cost"]


def run(rng, n=8, m=18):
    nodes, edges = random_graph(rng, max_nodes=n, max_edges=m)
    ids = [node_id(x) for x in nodes]
    p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
    raw = {"operation": "shortest_path", "sources": rng.sample(ids, rng.randint(1, min(2, len(ids)))),
           "targets": rng.sample(ids, rng.randint(1, min(2, len(ids)))), "scope": random_scope(rng),
           "costs": signed_costs(rng, edges), "certificate": rng.random() < 0.7}
    return nodes, edges, p, raw


def test_least_costs_and_negative_cycles_agree_with_an_independent_fixpoint():
    rng = random.Random("negative costs")
    tally = {"cost": 0, "none": 0, "unbounded": 0, "dead_ends": 0}
    for _ in range(1500):
        nodes, edges, p, raw = run(rng)
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == [], raw
        assert contract_problems(res.to_dict()) == []
        verdict, want = signed_least_cost(nodes, edges, raw["sources"], raw["targets"], raw["scope"], raw["costs"])
        a = res.answer
        tally[verdict] += 1
        if verdict == "unbounded":
            assert a["negative_cycle"] is not None and a["found"] is True and a["cost"] is None, raw
        elif verdict == "none":
            assert a["found"] is False and a["negative_cycle"] is None
        else:
            assert a["negative_cycle"] is None and a["found"] is True and cost_of(a) == want, (raw, a, want)
            target, fewest = signed_fewest_hops(nodes, edges, raw["sources"], raw["targets"], raw["scope"],
                                                raw["costs"])
            assert (a["witness"]["target"], a["witness"]["length"]) == (target, fewest), (raw, a)
            cert = a["certificate"]
            assert (cert is not None) == raw["certificate"]
            tally["dead_ends"] += bool(cert and cert["dead_ends"])
    assert min(tally.values()) >= 40, tally


def test_a_negative_cycle_that_reaches_no_target_changes_nothing():
    nodes = ["s", "a", "t", "x", "y"]
    edges = [{"id": "e1", "source": "s", "target": "a", "relation": "R"},
             {"id": "e2", "source": "a", "target": "t", "relation": "R"},
             {"id": "e3", "source": "a", "target": "x", "relation": "R"},
             {"id": "e4", "source": "x", "target": "y", "relation": "R"},
             {"id": "e5", "source": "y", "target": "x", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("shortest_path", sources=["s"], targets=["t"], scope=[("R", "forward")],
                               costs={"by_edge": {"e1": 1, "e2": 1, "e3": -1, "e4": -5, "e5": 1}}, certificate=True))
    assert (res.answer["cost"], res.answer["negative_cycle"]) == (2, None)
    assert list(res.answer["certificate"]["dead_ends"]) == ["x", "y"]
    assert verify_result(res, nodes=nodes, edges=edges) == []


def test_non_negative_costs_are_answered_as_before():
    rng = random.Random("unchanged")
    for _ in range(200):
        nodes, edges, p, raw = run(rng)
        raw["costs"] = {"by_relation": {r: rng.randint(0, 3) for r in RELATIONS}}
        a = execute(p, {**raw, "projection": p.identity}).answer
        assert "negative_cycle" not in a                      # Dijkstra's answer, with its own certificate kinds
        assert a["certificate"] is None or a["certificate"]["kind"] in ("potentials", "closure")


def test_an_interrupted_search_claims_no_bound_and_no_negative():
    rng = random.Random("interrupted negative")
    seen = set()
    for _ in range(600):
        nodes, edges, p, raw = run(rng)
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 12)}})
        assert verify_result(res, nodes=nodes, edges=edges) == []
        if res.status == INTERRUPTED:
            a = res.answer
            assert a["found"] in (True, None) and a["lower_bound"] is None and a["certificate"] is None
            assert a["negative_cycle"] is None
            if a["found"]:
                verdict, want = signed_least_cost(nodes, edges, raw["sources"], raw["targets"], raw["scope"],
                                                  raw["costs"])
                assert verdict == "unbounded" or want <= cost_of(a)   # a real walk: no cheaper than the least
            seen.add(a["found"])
    assert seen == {True, None}


def tampered(res):
    data = copy.deepcopy(thaw(res.to_dict()))
    return data, data["answer"]


def test_forged_proofs_are_rejected():
    nodes = ["s", "a", "b", "t", "x"]
    edges = [{"id": "e1", "source": "s", "target": "a", "relation": "R"},
             {"id": "e2", "source": "a", "target": "t", "relation": "R"},
             {"id": "e3", "source": "s", "target": "b", "relation": "R"},
             {"id": "e4", "source": "b", "target": "t", "relation": "R"},
             {"id": "e5", "source": "a", "target": "x", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    raw = {"sources": ["s"], "targets": ["t"], "scope": [("R", "forward")],
           "costs": {"by_edge": {"e1": 2, "e2": -3, "e3": 1, "e4": 1, "e5": 4}}, "certificate": True}
    res = execute(p, p.request("shortest_path", **raw))
    assert res.answer["cost"] == -1 and verify_result(res, nodes=nodes, edges=edges) == []

    def problems(data):
        return verify_result(reseal(data), nodes=nodes, edges=edges)

    data, a = tampered(res)                                   # a potential raised: the hop to t no longer bounds it
    next(x for x in a["certificate"]["potentials"] if x["node"] == "t")["value"] = 0
    assert problems(data)
    data, a = tampered(res)                                   # the target called a dead end
    a["certificate"]["dead_ends"] = ["t", "x"]
    a["certificate"]["potentials"] = [x for x in a["certificate"]["potentials"] if x["node"] != "t"]
    assert problems(data)
    data, a = tampered(res)                                   # a live node called a dead end: a leads to t
    a["certificate"]["dead_ends"] = ["a", "x"]
    a["certificate"]["potentials"] = [x for x in a["certificate"]["potentials"] if x["node"] != "a"]
    assert problems(data)
    data, a = tampered(res)                                   # the old kind, whose unlisted nodes sit at the bound
    a["certificate"] = {"kind": "potentials", "bound": -1, "potentials": a["certificate"]["potentials"]}
    assert problems(data)

    loop_edges = edges + [{"id": "e6", "source": "t", "target": "s", "relation": "R"}]
    q = Projection.build(nodes=nodes, edges=loop_edges)
    cyc = execute(q, q.request("shortest_path", **{**raw, "costs": {"by_edge": {**raw["costs"]["by_edge"],
                                                                               "e6": 0}}}))
    nc = cyc.answer["negative_cycle"]
    assert nc is not None and verify_result(cyc, nodes=nodes, edges=loop_edges) == []
    data, a = tampered(cyc)                                   # a cycle that costs nothing
    a["negative_cycle"]["cycle"]["cost"] = 0
    assert verify_result(reseal(data), nodes=nodes, edges=loop_edges)
    data, a = tampered(cyc)                                   # a cycle that does not close
    a["negative_cycle"]["cycle"]["hops"] = a["negative_cycle"]["cycle"]["hops"][:-1]
    a["negative_cycle"]["cycle"]["length"] -= 1
    assert verify_result(reseal(data), nodes=nodes, edges=loop_edges)
    assert nc["cycle"]["source"] == "s" and [h["edge"] for h in nc["cycle"]["hops"]] == ["e1", "e2", "e6"]
    data, a = tampered(cyc)                                   # a cycle shown to lead to no target
    a["negative_cycle"]["lead_out"] = {"source": "s", "target": "s", "length": 0, "hops": []}
    assert verify_result(reseal(data), nodes=nodes, edges=loop_edges)


def test_revalidation_of_signed_answers_agrees_with_fresh_runs():
    rng = random.Random("revalidate negative")
    tally = {}
    for _ in range(600):
        nodes, edges, p, raw = run(rng)
        raw["certificate"] = True
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES)
        old = execute(p, {**raw, "projection": p.identity})
        full, fast = revalidate(old, q), revalidate(old, q, diff=diff(p, q))
        assert (fast.verdict, fast.reasons) == (full.verdict, full.reasons), raw
        fresh = execute(q, {**raw, "projection": q.identity})
        if full.verdict == HOLDS:
            a, b = old.answer, fresh.answer
            assert fresh.status == COMPLETED and (a["found"], a["negative_cycle"] is None) == \
                (b["found"], b["negative_cycle"] is None), (raw, full.reasons)
            assert a["negative_cycle"] is not None or not a["found"] or cost_of(a) == cost_of(b)
            assert verify_result(old, nodes=nodes2, edges=edges2) == []
        elif full.verdict == FAILS:
            assert fresh.status != COMPLETED or verify_result(old, nodes=nodes2, edges=edges2), full.reasons
        kind = "cycle" if old.answer["negative_cycle"] else (old.answer["certificate"] or {}).get("kind")
        tally[(kind, full.verdict)] = tally.get((kind, full.verdict), 0) + 1
    for key in (("feasible_potentials", HOLDS), ("feasible_potentials", FAILS), ("cycle", HOLDS), ("cycle", FAILS),
                ("closure", HOLDS)):
        assert tally.get(key, 0) >= 5, (key, tally)


class Unread(list):
    """A certificate list that may be searched (indexed) but not read whole."""

    def __iter__(self):
        raise AssertionError("an unrelated edit read the whole certificate")


def test_an_unrelated_edit_does_not_read_the_whole_certificate():
    for costs in ({"R": -1}, {"R": 1}):                     # feasible potentials, and Dijkstra's potentials
        nodes = ["s", "t"] + [f"leaf{i:04d}" for i in range(300)]
        edges = [{"id": "st", "source": "s", "target": "t", "relation": "R"}] + \
            [{"id": f"x{i:04d}", "source": "s", "target": f"leaf{i:04d}", "relation": "R"} for i in range(300)]
        p = Projection.build(nodes=nodes, edges=edges)
        res = execute(p, p.request("shortest_path", sources=["s"], targets=["t"], scope=[("R", "forward")],
                                   costs={"by_relation": costs}, certificate=True))
        q = Projection.build(nodes=nodes + ["far"], edges=edges)
        data = thaw(res.to_dict())
        cert = data["answer"]["certificate"]
        for key in ("potentials", "dead_ends", "reached"):
            if key in cert:
                cert[key] = Unread(cert[key])
        assert revalidate(data, q, diff=diff(p, q)).verdict == HOLDS
