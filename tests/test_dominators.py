"""Dominators: the nodes every walk from the sources passes through. The oracle is networkx's immediate_dominators
on the same hops with a start joined to every source; certificates (low-high orders) are checked by verify_result,
and forged trees and orders are refused."""

import random

import networkx as nx
import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.tools import summarize
from oracle import RELATIONS, TYPES, expand_scope, node_id, random_graph, random_scope
from test_certificates import reseal

START = ("start",)


def oracle(nodes, edges, scope, sources):
    pairs = {(s["relation"], s["direction"]) for s in expand_scope(scope, {}, set(RELATIONS))}
    g = nx.DiGraph()
    g.add_node(START)
    for s in sources:
        g.add_edge(START, s)
    for e in edges:
        if e["source"] == e["target"]:
            continue
        if (e["relation"], "forward") in pairs:
            g.add_edge(e["source"], e["target"])
        if (e["relation"], "reverse") in pairs:
            g.add_edge(e["target"], e["source"])
    return {v: (None if d == START else d) for v, d in nx.immediate_dominators(g, START).items() if v != START}


def test_dominators_match_networkx():
    rng = random.Random("dominators")
    deep = 0
    for _ in range(600):
        nodes, edges = random_graph(rng, max_nodes=12, max_edges=30)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(x) for x in nodes]
        sources = sorted(rng.sample(ids, rng.randint(1, min(3, len(ids)))))
        scope = random_scope(rng)
        want = oracle(nodes, edges, scope, sources)
        targets = rng.choice([None, rng.sample(ids, rng.randint(1, len(ids)))])
        res = execute(p, p.request("dominators", sources=sources, scope=scope, targets=targets,
                                   certificate=rng.random() < 0.8))
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        assert res.answer["reached"] == len(want)
        if targets is None:
            assert {e["node"]: e["idom"] for e in res.answer["tree"]} == want
        else:
            for c in res.answer["chains"]:
                chain, k = [], want.get(c["target"])
                while k is not None:
                    chain.append(k)
                    k = want[k]
                assert c["reachable"] is (c["target"] in want) and list(c["dominators"]) == chain[::-1]
        deep += sum(1 for d in want.values() if d is not None and want.get(d) is not None)
    assert deep > 200


def test_low_high_orders_on_larger_graphs():
    rng = random.Random("low-high")
    for _ in range(60):
        ids = [f"v{i:03d}" for i in range(rng.randint(20, 120))]
        nodes = ids
        edges = [{"id": f"x{k:04d}", "source": rng.choice(ids), "target": rng.choice(ids),
                  "relation": rng.choice(RELATIONS)} for k in range(rng.randint(len(ids), 4 * len(ids)))]
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        sources = sorted(rng.sample(ids, rng.randint(1, min(3, len(ids)))))
        scope = random_scope(rng)
        res = execute(p, p.request("dominators", sources=sources, scope=scope, certificate=True))
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert {e["node"]: e["idom"] for e in res.answer["tree"]} == oracle(nodes, edges, scope, sources)


def test_an_interrupted_search_claims_nothing():
    rng = random.Random("dominators-limits")
    stopped = 0
    for _ in range(200):
        nodes, edges = random_graph(rng, max_nodes=10, max_edges=24)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(x) for x in nodes]
        res = execute(p, p.request("dominators", sources=[ids[0]], scope=random_scope(rng), certificate=True,
                                   limits={"max_expansions": rng.randint(0, 5)}))
        assert verify_result(res, nodes=nodes, edges=edges) == []
        if res.status == INTERRUPTED:
            stopped += 1
            assert res.answer == {"reached": None, "tree": None, "chains": None, "certificate": None}
    assert stopped > 50


NODES = ["A", "B", "C", "D", "F", "P", "X", "Y"]
EDGES = [{"id": "d1", "source": "F", "target": "A", "relation": "R"},
         {"id": "d2", "source": "A", "target": "B", "relation": "R"},
         {"id": "d3", "source": "A", "target": "C", "relation": "R"},
         {"id": "d4", "source": "B", "target": "D", "relation": "R"},
         {"id": "d5", "source": "C", "target": "D", "relation": "R"},
         {"id": "d6", "source": "D", "target": "P", "relation": "R"},
         {"id": "d7", "source": "X", "target": "Y", "relation": "R"},
         {"id": "d8", "source": "P", "target": "A", "relation": "R"}]


def forged(res, change):
    data = res.to_dict()
    change(data)
    return reseal(data)


def test_unavoidable_intermediates_and_forgeries():
    """From the feed F every route to the product P forms A and then D; B and C are alternatives."""
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("dominators", sources=["F"], scope=[("R", "forward")], targets=["P", "Y"],
                               certificate=True))
    assert [(c["target"], c["reachable"], list(c["dominators"])) for c in res.answer["chains"]] == [
        ("P", True, ["F", "A", "D"]), ("Y", False, [])]
    assert verify_result(res, nodes=NODES, edges=EDGES) == []

    def shortcut(data):                                  # claim B dominates D
        order = data["answer"]["certificate"]["order"]
        next(e for e in order if e["node"] == "D")["idom"] = "B"
        data["answer"]["chains"][0]["dominators"] = ["F", "A", "B", "D"]
    assert verify_result(forged(res, shortcut), nodes=NODES, edges=EDGES) != []

    def skip(data):                                      # claim F alone dominates D: no order can prove it
        order = data["answer"]["certificate"]["order"]
        next(e for e in order if e["node"] == "D")["idom"] = "F"
        data["answer"]["chains"][0]["dominators"] = ["F", "D"]
    assert verify_result(forged(res, skip), nodes=NODES, edges=EDGES) != []

    def reorder(data):                                   # D first among A's children: no hop reaches it from before
        order = data["answer"]["certificate"]["order"]
        rank = {"F": 0, "A": 1, "D": 2, "P": 3, "B": 4, "C": 5}
        order.sort(key=lambda e: rank.get(e["node"], 9))
    assert verify_result(forged(res, reorder), nodes=NODES, edges=EDGES) != []

    def drop(data):                                      # a region that is not closed
        order = data["answer"]["certificate"]["order"]
        order[:] = [e for e in order if e["node"] != "P"]
        data["answer"]["reached"] -= 1
    assert verify_result(forged(res, drop), nodes=NODES, edges=EDGES) != []


def test_two_sources_share_no_dominator():
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("dominators", sources=["B", "F"], scope=[("R", "forward")], certificate=True))
    tree = {e["node"]: e["idom"] for e in res.answer["tree"]}
    assert tree == {"A": None, "B": None, "C": "A", "D": None, "F": None, "P": "D"}
    assert verify_result(res, nodes=NODES, edges=EDGES) == []


def test_summaries_and_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    p = Projection.build(nodes=NODES, edges=EDGES)
    for extra in ({}, {"targets": ["P", "Y"]}, {"limits": {"max_expansions": 2}}):
        res = execute(p, p.request("dominators", sources=["F"], scope=[("R", "forward")], certificate=True, **extra))
        assert list(validator.iter_errors(res.to_dict())) == []
        assert summarize(res)["summary"]["reached"] == res.answer["reached"]


def test_deep_trees_are_checked_in_linear_time_and_cycles_refused():
    """A chain of 20,000 nodes: the result is read and verified without walking shared ancestors again (a quadratic
    walk took seconds); a tree whose dominators form a cycle is refused."""
    import time
    from graph_runtime.result import contract_problems
    n = 20_000
    nodes = [f"v{i:05d}" for i in range(n)]
    edges = [{"id": f"e{i:05d}", "source": nodes[i], "target": nodes[i + 1], "relation": "R"} for i in range(n - 1)]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("dominators", sources=[nodes[0]], scope=[("R", "forward")], certificate=True))
    started = time.perf_counter()
    assert verify_result(res, nodes=nodes, edges=edges) == []
    Result.from_dict(res.to_dict())
    assert time.perf_counter() - started < 10

    small = Projection.build(nodes=NODES, edges=EDGES)
    ok = execute(small, small.request("dominators", sources=["F"], scope=[("R", "forward")]))

    def loop(data):                                      # A and B each other's dominator
        tree = data["answer"]["tree"]
        next(e for e in tree if e["node"] == "A")["idom"] = "B"
    assert contract_problems(forged(ok, loop))
