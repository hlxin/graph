"""Maximum flows and matchings. The oracles are networkx's: a maximum flow on the same network (nodes with a capacity
split in two, a super source and sink), the cut nearest the sources from its residual graph, and a maximum bipartite
matching. Every result is checked by verify_result, interrupted ones too, and forged flows, cuts and covers are
refused."""

import random
from fractions import Fraction

import networkx as nx
import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.tools import summarize
from oracle import RELATIONS, TYPES, expand_scope, node_id, random_graph, random_scope
from test_certificates import reseal

BIG = 10 ** 9


def network(nodes, edges, scope, caps, node_cap, sources, targets):
    """The flow network as networkx sees it: a node with a capacity is ("in", v) -> ("out", v); every hop in scope
    an arc of its edge's capacity (parallel arcs added together); "S" and "T" joined to the sources and targets."""
    pairs = {(s["relation"], s["direction"]) for s in expand_scope(scope, {}, set(RELATIONS))}
    terminals = set(sources) | set(targets)
    g = nx.DiGraph()
    g.add_nodes_from(["S", "T"])

    def ends(v):
        return (("in", v), ("out", v)) if node_cap is not None and v not in terminals else (("in", v), ("in", v))

    def add(u, v, c):
        if g.has_edge(u, v):
            g[u][v]["capacity"] += c
        else:
            g.add_edge(u, v, capacity=c)
    for n in nodes:
        a, b = ends(node_id(n))
        if a != b:
            add(a, b, node_cap)
    for e in edges:
        if e["source"] == e["target"]:
            continue
        c = caps(e)
        if (e["relation"], "forward") in pairs:
            add(ends(e["source"])[1], ends(e["target"])[0], c)
        if (e["relation"], "reverse") in pairs:
            add(ends(e["target"])[1], ends(e["source"])[0], c)
    for s in sources:
        add("S", ends(s)[0], BIG)
    for t in targets:
        add(ends(t)[1], "T", BIG)
    return g


def nearest_cut(g):
    """The source side of the minimum cut nearest the sources: what the residual graph of a maximum flow reaches."""
    r = nx.algorithms.flow.edmonds_karp(g, "S", "T")
    seen, todo = {"S"}, ["S"]
    while todo:
        u = todo.pop()
        for v in r[u]:
            if v not in seen and r[u][v]["capacity"] - r[u][v]["flow"] > 0:
                seen.add(v)
                todo.append(v)
    return r.graph["flow_value"], seen


def flow_cases(rng, n, **graph):
    for _ in range(n):
        nodes, edges = random_graph(rng, **graph)
        ids = [node_id(x) for x in nodes]
        if len(ids) < 2:
            continue
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        picks = rng.sample(ids, rng.randint(2, min(4, len(ids))))
        half = rng.randint(1, len(picks) - 1)
        table = rng.choice([None, "edge", "relation", "float"])
        if table == "edge":
            capacities = {"by_edge": {e["id"]: rng.randint(0, 4) for e in edges}}
        elif table == "relation":
            capacities = {"by_relation": {r: rng.randint(1, 3) for r in RELATIONS}}
        elif table == "float":
            capacities = {"by_edge": {e["id"]: rng.choice([0.5, 1.25, 2, 0.75]) for e in edges}}
        else:
            capacities = None
        raw = {"operation": "max_flow", "sources": sorted(picks[:half]), "targets": sorted(picks[half:]),
               "scope": random_scope(rng), "capacities": capacities,
               "node_capacity": rng.choice([None, None, 1, 2, 0.5]), "certificate": rng.random() < 0.7}
        yield nodes, edges, p, raw


def capacity_of(raw):
    table = raw["capacities"]
    if table is None:
        return lambda e: Fraction(1)
    (kind, values), = table.items()
    return lambda e: Fraction(values[e["relation"] if kind == "by_relation" else e["id"]])


def exact(entry, field):
    x = entry.get(f"{field}_exact")
    return Fraction(x) if x is not None else Fraction(entry[field])


def test_max_flow_matches_networkx():
    rng = random.Random("max-flow")
    cuts = 0
    for nodes, edges, p, raw in flow_cases(rng, 500, max_nodes=9, max_edges=24):
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        cap = capacity_of(raw)
        scale = 4                                             # every capacity here is a multiple of 1/4
        node_cap = None if raw["node_capacity"] is None else int(Fraction(raw["node_capacity"]) * scale)
        g = network(nodes, edges, raw["scope"], lambda e: int(cap(e) * scale), node_cap, raw["sources"],
                    raw["targets"])
        value, side = nearest_cut(g)
        assert exact(res.answer, "value") == Fraction(value, scale), raw
        on = {v for kind, v in (x for x in side if x not in ("S", "T")) if kind == "in"}
        cut_nodes = sorted(v for kind, v in (x for x in side if x not in ("S", "T"))
                           if kind == "in" and ("out", v) not in side and g.has_node(("out", v)))
        assert [x["node"] for x in res.answer["cut_nodes"]] == cut_nodes
        pairs = {(s["relation"], s["direction"]) for s in expand_scope(raw["scope"], {}, set(RELATIONS))}

        def outward(v):
            return ("out", v) if g.has_node(("out", v)) else ("in", v)
        want = sorted((e["id"], d) for e in edges for d, (u, v) in (("forward", (e["source"], e["target"])),
                                                                      ("reverse", (e["target"], e["source"])))
                      if (e["relation"], d) in pairs and u != v and cap(e) and outward(u) in side
                      and ("in", v) not in side)
        crossing = [(h["edge"], h["direction"]) for h in res.answer["cut"]]
        assert crossing == want and all(h["from"] in on and h["to"] not in on for h in res.answer["cut"])
        cuts += bool(crossing or cut_nodes)
    assert cuts > 150


def test_an_interrupted_flow_is_a_valid_lower_bound():
    rng = random.Random("max-flow-limits")
    stopped = 0
    for nodes, edges, p, raw in flow_cases(rng, 400, max_nodes=9, max_edges=24):
        full = execute(p, {**raw, "projection": p.identity})
        part = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 12)}})
        assert verify_result(part, nodes=nodes, edges=edges) == []
        if part.status == INTERRUPTED:
            stopped += 1
            best = exact(full.answer, "value")
            assert exact(part.answer, "value") <= best <= exact(part.answer, "upper_bound")
            assert part.answer["cut"] is None and part.answer["optimal"] is False
    assert stopped > 100


def test_matching_matches_networkx():
    rng = random.Random("matching")
    deficient = 0
    for _ in range(500):
        nodes, edges = random_graph(rng, max_nodes=10, max_edges=22)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(x) for x in nodes]
        left = rng.choice([{"node_types": rng.sample(TYPES, 1)}, {"nodes": rng.sample(ids, rng.randint(1, len(ids)))}])
        scope = random_scope(rng)
        res = execute(p, p.request("matching", left=left, scope=scope, certificate=True))
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == []
        types = {node_id(x): set() if isinstance(x, str) else set(x["types"]) for x in nodes}
        chosen = {n for n in ids if ("nodes" not in left or n in left["nodes"]) and
                  ("node_types" not in left or types[n] & set(left["node_types"]))}
        pairs = {(s["relation"], s["direction"]) for s in scope}
        b = nx.Graph()
        b.add_nodes_from(("L", u) for u in chosen)
        for e in edges:
            for d, (u, v) in (("forward", (e["source"], e["target"])), ("reverse", (e["target"], e["source"]))):
                if (e["relation"], d) in pairs and u in chosen and v not in chosen:
                    b.add_edge(("L", u), ("R", v))
        size = len(nx.bipartite.maximum_matching(b, top_nodes=[("L", u) for u in chosen])) // 2
        assert res.answer["size"] == size and res.answer["left_count"] == len(chosen)
        assert len(res.answer["certificate"]["nodes"]) == size
        deficient += res.answer["deficiency"] > 0
    assert deficient > 100


NODES = ["a", "b", "c", "d", "s", "t"]
EDGES = [{"id": "p1", "source": "s", "target": "a", "relation": "P"},
         {"id": "p2", "source": "s", "target": "b", "relation": "P"},
         {"id": "p3", "source": "a", "target": "c", "relation": "P"},
         {"id": "p4", "source": "b", "target": "c", "relation": "P"},
         {"id": "p5", "source": "c", "target": "t", "relation": "P"},
         {"id": "p6", "source": "b", "target": "d", "relation": "P"},
         {"id": "p7", "source": "d", "target": "t", "relation": "P"}]


def forged(res, change):
    data = res.to_dict()
    change(data)
    return reseal(data)


def refused(data):
    return verify_result(data, nodes=NODES, edges=EDGES) != []


def test_independent_lines_of_evidence_and_forgeries():
    """Two node-disjoint lines join s to t (through c, and through d), though three edge routes leave s: the node
    capacity finds the bottleneck at c."""
    p = Projection.build(nodes=NODES, edges=EDGES)
    edge_paths = execute(p, p.request("max_flow", sources=["s"], targets=["t"], scope=[("P", "forward")]))
    lines = execute(p, p.request("max_flow", sources=["s"], targets=["t"], scope=[("P", "forward")],
                                 node_capacity=1, certificate=True))
    assert edge_paths.answer["value"] == 2 and lines.answer["value"] == 2
    assert verify_result(lines, nodes=NODES, edges=EDGES) == []

    def inflate(data):                                   # a value the flow does not carry
        data["answer"]["value"] = data["answer"]["upper_bound"] = 3
    assert refused(forged(lines, inflate))

    def overflow(data):                                  # more through an edge than it holds
        data["answer"]["flows"][0]["flow"] = 2
    assert refused(forged(lines, overflow))

    def shrink(data):                                    # a cut that leaves a hop out
        a = data["answer"]
        dropped = (a["cut"] or a["cut_nodes"]).pop()
        a["value"] = a["upper_bound"] = a["value"] - dropped["capacity"]
    assert refused(forged(lines, shrink))

    def move(data):                                      # a source side that is not the cut's
        data["answer"]["certificate"]["source_side"].append("a")
    assert refused(forged(lines, move))

    stopped = execute(p, p.request("max_flow", sources=["s"], targets=["t"], scope=[("P", "forward")],
                                   limits={"max_expansions": 0}))
    assert stopped.status == INTERRUPTED and verify_result(stopped, nodes=NODES, edges=EDGES) == []

    def deny(data):                                      # an interrupted answer that bounds the flow at nothing
        data["answer"]["upper_bound"] = 0
    assert refused(forged(stopped, deny))


def test_forged_covers_are_refused():
    nodes = [{"id": "x", "types": ["A"]}, {"id": "y", "types": ["A"]}, {"id": "u", "types": ["B"]},
             {"id": "v", "types": ["B"]}]
    edges = [{"id": "m1", "source": "x", "target": "u", "relation": "R"},
             {"id": "m2", "source": "y", "target": "u", "relation": "R"},
             {"id": "m3", "source": "y", "target": "v", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("matching", left={"node_types": ["A"]}, scope=[("R", "forward")], certificate=True))
    assert res.answer["size"] == 2 and verify_result(res, nodes=nodes, edges=edges) == []

    def short(data):                                     # a cover that misses a candidate hop
        data["answer"]["certificate"]["nodes"] = ["u", "x"]
    assert verify_result(forged(res, short), nodes=nodes, edges=edges) != []

    def twice(data):                                     # one partner in two pairs
        data["answer"]["pairs"][1]["right"] = "u"
        data["answer"]["pairs"][1]["edge"] = "m2"
    assert verify_result(forged(res, twice), nodes=nodes, edges=edges) != []


def test_summaries_and_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    p = Projection.build(nodes=NODES, edges=EDGES)
    for limits in ({}, {"max_expansions": 2}):
        for extra in ({}, {"capacities": {"by_relation": {"P": 0.5}}, "node_capacity": 1}):
            res = execute(p, p.request("max_flow", sources=["s"], targets=["t"], scope=[("P", "forward")],
                                       certificate=True, limits=limits, **extra))
            assert list(validator.iter_errors(res.to_dict())) == []
            assert summarize(res)["summary"]["optimal"] is res.complete
        res = execute(p, p.request("matching", left={"nodes": ["s", "a", "b"]}, scope=[("P", "forward")],
                                   certificate=True, limits=limits))
        assert list(validator.iter_errors(res.to_dict())) == []
        assert summarize(res)["summary"]["size"] == res.answer["size"]


@pytest.mark.parametrize("extra", [{"targets": ["s"]}, {"capacities": {"by_edge": {"p1": -1}}},
                                   {"capacities": {"by_edge": {"p1": float("inf")}}}, {"node_capacity": -2},
                                   {"node_capacity": True}, {"capacities": {"by_kind": {}}}])
def test_flow_requests_are_checked(extra):
    p = Projection.build(nodes=NODES, edges=EDGES)
    raw = {"operation": "max_flow", "projection": p.identity, "sources": ["s"], "targets": ["t"],
           "scope": [("P", "forward")], **extra}
    res = execute(p, raw)
    assert res.status == "failed" and res.error["code"] in ("invalid_request", "invalid_cost")
