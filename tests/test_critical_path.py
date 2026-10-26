"""Critical paths: the makespan of an acyclic graph, a critical path, every node's earliest and latest time and slack,
or a cycle. The oracles: networkx's longest path length of a DAG and its acyclicity test, and the earliest and latest
times by brute force (the longest path to and from each node, by enumerating paths). Certificates are checked by
verify_result, and forged orders and times refused."""

import random
from fractions import Fraction

import networkx as nx
import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.tools import summarize
from test_certificates import reseal


def graph(rng, n, acyclic):
    nodes = [f"v{i:02d}" for i in range(n)]
    edges = []
    for k in range(rng.randint(0, 2 * n)):
        a, b = rng.choice(nodes), rng.choice(nodes)
        if acyclic:
            if a == b:
                continue
            a, b = min(a, b), max(a, b)
        edges.append({"id": f"e{k:02d}", "source": a, "target": b, "relation": rng.choice(["R", "S"])})
    return nodes, edges


def exact(entry, field):
    x = entry.get(f"{field}_exact")
    return Fraction(x) if x is not None else Fraction(entry[field])


def test_critical_paths_match_networkx_and_brute_force():
    rng = random.Random("critical-path")
    cyclic = 0
    for _ in range(500):
        acyc = rng.random() < 0.7
        nodes, edges = graph(rng, rng.randint(1, 10), acyc)
        p = Projection.build(nodes=nodes, edges=edges, relations=["R", "S"])
        scope = rng.choice([[("R", "forward")], [("R", "forward"), ("S", "forward")]])
        costs = rng.choice([None, {"by_edge": {e["id"]: rng.choice([0, 1, 2, 5, 0.5]) for e in edges}},
                            {"by_relation": {"R": 2, "S": 3}}])
        sources = rng.choice([None, rng.sample(nodes, 1)])
        res = execute(p, p.request("critical_path", scope=scope, costs=costs, sources=sources, answer="schedule",
                                   certificate=rng.random() < 0.8))
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        rels = {r for r, _ in scope}
        g = nx.MultiDiGraph()
        g.add_nodes_from(nodes)
        for e in edges:
            if e["relation"] in rels:
                c = 1 if costs is None else Fraction(costs["by_edge"][e["id"]] if "by_edge" in costs
                                                     else costs["by_relation"][e["relation"]])
                g.add_edge(e["source"], e["target"], w=c)
        if sources is not None:
            g = g.subgraph(set(sources) | set().union(*(nx.descendants(g, s) for s in sources))).copy()
        if not nx.is_directed_acyclic_graph(g):
            cyclic += 1
            assert res.answer["acyclic"] is False
            continue
        order = list(nx.topological_sort(g))
        early = {}
        for v in order:
            early[v] = max((early[u] + d["w"] for u, _, d in g.in_edges(v, data=True)), default=Fraction(0))
        span = max(early.values(), default=Fraction(0))
        late = {}
        for v in reversed(order):
            late[v] = min((late[w] - d["w"] for _, w, d in g.out_edges(v, data=True)), default=span)
        assert exact(res.answer, "cost") == span
        got = {e["node"]: (exact(e, "earliest"), exact(e, "latest"), exact(e, "slack")) for e in res.answer["schedule"]}
        assert got == {v: (early[v], late[v], late[v] - early[v]) for v in g}
    assert cyclic > 60


def test_an_interrupted_search_claims_nothing():
    rng = random.Random("critical-path-limits")
    stopped = 0
    for _ in range(200):
        nodes, edges = graph(rng, rng.randint(2, 10), rng.random() < 0.7)
        p = Projection.build(nodes=nodes, edges=edges, relations=["R", "S"])
        res = execute(p, p.request("critical_path", scope=[("R", "forward"), ("S", "forward")], certificate=True,
                                   limits={"max_expansions": rng.randint(0, 6)}))
        assert verify_result(res, nodes=nodes, edges=edges) == []
        if res.status == INTERRUPTED:
            stopped += 1
            assert all(v is None for v in res.answer.values())
    assert stopped > 50


NODES = ["a", "b", "c", "d", "e", "f"]
EDGES = [{"id": "k1", "source": "a", "target": "b", "relation": "BEFORE"},
         {"id": "k2", "source": "a", "target": "c", "relation": "BEFORE"},
         {"id": "k3", "source": "b", "target": "d", "relation": "BEFORE"},
         {"id": "k4", "source": "c", "target": "d", "relation": "BEFORE"},
         {"id": "k5", "source": "d", "target": "e", "relation": "BEFORE"},
         {"id": "k6", "source": "c", "target": "e", "relation": "BEFORE"}]
COSTS = {"by_edge": {"k1": 3, "k2": 2, "k3": 4, "k4": 4, "k5": 1, "k6": 2}}


def forged(res, change):
    data = res.to_dict()
    change(data)
    return reseal(data)


def test_forged_orders_and_times_are_refused():
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("critical_path", scope=[("BEFORE", "forward")], costs=COSTS, answer="schedule",
                               certificate=True))
    assert res.answer["cost"] == 8 and verify_result(res, nodes=NODES, edges=EDGES) == []

    def shorter(data):                                   # a makespan of 7 along a path that skips k3's weight
        a = data["answer"]
        a["cost"] = 7
        for e in a["certificate"]["order"]:
            if e["node"] == "d":
                e["earliest"] = 6
            if e["node"] == "e":
                e["earliest"] = 7
        a["path"] = {"source": "a", "target": "e", "length": 3, "hops": [
            {"edge": "k2", "relation": "BEFORE", "direction": "forward", "from": "a", "to": "c", "cost": 2},
            {"edge": "k4", "relation": "BEFORE", "direction": "forward", "from": "c", "to": "d", "cost": 4},
            {"edge": "k5", "relation": "BEFORE", "direction": "forward", "from": "d", "to": "e", "cost": 1}]}
        for e in a["schedule"]:
            row = {"a": (0, 0), "b": (3, 3), "c": (2, 2), "d": (6, 6), "e": (7, 7), "f": (0, 7)}[e["node"]]
            e["earliest"], e["latest"], e["slack"] = row[0], row[1], row[1] - row[0]
    assert verify_result(forged(res, shorter), nodes=NODES, edges=EDGES) != []

    def backwards(data):                                 # an order with a hop going back
        order = data["answer"]["certificate"]["order"]
        order[1], order[3] = order[3], order[1]
    assert verify_result(forged(res, backwards), nodes=NODES, edges=EDGES) != []

    cyc = Projection.build(nodes=NODES, edges=EDGES + [{"id": "k7", "source": "e", "target": "a", "relation": "BEFORE"}])
    loop = execute(cyc, cyc.request("critical_path", scope=[("BEFORE", "forward")]))
    assert loop.answer["acyclic"] is False and verify_result(loop, nodes=NODES, edges=EDGES + [
        {"id": "k7", "source": "e", "target": "a", "relation": "BEFORE"}]) == []

    def fake_cycle(data):                                # a cycle through an edge that does not close it
        data["answer"]["cycle"]["hops"][-1]["edge"] = "k6"
    assert verify_result(forged(loop, fake_cycle), nodes=NODES, edges=EDGES + [
        {"id": "k7", "source": "e", "target": "a", "relation": "BEFORE"}]) != []


def test_summaries_and_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    p = Projection.build(nodes=NODES, edges=EDGES)
    for extra in ({"answer": "schedule"}, {"costs": {"by_relation": {"BEFORE": 0.5}}, "answer": "schedule"},
                  {"limits": {"max_expansions": 1}}, {"scope": [("BEFORE", "forward"), ("BEFORE", "reverse")]}):
        raw = {"scope": [("BEFORE", "forward")], "certificate": True, **extra}
        res = execute(p, p.request("critical_path", **raw))
        assert list(validator.iter_errors(res.to_dict())) == []
        assert summarize(res)["summary"]["acyclic"] is res.answer["acyclic"]


def test_review_regressions():
    """Exact fields an integer answer must not carry; a path from outside what the sources reach; cancellation and
    deadlines on a graph where no node is ever ready (a ring)."""
    from graph_runtime.result import contract_problems
    nodes = ["a", "b", "c"]
    chain = [{"id": "x1", "source": "a", "target": "b", "relation": "R"},
             {"id": "x2", "source": "b", "target": "c", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=chain)
    res = execute(p, p.request("critical_path", scope=[("R", "forward")], answer="schedule", certificate=True))
    assert res.answer["cost"] == 2

    def zero(data):                                      # zeros beside the true values in *_exact
        a = data["answer"]
        a["cost"], a["cost_exact"] = 0, "2"
        a["path"] = {"source": "c", "target": "c", "length": 0, "hops": []}
        for e in a["schedule"] + a["certificate"]["order"]:
            for f in ("earliest", "latest", "slack"):
                if f in e:
                    e[f"{f}_exact"], e[f] = str(e[f]), 0
    bad = forged(res, zero)
    assert contract_problems(bad) and verify_result(bad, nodes=nodes, edges=chain)

    edges = [{"id": "y1", "source": "s", "target": "t", "relation": "R"},
             {"id": "y2", "source": "x", "target": "t", "relation": "R"}]
    q = Projection.build(nodes=["s", "t", "x"], edges=edges)
    ok = execute(q, q.request("critical_path", scope=[("R", "forward")], sources=["s"], certificate=True))
    assert verify_result(ok, nodes=["s", "t", "x"], edges=edges) == []

    def outside(data):                                   # the same cost from x, which s does not reach
        data["answer"]["path"] = {"source": "x", "target": "t", "length": 1, "hops": [
            {"edge": "y2", "relation": "R", "direction": "forward", "from": "x", "to": "t"}]}
    assert verify_result(forged(ok, outside), nodes=["s", "t", "x"], edges=edges)

    ring = Projection.build(nodes=["a", "b"], edges=[{"id": "r1", "source": "a", "target": "b", "relation": "R"},
                                                     {"id": "r2", "source": "b", "target": "a", "relation": "R"}])
    req = ring.request("critical_path", scope=[("R", "forward")])
    assert execute(ring, req, cancel=lambda: True).status == INTERRUPTED
    assert execute(ring, req, timeout=0).status == INTERRUPTED
