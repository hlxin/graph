"""d-separation: whether a causal graph makes x and y independent given a set. The oracle is networkx's
is_d_separator (moralized ancestral graph, an independent method); every active trail is checked by verify_result, and
every separated answer's Bayes-ball certificate; forged trails and certificates are refused."""

import random

import networkx as nx
import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.revalidate import revalidate
from graph_runtime.tools import summarize
from test_certificates import reseal


def dag(rng, n):
    nodes = [f"v{i:02d}" for i in range(n)]
    edges = []
    for k in range(rng.randint(0, 2 * n)):
        a, b = sorted(rng.sample(range(n), 2))
        edges.append({"id": f"e{k:02d}", "source": nodes[a], "target": nodes[b], "relation": "CAUSES"})
    return nodes, edges


def split(rng, nodes):
    picks = rng.sample(nodes, len(nodes))
    i, j = rng.randint(1, max(1, len(nodes) // 3)), rng.randint(1, max(1, len(nodes) // 3))
    k = rng.randint(0, max(0, len(nodes) - i - j))
    return picks[:i], picks[i:i + j], picks[i + j:i + j + k]


def test_d_separation_matches_networkx():
    rng = random.Random("d-separation")
    tally = {True: 0, False: 0}
    colliders = 0
    for _ in range(600):
        nodes, edges = dag(rng, rng.randint(3, 12))
        p = Projection.build(nodes=nodes, edges=edges, relations=["CAUSES"])
        x, y, z = split(rng, nodes)
        res = execute(p, p.request("d_separation", x=x, y=y, given=z, scope=[("CAUSES", "forward")],
                                   certificate=rng.random() < 0.8))
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        g = nx.DiGraph()
        g.add_nodes_from(nodes)
        g.add_edges_from((e["source"], e["target"]) for e in edges)
        want = nx.is_d_separator(g, set(x), set(y), set(z))
        assert res.answer["separated"] is want
        tally[want] += 1
        colliders += len(res.answer["colliders"] or [])
    assert min(tally.values()) > 150 and colliders > 20, (tally, colliders)


def test_an_interrupted_search_claims_nothing():
    rng = random.Random("d-separation-limits")
    stopped = 0
    for _ in range(200):
        nodes, edges = dag(rng, rng.randint(3, 10))
        p = Projection.build(nodes=nodes, edges=edges, relations=["CAUSES"])
        x, y, z = split(rng, nodes)
        res = execute(p, p.request("d_separation", x=x, y=y, given=z, scope=[("CAUSES", "forward")],
                                   certificate=True, limits={"max_expansions": rng.randint(0, 4)}))
        assert verify_result(res, nodes=nodes, edges=edges) == []
        if res.status == INTERRUPTED:
            stopped += 1
            assert all(v is None for v in res.answer.values())
    assert stopped > 50


NODES = ["k", "kd", "m", "x1", "x2", "x3", "y1", "y2", "y3", "z"]
EDGES = [{"id": i, "source": s, "target": t, "relation": "CAUSES"} for i, s, t in
         [("h1", "x1", "m"), ("h2", "m", "y1"), ("h3", "z", "x2"), ("h4", "z", "y2"), ("h5", "x3", "k"),
          ("h6", "y3", "k"), ("h7", "k", "kd")]]
SCOPE = [("CAUSES", "forward")]


def forged(res, change):
    data = res.to_dict()
    change(data)
    return reseal(data)


def test_forged_trails_and_certificates_are_refused():
    p = Projection.build(nodes=NODES, edges=EDGES)
    blocked = execute(p, p.request("d_separation", x=["x1"], y=["y1"], given=["m"], scope=SCOPE, certificate=True))
    assert blocked.answer["separated"] is True and verify_result(blocked, nodes=NODES, edges=EDGES) == []

    def through(data):                                   # a trail through the given middle of a chain
        a = data["answer"]
        a.update(separated=False, certificate=None, colliders=[], trail=[
            {"edge": "h1", "relation": "CAUSES", "direction": "forward", "from": "x1", "to": "m", "travel": "down"},
            {"edge": "h2", "relation": "CAUSES", "direction": "forward", "from": "m", "to": "y1", "travel": "down"}])
    assert verify_result(forged(blocked, through), nodes=NODES, edges=EDGES) != []

    collider = execute(p, p.request("d_separation", x=["x3"], y=["y3"], scope=SCOPE, certificate=True))
    assert collider.answer["separated"] is True

    def open_collider(data):                             # a collider passed with nothing given below it
        a = data["answer"]
        a.update(separated=False, certificate=None, colliders=[{"node": "k", "to_given": []}], trail=[
            {"edge": "h5", "relation": "CAUSES", "direction": "forward", "from": "x3", "to": "k", "travel": "down"},
            {"edge": "h6", "relation": "CAUSES", "direction": "forward", "from": "y3", "to": "k", "travel": "up"}])
    assert verify_result(forged(collider, open_collider), nodes=NODES, edges=EDGES) != []

    def short_states(data):                              # a certificate missing a state a move reaches
        states = data["answer"]["certificate"]["states"]
        states[:] = [s for s in states if s["node"] != "k"]
    assert verify_result(forged(collider, short_states), nodes=NODES, edges=EDGES) != []

    def short_ancestors(data):                           # ancestors not closed under parents
        data["answer"]["certificate"]["ancestors"] = []
    given = execute(p, p.request("d_separation", x=["x2"], y=["y2"], given=["z"], scope=SCOPE, certificate=True))
    assert verify_result(forged(given, short_ancestors), nodes=NODES, edges=EDGES) != []


def test_a_connected_answer_revalidates_by_its_trail():
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("d_separation", x=["x2"], y=["y2"], scope=SCOPE))
    assert res.answer["separated"] is False
    q = Projection.build(nodes=NODES, edges=EDGES + [{"id": "h8", "source": "x1", "target": "y3",
                                                      "relation": "CAUSES"}])
    assert revalidate(res, q).verdict == "holds"
    r = Projection.build(nodes=NODES, edges=[e for e in EDGES if e["id"] != "h4"])
    assert revalidate(res, r).verdict == "fails"


def test_summaries_and_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    p = Projection.build(nodes=NODES, edges=EDGES)
    for given, limits in (([], {}), (["kd"], {}), (["m"], {}), ([], {"max_expansions": 1})):
        res = execute(p, p.request("d_separation", x=["x3", "x1"], y=["y3", "y1"], given=given, scope=SCOPE,
                                   certificate=True, limits=limits))
        assert list(validator.iter_errors(res.to_dict())) == []
        assert summarize(res)["summary"]["separated"] is res.answer["separated"]
