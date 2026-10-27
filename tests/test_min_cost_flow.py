"""Minimum-cost flows: successive shortest paths with potentials. networkx is the oracle, on a network that gives every
hop in scope its own arc (a middle node per hop, so parallel edges and both scope directions stay distinct) between
a virtual source feeding the sources and a virtual sink draining the targets: its least cost for the same value, or
its maximum flow's least cost. The certificate proves each answer, and forgeries are refused."""

import copy
import random
from fractions import Fraction

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from test_certificates import reseal
from oracle import RELATIONS, TYPES, hops_of, node_id, random_graph, random_scope
from test_revalidate import edited

nx = pytest.importorskip("networkx")


def oracle(edges, sources, targets, scope, capacities, costs, value):
    """(value sent, least cost) by networkx, or None when the value cannot be sent."""
    g = nx.DiGraph()
    rel = {e["id"]: e["relation"] for e in edges}

    def read(table, eid, default):
        if table is None:
            return default
        (kind, t), = table.items()
        return t[rel[eid] if kind == "by_relation" else eid]
    total = 0
    for k, (u, v, eid) in enumerate(hops_of(edges, scope)):
        if u == v:
            continue
        mid = ("hop", k)
        cap, cost = read(capacities, eid, 1), read(costs, eid, None)
        g.add_edge(u, mid, capacity=cap, weight=cost)
        g.add_edge(mid, v, capacity=cap, weight=0)
        total += cap
    for s in sources:
        g.add_edge("SOURCE", s, capacity=total + 1, weight=0)
    for t in targets:
        g.add_edge(t, "SINK", capacity=total + 1, weight=0)
    if "SOURCE" not in g or "SINK" not in g:
        return (0, 0) if not value else None
    if value is None:
        flow = nx.max_flow_min_cost(g, "SOURCE", "SINK")
        return sum(flow["SOURCE"].values()), nx.cost_of_flow(g, flow)
    g.nodes["SOURCE"]["demand"], g.nodes["SINK"]["demand"] = -value, value
    try:
        flow = nx.min_cost_flow(g)
    except nx.NetworkXUnfeasible:
        return None
    return value, nx.cost_of_flow(g, flow)


def run(rng, integral=True):
    nodes, edges = random_graph(rng, max_nodes=7, max_edges=15)
    ids = [node_id(x) for x in nodes]
    if len(ids) < 2:
        nodes, ids = nodes + ["extra"], ids + ["extra"]
    p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
    picked = rng.sample(ids, rng.randint(2, min(4, len(ids))))
    cut = rng.randint(1, len(picked) - 1)
    pick = (lambda: rng.randint(0, 4)) if integral else (lambda: rng.choice([0, 0.5, 1.25, 2]))
    raw = {"operation": "min_cost_flow", "sources": picked[:cut], "targets": picked[cut:], "scope": random_scope(rng),
           "capacities": rng.choice([None, {"by_relation": {r: rng.randint(0, 3) for r in RELATIONS}},
                                     {"by_edge": {e["id"]: rng.randint(0, 3) for e in edges}}]),
           "costs": rng.choice([{"by_relation": {r: pick() for r in RELATIONS}},
                                {"by_edge": {e["id"]: pick() for e in edges}}]),
           "value": rng.choice([None, 0, 1, 2, 3, 5]), "certificate": rng.random() < 0.8}
    return nodes, edges, p, raw


def exact(a, field):
    return Fraction(a[f"{field}_exact"]) if f"{field}_exact" in a else a[field]


def test_the_flow_is_cheapest_and_proves_itself():
    rng = random.Random("min cost flow")
    seen = set()
    for _ in range(500):
        nodes, edges, p, raw = run(rng)
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        want = oracle(edges, raw["sources"], raw["targets"], raw["scope"], raw["capacities"], raw["costs"],
                      raw["value"])
        a = res.answer
        if want is None:
            assert a["feasible"] is False
            best = oracle(edges, raw["sources"], raw["targets"], raw["scope"], raw["capacities"], raw["costs"], None)
            assert (a["value"], a["cost"]) == best                # a maximum flow at least cost instead
        else:
            assert a["feasible"] is True and (a["value"], a["cost"]) == want, raw
        seen.add((a["feasible"], raw["value"] is None, a["value"] > 0))
    assert len(seen) >= 4, seen


def test_rational_capacities_costs_and_values_are_exact():
    rng = random.Random("rational flow")
    for _ in range(200):
        nodes, edges, p, raw = run(rng, integral=False)
        raw["value"] = rng.choice([None, 0.5, 1.75])
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        if raw["value"] is None:                          # integral capacities: compare the maximum flow's cost
            quartered = {k: {x: int(Fraction(c) * 4) for x, c in t.items()} for k, t in raw["costs"].items()}
            want = oracle(edges, raw["sources"], raw["targets"], raw["scope"], raw["capacities"], quartered, None)
            assert (exact(res.answer, "value"), exact(res.answer, "cost") * 4) == want


def test_forged_flows_are_refused():
    edges = [{"id": "a", "source": "s", "target": "x", "relation": "R"},
             {"id": "b", "source": "s", "target": "y", "relation": "R"},
             {"id": "c", "source": "x", "target": "t", "relation": "R"},
             {"id": "d", "source": "y", "target": "t", "relation": "R"},
             {"id": "e", "source": "x", "target": "y", "relation": "R"}]
    nodes = ["s", "t", "x", "y"]
    caps = {"by_edge": {"a": 2, "b": 1, "c": 1, "d": 2, "e": 1}}
    costs = {"by_edge": {"a": 1, "b": 4, "c": 1, "d": 1, "e": 1}}
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("min_cost_flow", sources=["s"], targets=["t"], scope=[("R", "forward")],
                               capacities=caps, costs=costs, value=2, certificate=True))
    assert (res.answer["value"], res.answer["cost"]) == (2, 5)
    assert verify_result(res, nodes=nodes, edges=edges) == []

    def forged(change):
        data = copy.deepcopy(thaw(res.to_dict()))
        change(data["answer"])
        return verify_result(reseal(data), nodes=nodes, edges=edges)

    def dearer(a):                                         # the same value through s -> y -> t, under the old proof
        a["flows"] = [{"edge": "a", "relation": "R", "direction": "forward", "from": "s", "to": "x", "flow": 1},
                      {"edge": "b", "relation": "R", "direction": "forward", "from": "s", "to": "y", "flow": 1},
                      {"edge": "c", "relation": "R", "direction": "forward", "from": "x", "to": "t", "flow": 1},
                      {"edge": "d", "relation": "R", "direction": "forward", "from": "y", "to": "t", "flow": 1}]
        a["cost"] = 7
    assert forged(dearer)
    assert forged(lambda a: a["flows"][0].__setitem__("flow", 3))                 # beyond a capacity
    assert forged(lambda a: a.__setitem__("cost", 4))
    assert forged(lambda a: a["certificate"]["potentials"][1].__setitem__("value", -50))


def test_an_interrupted_search_keeps_a_valid_flow():
    rng = random.Random("flow interrupted")
    seen = set()
    for _ in range(300):
        nodes, edges, p, raw = run(rng)
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 20)}})
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        if res.status == INTERRUPTED:
            assert res.answer["feasible"] is None and res.answer["certificate"] is None
        seen.add(res.status)
    assert seen == {COMPLETED, INTERRUPTED}


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("flow revalidation")
    tally = {}
    for _ in range(300):
        nodes, edges, p, raw = run(rng)
        raw["certificate"] = True
        raw["capacities"] = None
        raw["costs"] = {"by_relation": {r: rng.randint(0, 3) for r in RELATIONS}}
        nodes2, edges2 = edited(rng, nodes, edges)
        if not set(raw["sources"] + raw["targets"]) <= {node_id(n) for n in nodes2}:
            continue
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES)
        old = execute(p, {**raw, "projection": p.identity})
        verdict = revalidate(old, q, diff=diff(p, q)).verdict
        fresh = execute(q, {**raw, "projection": q.identity})
        if verdict == HOLDS:
            assert (fresh.answer["value"], fresh.answer["cost"], fresh.answer["feasible"]) == \
                (old.answer["value"], old.answer["cost"], old.answer["feasible"])
        elif verdict == FAILS:
            assert verify_result(old, nodes=nodes2, edges=edges2)
        tally[verdict] = tally.get(verdict, 0) + 1
    assert tally.get(HOLDS, 0) >= 20 and tally.get(FAILS, 0) >= 10, tally


def test_flow_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from graph_runtime import json_schema
    from graph_runtime._validation import shape_problems
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("flow schema")
    for _ in range(80):
        nodes, edges, p, raw = run(rng, integral=rng.random() < 0.5)
        for limits in ({}, {"max_expansions": rng.randint(0, 9)}):
            data = json.loads(json.dumps(thaw(execute(p, {**raw, "projection": p.identity, "limits": limits})
                                              .to_dict())))
            assert [e.message for e in validator.iter_errors(data)] == [] and shape_problems("result", data) == []


def test_a_flow_names_its_edges_relation_and_stays_within_the_value():
    nodes = ["s", "t"]
    edges = [{"id": "e", "source": "s", "target": "t", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges, relations=["R", "S"])
    res = execute(p, p.request("min_cost_flow", sources=["s"], targets=["t"], scope=[("R", "forward"), ("S", "forward")],
                               capacities={"by_edge": {"e": 10}}, costs={"by_relation": {"R": 1, "S": 1}},
                               value=1, certificate=True))
    assert (res.answer["value"], res.answer["cost"]) == (1, 1)
    data = copy.deepcopy(thaw(res.to_dict()))
    data["answer"]["flows"][0]["relation"] = "S"                  # the record says R
    assert verify_result(reseal(data), nodes=nodes, edges=edges)
    relabeled = [{**edges[0], "relation": "S"}]
    q = Projection.build(nodes=nodes, edges=relabeled, relations=["R", "S"])
    assert revalidate(res, q).verdict != HOLDS                    # the cached flow still says R
    over = copy.deepcopy(thaw(res.to_dict()))                     # an interrupted answer sending more than asked
    over.update(status="interrupted", complete=False,
                interruption={"reason": "deadline", "expansions": over["stats"]["expansions"], "limit": None})
    over["answer"].update(value=10, cost=10, feasible=None, optimal=False, certificate=None)
    over["answer"]["flows"][0]["flow"] = 10
    over = reseal(over)
    assert contract_problems(over) == ["the value is at most what was asked, and feasible says whether it is all of it"]
    assert verify_result(over, nodes=nodes, edges=edges)
