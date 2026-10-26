import copy
import hashlib
import json
import pickle
import random

import pytest

from conftest import EDGES, NODES
from graph_runtime import HypothesisCandidate, Projection, ProjectionError
from graph_runtime.projection import CONTRACT


def build(**kw):
    return Projection.build(**{"nodes": NODES, "edges": EDGES, **kw})


def test_identity_ignores_record_order():
    rng = random.Random(7)
    base = build()
    for _ in range(20):
        nodes, edges = NODES[:], EDGES[:]
        rng.shuffle(nodes)
        rng.shuffle(edges)
        assert Projection.build(nodes=nodes, edges=edges).identity == base.identity


@pytest.mark.parametrize("change", [
    lambda n, e, kw: (n, e + [{"id": "e7", "source": "a", "target": "e", "relation": "USES"}], kw),
    lambda n, e, kw: (n, [dict(x, relation="CITES") if x["id"] == "e1" else x for x in e], kw),
    lambda n, e, kw: (n, [dict(x, target="e") if x["id"] == "e1" else x for x in e], kw),
    lambda n, e, kw: ([*n, "f"], e, kw),
    lambda n, e, kw: ([{"id": "a", "types": []}, *n[1:]], e, kw),
    lambda n, e, kw: (n, e, {**kw, "bindings": {"source": "doc@2"}}),
    lambda n, e, kw: (n, e, {**kw, "relations": ["USES", "CITES", "MENTIONS"]}),
    lambda n, e, kw: (n, e, {**kw, "node_types": ["Sample", "Catalyst", "Species", "Dataset"]}),
])
def test_identity_changes_with_content(change):
    nodes, edges, kw = change(NODES, EDGES, {})
    assert Projection.build(nodes=nodes, edges=edges, **kw).identity != build().identity


def test_identity_is_reproducible_from_the_records_alone():
    """The digest documented in projection._identity, computed here without the runtime."""
    p = build(bindings={"source": "doc@1"})

    def line(x):
        return (json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()

    types = {n if isinstance(n, str) else n["id"]: sorted(set([] if isinstance(n, str) else n["types"])) for n in NODES}
    h = hashlib.sha256(line({"kind": "graph_runtime.projection", "contract": CONTRACT, "bindings": {"source": "doc@1"},
                             "relations": ["CITES", "USES"], "node_types": ["Catalyst", "Sample", "Species"],
                             "type_parents": {}, "relation_parents": {}, "node_count": 5, "edge_count": 6}))
    for nid in sorted(types):
        h.update(line([nid, types[nid]]))
    for e in sorted(EDGES, key=lambda e: e["id"]):
        h.update(line([e["id"], e["source"], e["target"], e["relation"]]))
    assert p.identity == "sha256:" + h.hexdigest()


def test_original_ids_and_parallel_edges_are_kept():
    p = build()
    assert [e.id for e in p.edges()] == ["e1", "e2", "e3", "e4", "e5", "e6"]
    assert p.edge("e4")[1:] == p.edge("e5")[1:] == ("c", "d", "USES")
    assert p.node("d").types == ("Catalyst", "Species")
    assert p.node("e").types == ()
    assert (p.node_count, p.edge_count, p.relations) == (5, 6, ("CITES", "USES"))
    assert p.has_node("a") and not p.has_node("z") and p.has_edge("e5") and not p.has_edge("e9")


def test_declared_vocabularies_may_be_wider_than_the_records():
    p = build(relations=["USES", "CITES", "MENTIONS"], node_types=["Sample", "Catalyst", "Species", "Dataset"])
    assert p.relations == ("CITES", "MENTIONS", "USES")
    assert "Dataset" in p.node_types


@pytest.mark.parametrize("kw,code", [
    ({"nodes": ["a", "a"], "edges": []}, "duplicate_node_id"),
    ({"nodes": ["a", {"id": "a"}], "edges": []}, "duplicate_node_id"),
    ({"nodes": ["a"], "edges": [{"id": "x", "source": "a", "target": "a", "relation": "R"}] * 2}, "duplicate_edge_id"),
    ({"nodes": ["a"], "edges": [{"id": "x", "source": "a", "target": "b", "relation": "R"}]}, "dangling_edge"),
    ({"nodes": ["a"], "edges": [{"id": "x", "source": "a", "target": "a", "relation": "R"}], "relations": ["S"]},
     "undeclared_relation"),
    ({"nodes": [{"id": "a", "types": ["T"]}], "edges": [], "node_types": ["U"]}, "undeclared_node_type"),
    ({"nodes": [""], "edges": []}, "invalid_record"),
    ({"nodes": [7], "edges": []}, "invalid_record"),
    ({"nodes": [{"id": "a", "types": "T"}], "edges": []}, "invalid_record"),
    ({"nodes": ["a"], "edges": [{"id": "x", "source": "a", "target": "a"}]}, "invalid_record"),
    ({"nodes": ["a"], "edges": [{"id": "x", "source": "a", "target": "a", "relation": ""}]}, "invalid_record"),
    ({"nodes": ["a"], "edges": [("x", "a", "a", "R")]}, "invalid_record"),
    ({"nodes": ["\ud800"], "edges": []}, "invalid_record"),
    ({"nodes": ["a"], "edges": [], "bindings": {"source": 3}}, "invalid_record"),
    ({"nodes": ["a"], "edges": [], "bindings": ["source"]}, "invalid_bindings"),
])
def test_invalid_records_are_refused(kw, code):
    with pytest.raises(ProjectionError) as err:
        Projection.build(**kw)
    assert err.value.code == code


def test_candidates_never_enter_a_projection():
    for rec in ({"id": "h", "kind": "hypothesis_candidate"}, {"id": "h", "epistemic_status": "unverified"}):
        with pytest.raises(ProjectionError) as err:
            Projection.build(nodes=["a", rec], edges=[])
        assert err.value.code == "candidate_record"
        with pytest.raises(ProjectionError) as err:
            Projection.build(nodes=["a"], edges=[{**rec, "source": "a", "target": "a", "relation": "R"}])
        assert err.value.code == "candidate_record"
    p = build()
    cand = HypothesisCandidate.create(
        statement="s", scope="synthetic", projection=p, producer={"name": "t", "version": "0"},
        basis=[("node", "a")], at="2026-10-25",
        proposed_tests=[{"description": "d", "outcomes": [{"observation": "x", "bearing": "supports"},
                                                          {"observation": "y", "bearing": "opposes"}]}])
    with pytest.raises(ProjectionError):
        Projection.build(nodes=["a", cand], edges=[])


def test_projection_is_immutable():
    p = build()
    with pytest.raises(AttributeError):
        p.foo = 1
    with pytest.raises(AttributeError):
        p._identity = "x"
    with pytest.raises(AttributeError):
        del p._out
    with pytest.raises(TypeError):
        p._node_index["zz"] = 3
    with pytest.raises(TypeError):
        Projection()
    assert isinstance(p._out, tuple) and all(isinstance(x, tuple) for x in p._out)


def test_records_are_only_read():
    nodes, edges = copy.deepcopy(NODES), copy.deepcopy(EDGES)
    build(nodes=nodes, edges=edges)
    assert (nodes, edges) == (NODES, EDGES)


def test_snapshot_round_trip_and_checks():
    p = build(bindings={"source": "doc@1"}, relations=["USES", "CITES", "MENTIONS"])
    snap = json.loads(json.dumps(p.to_dict()))
    q = Projection.from_dict(snap)
    assert q == p and q.identity == p.identity and q.relations == p.relations
    with pytest.raises(ProjectionError) as err:
        Projection.from_dict({**snap, "edges": snap["edges"][:-1]})
    assert err.value.code == "identity_mismatch"
    with pytest.raises(ProjectionError) as err:
        Projection.from_dict({**snap, "contract": "graph-runtime/v9"})
    assert err.value.code == "unsupported_contract"
    assert pickle.loads(pickle.dumps(p)) == p
    assert copy.deepcopy(p).identity == p.identity


def test_stale_bindings():
    p = build(bindings={"source": "doc@1", "schema": "s@1"})
    assert p.stale_bindings({"source": "doc@1", "schema": "s@1"}) == ()
    assert p.stale_bindings({"source": "doc@2", "schema": "s@1"}) == ("source",)
    assert p.stale_bindings({"source": "doc@1"}) == ("schema",)
    assert p.stale_bindings({"source": "doc@1", "schema": "s@1", "policy": "public"}) == ("policy",)
