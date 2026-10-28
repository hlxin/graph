"""Components and biconnectivity: the partition into weak or strong components (with their flags and edge counts),
and the articulation points, bridges and blocks of the links in scope. The oracles are independent of the engine:
networkx for components and blocks, and removal by brute force (count the pieces with the node or edge gone) for
articulation points, their splits and bridges. Certificates are checked by verify_result, and forged ones refused."""

import random

import networkx as nx
import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.tools import summarize
from oracle import RELATIONS, TYPES, expand_scope, node_id, random_graph, random_scope
from test_certificates import reseal

PARENTS = {"S": ["R"]}                                     # S is a kind of R: a scope with R covers S


def directed(nodes, edges, scope):
    """The hops in scope as a multigraph: an edge in scope forward is a hop from its source, reverse from its
    target (both, when both are)."""
    pairs = {(s["relation"], s["direction"]) for s in expand_scope(scope, PARENTS, set(RELATIONS))}
    g = nx.MultiDiGraph()
    g.add_nodes_from(node_id(n) for n in nodes)
    for e in edges:
        if (e["relation"], "forward") in pairs:
            g.add_edge(e["source"], e["target"], key=e["id"])
        if (e["relation"], "reverse") in pairs:
            g.add_edge(e["target"], e["source"], key=e["id"])
    return g


def linked(edges, scope):
    pairs = {(s["relation"], s["direction"]) for s in expand_scope(scope, PARENTS, set(RELATIONS))}
    return [e for e in edges if (e["relation"], "forward") in pairs or (e["relation"], "reverse") in pairs]


def pieces(ids, links, without=None):
    g = nx.MultiGraph()
    g.add_nodes_from(n for n in ids if n != without)
    g.add_edges_from((e["source"], e["target"]) for e in links if without not in (e["source"], e["target"]))
    return nx.number_connected_components(g)


def cases(rng, n, **graph):
    for _ in range(n):
        nodes, edges = random_graph(rng, **graph)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES,
                             relation_parents=PARENTS)
        yield nodes, edges, p, [node_id(x) for x in nodes]


def check(res, nodes, edges):
    assert verify_result(res, nodes=nodes, edges=edges, relation_parents=PARENTS) == []
    assert Result.from_dict(res.to_dict()).result_id == res.result_id


def test_components_match_networkx():
    rng = random.Random("components")
    listed = loops = 0
    for nodes, edges, p, ids in cases(rng, 400, max_nodes=10, max_edges=24):
        scope = random_scope(rng)
        g = directed(nodes, edges, scope)
        for kind in ("weak", "strong"):
            named = rng.choice([None, rng.sample(ids, rng.randint(1, min(3, len(ids))))])
            size = rng.choice([1, 1, 2, 3])
            res = execute(p, p.request("components", scope=scope, connectivity=kind, nodes=named, min_size=size,
                                       certificate=rng.random() < 0.7))
            assert res.status == COMPLETED, res.error
            check(res, nodes, edges)
            parts = [sorted(c) for c in (nx.weakly_connected_components(g) if kind == "weak"
                                         else nx.strongly_connected_components(g))]
            chosen = [c for c in parts if named is None or set(named) & set(c)]
            want = sorted(c for c in chosen if len(c) >= size)
            got = res.answer["components"]
            assert [list(c["nodes"]) for c in got] == want, (kind, scope, named, size)
            assert res.answer["total"] == len(chosen) and res.answer["count"] == len(want)
            for c in got:
                member = set(c["nodes"])
                inside = {k for u, v, k in g.edges(keys=True) if u in member and v in member}
                assert c["edges"] == len(inside)
                if kind == "strong":
                    assert c["cyclic"] == (len(member) > 1 or any(g.has_edge(u, u) for u in member))
                    assert c["initial"] == all(u in member for v in member for u in g.predecessors(v))
                    assert c["terminal"] == all(v in member for u in member for v in g.successors(u))
            listed += len(got)
            loops += sum(1 for c in got if c.get("cyclic") and c["size"] > 1)
    assert listed > 800 and loops > 100, (listed, loops)


def test_biconnectivity_matches_removal():
    rng = random.Random("biconnectivity")
    found = {"points": 0, "bridges": 0, "blocks": 0}
    for nodes, edges, p, ids in cases(rng, 400, max_nodes=10, max_edges=18):
        scope = random_scope(rng)
        links = linked(edges, scope)
        named = rng.choice([None, None, rng.sample(ids, 1)])
        res = execute(p, p.request("biconnectivity", scope=scope, nodes=named, blocks=True,
                                   certificate=rng.random() < 0.7))
        assert res.status == COMPLETED, res.error
        check(res, nodes, edges)
        full = nx.MultiGraph()
        full.add_nodes_from(ids)
        full.add_edges_from((e["source"], e["target"]) for e in links)
        region = set(ids) if named is None else nx.node_connected_component(full, named[0])
        base = pieces(ids, links)
        points = {}
        for v in sorted(region):
            k = pieces(ids, links, without=v) - base + 1
            if k >= 2:
                points[v] = k
        bridges = sorted(e["id"] for e in links if e["source"] != e["target"] and e["source"] in region
                         and pieces(ids, [f for f in links if f is not e]) > base)
        assert {x["node"]: x["splits"] for x in res.answer["articulation_points"]} == points
        assert [b["edge"] for b in res.answer["bridges"]] == bridges
        simple = nx.Graph()
        simple.add_edges_from((e["source"], e["target"]) for e in links if e["source"] != e["target"])
        owner = {}
        for i, block in enumerate(nx.biconnected_component_edges(simple)):
            for u, v in block:
                owner[frozenset((u, v))] = i
        grouped = {}
        for e in links:
            if e["source"] != e["target"] and e["source"] in region:
                grouped.setdefault(owner[frozenset((e["source"], e["target"]))], []).append(e["id"])
        assert sorted(list(b["edges"]) for b in res.answer["blocks"]) == sorted(sorted(b) for b in grouped.values())
        assert res.answer["components"] == (nx.number_connected_components(full) if named is None else 1)
        found["points"] += len(points)
        found["bridges"] += len(bridges)
        found["blocks"] += len(grouped)
    assert min(found.values()) > 300, found


def test_an_interrupted_search_lists_only_what_it_finished():
    rng = random.Random("structure-limits")
    stopped = 0
    for nodes, edges, p, ids in cases(rng, 300, max_nodes=10, max_edges=20):
        scope = random_scope(rng)
        limits = {"max_expansions": rng.randint(0, 10)}
        for kind in ("weak", "strong"):
            req = dict(scope=scope, connectivity=kind, certificate=True, min_size=rng.choice([1, 2]))
            full = execute(p, p.request("components", **req))
            part = execute(p, p.request("components", **req, limits={**limits, "max_results": rng.randint(0, 4)}))
            check(part, nodes, edges)
            whole = [c["nodes"] for c in full.answer["components"]]
            assert all(c["nodes"] in whole for c in part.answer["components"])
            if part.status == INTERRUPTED:
                stopped += 1
                assert part.answer["total"] is None
        req = dict(scope=scope, blocks=True, certificate=True)
        full = execute(p, p.request("biconnectivity", **req)).answer
        part = execute(p, p.request("biconnectivity", **req, limits=limits))
        check(part, nodes, edges)
        a = part.answer
        assert all(x in full["articulation_points"] for x in a["articulation_points"])
        assert all(x in full["bridges"] for x in a["bridges"]) and all(x in full["blocks"] for x in a["blocks"])
        stopped += part.status == INTERRUPTED
    assert stopped > 300


NODES = ["a", "b", "c", "d", "e", "f", "g"]
EDGES = [{"id": "e1", "source": "a", "target": "b", "relation": "R"},
         {"id": "e2", "source": "b", "target": "c", "relation": "R"},
         {"id": "e3", "source": "c", "target": "a", "relation": "R"},
         {"id": "e4", "source": "c", "target": "d", "relation": "R"},
         {"id": "e5", "source": "d", "target": "e", "relation": "R"},
         {"id": "e6", "source": "e", "target": "d", "relation": "R"},
         {"id": "e7", "source": "e", "target": "f", "relation": "R"},
         {"id": "e8", "source": "f", "target": "g", "relation": "R"}]


def forged(res, change):
    data = res.to_dict()
    change(data)
    return reseal(data)


def refused(data):
    return verify_result(data, nodes=NODES, edges=EDGES) != []


def test_forged_component_certificates_are_refused():
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("components", scope=[("R", "forward")], connectivity="strong", certificate=True))
    assert verify_result(res, nodes=NODES, edges=EDGES) == []
    assert [c["nodes"] for c in res.answer["components"]][:2] == [("a", "b", "c"), ("d", "e")]

    def swap_groups(data):                               # a group listed before one that reaches it
        g = data["answer"]["certificate"]["groups"]
        g[0], g[-1] = g[-1], g[0]
    assert refused(forged(res, swap_groups))

    def merge(data):                                     # two components claimed as one, with their trees joined
        comps = data["answer"]["components"]
        comps[1]["nodes"], comps[1]["size"] = ["d", "e", "f"], 3
        del comps[2]
        data["answer"]["count"] -= 1
        groups = data["answer"]["certificate"]["groups"]
        de = next(g for g in groups if g["nodes"] == ["d", "e"])
        de["nodes"] = ["d", "e", "f"]
        de["out"].append({"node": "f", "via": {"edge": "e7", "relation": "R", "direction": "forward", "from": "e"}})
        groups.remove(next(g for g in groups if g["nodes"] == ["f"]))
    assert refused(forged(res, merge))

    def cut_tree(data):                                  # a tree that does not span its component
        data["answer"]["certificate"]["groups"][0]["in"].pop()
    assert refused(forged(res, cut_tree))

    def wrong_flag(data):
        data["answer"]["components"][1]["terminal"] = True
    assert refused(forged(res, wrong_flag))

    weak = execute(p, p.request("components", scope=[("R", "forward")], certificate=True))
    assert verify_result(weak, nodes=NODES, edges=EDGES) == []

    def split(data):                                     # one weak component claimed as two
        a = data["answer"]
        a["components"] = [{"nodes": ["a", "b", "c"], "size": 3, "edges": 3},
                           {"nodes": ["d", "e", "f", "g"], "size": 4, "edges": 4}]
        a["count"], a["total"] = 2, 2
        tree = a["certificate"]["groups"][0]["out"]
        a["certificate"]["groups"] = [{"nodes": ["a", "b", "c"], "out": [t for t in tree if t["node"] in "bc"],
                                       "in": None},
                                      {"nodes": ["d", "e", "f", "g"], "out": [t for t in tree if t["node"] in "efg"],
                                       "in": None}]
    assert refused(forged(weak, split))


def test_unlisted_groups_are_proven_too():
    """A group left out by min_size still counts toward total, so it needs its own trees: two isolated nodes cannot be
    passed off as one component of two."""
    nodes, edges = ["a", "b"], []
    p = Projection.build(nodes=nodes, edges=edges, relations=["R"])
    for connectivity in ("weak", "strong"):
        res = execute(p, p.request("components", scope=[("R", "forward")], connectivity=connectivity, min_size=3,
                                   certificate=True))
        assert res.answer["total"] == 2 and verify_result(res, nodes=nodes, edges=edges) == []

        def merge(data):
            data["answer"]["total"] = 1
            data["answer"]["certificate"]["groups"] = [{"nodes": ["a", "b"], "out": [],
                                                        "in": [] if connectivity == "strong" else None}]
        assert verify_result(forged(res, merge), nodes=nodes, edges=edges) != []


def test_a_forest_grows_only_from_named_nodes():
    """With nodes named, a certificate for the whole graph does not prove claims about other components."""
    nodes = ["a", "b", "c", "d"]
    edges = [{"id": "x1", "source": "b", "target": "c", "relation": "R"},
             {"id": "x2", "source": "c", "target": "d", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    mine = execute(p, p.request("biconnectivity", scope=[("R", "forward")], nodes=["a"], certificate=True))
    assert mine.answer["articulation_points"] == () and mine.answer["components"] == 1
    whole = execute(p, p.request("biconnectivity", scope=[("R", "forward")], certificate=True)).to_dict()["answer"]
    assert [x["node"] for x in whole["articulation_points"]] == ["c"]

    def swap(data):
        data["answer"] = whole
    assert verify_result(forged(mine, swap), nodes=nodes, edges=edges) != []


def test_weak_and_strong_requests_are_different_requests():
    """A request's identity digests its fields with ``kind`` naming the record kind, so no operation may take a field
    of that name: weak and strong components must not share a request_id (nor a session's cached answer)."""
    from graph_runtime.request import _PARAMS
    from graph_runtime.tools import GraphToolkit
    assert all("kind" not in required + optional for required, optional in _PARAMS.values())
    kit = GraphToolkit()
    built = kit.call("graph_projection", {"nodes": ["a", "b"], "edges": [
        {"id": "x", "source": "a", "target": "b", "relation": "R"}]})
    answers = {}
    for connectivity in ("weak", "strong"):
        res = kit.call("graph_execute", {"request": {"operation": "components", "projection": built["identity"],
                                                     "connectivity": connectivity, "scope": [["R", "forward"]]}})
        answers[connectivity] = (res["request_id"], [c["nodes"] for c in res["answer"]["components"]])
    assert answers["weak"][0] != answers["strong"][0]
    assert answers["weak"][1] == [["a", "b"]] and answers["strong"][1] == [["a"], ["b"]]


def test_forged_dfs_forests_are_refused():
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("biconnectivity", scope=[("R", "forward")], blocks=True, certificate=True))
    assert verify_result(res, nodes=NODES, edges=EDGES) == []
    assert {x["node"]: x["splits"] for x in res.answer["articulation_points"]} == {"c": 2, "d": 2, "e": 2, "f": 2}
    assert [b["edge"] for b in res.answer["bridges"]] == ["e4", "e7", "e8"]

    def claim(data):                                     # an articulation point the forest does not prove
        data["answer"]["articulation_points"].insert(0, {"node": "a", "splits": 2})
    assert refused(forged(res, claim))

    def drop(data):                                      # a complete answer that leaves a bridge out
        data["answer"]["bridges"].pop()
    assert refused(forged(res, drop))

    def renumber(data):                                  # numbers that are no preorder of the forest
        forest = data["answer"]["certificate"]["forest"]
        forest[0]["pre"], forest[1]["pre"] = forest[1]["pre"], forest[0]["pre"]
    assert refused(forged(res, renumber))

    def rehang(data):                                    # a node hung from an edge that is not at it
        forest = data["answer"]["certificate"]["forest"]
        next(f for f in forest if f["node"] == "g")["via"] = "e1"
    assert refused(forged(res, rehang))


def test_terminal_classes_and_circular_support():
    """A reaction network's terminal strong linkage classes (deficiency theory), and a circular derivation, as an
    adapter would ask for them."""
    nodes = ["A", "B", "C", "D", "X", "Y"]
    edges = [{"id": "r1", "source": "A", "target": "B", "relation": "REACTS_TO"},
             {"id": "r2", "source": "B", "target": "A", "relation": "REACTS_TO"},
             {"id": "r3", "source": "B", "target": "C", "relation": "REACTS_TO"},
             {"id": "r4", "source": "C", "target": "D", "relation": "REACTS_TO"},
             {"id": "r5", "source": "D", "target": "C", "relation": "REACTS_TO"},
             {"id": "d1", "source": "X", "target": "Y", "relation": "DERIVED_FROM"},
             {"id": "d2", "source": "Y", "target": "X", "relation": "DERIVED_FROM"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("components", connectivity="strong", scope=[("REACTS_TO", "forward")], min_size=2))
    assert [(c["nodes"], c["terminal"]) for c in res.answer["components"]] == [(("A", "B"), False),
                                                                               (("C", "D"), True)]
    weak = execute(p, p.request("components", scope=[("REACTS_TO", "forward")], min_size=2))
    assert weak.answer["count"] == 1                     # one linkage class, two strong ones: not weakly reversible
    loops = execute(p, p.request("components", connectivity="strong", scope=[("DERIVED_FROM", "forward")], min_size=2))
    assert [c["nodes"] for c in loops.answer["components"]] == [("X", "Y")]


def test_summaries_and_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    p = Projection.build(nodes=NODES, edges=EDGES)
    for limits in ({}, {"max_expansions": 3}, {"max_results": 1}):
        for kind in ("weak", "strong"):
            res = execute(p, p.request("components", scope=[("R", "forward")], connectivity=kind, certificate=True,
                                       limits=limits))
            assert list(validator.iter_errors(res.to_dict())) == []
            assert summarize(res)["summary"]["count"] == res.answer["count"]
        res = execute(p, p.request("biconnectivity", scope=[("R", "forward")], blocks=True, certificate=True,
                                   limits={k: v for k, v in limits.items() if k == "max_expansions"}))
        assert list(validator.iter_errors(res.to_dict())) == []
        assert summarize(res)["summary"]["bridges"] == len(res.answer["bridges"])


@pytest.mark.parametrize("extra", [{"connectivity": "both"}, {"min_size": 0}, {"min_size": True}, {"nodes": []},
                                   {"certificate": "yes"}])
def test_requests_are_checked(extra):
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, {"operation": "components", "projection": p.identity, "scope": [("R", "forward")], **extra})
    assert res.status == "failed" and res.error["code"] == "invalid_request"
