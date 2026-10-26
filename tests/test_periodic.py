"""Periodic projections: a cell's records with image shifts, standing for the infinite periodic graph, searched by
expanding images only as they are reached. On random periodic graphs in one to three dimensions, a periodic search
must give exactly what a finite lift that holds the whole search gives: the same nodes, depths, witnesses, counters
and limits. Then the verifier, snapshots, identities and refusals."""

import copy
import json
import random
from itertools import product

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, ProjectionError, execute, verify_result
from graph_runtime.periodic import PeriodicProjection, image_id, parse_image_id
from oracle import RELATIONS, TYPES, random_scope


def random_cell(rng, dims):
    names = rng.sample(["a", "b", "c", "d", "e", "10", "9", "σ"], rng.randint(1, 5))
    nodes = [{"id": n, "types": rng.sample(TYPES, rng.randint(0, 2))} for n in names]
    edges = []
    for i in range(rng.randint(0, 8)):
        if edges and rng.random() < 0.15:              # a parallel edge: same ends, relation and shift
            twin = rng.choice(edges)
            src, dst, rel, shift = twin["source"], twin["target"], twin["relation"], list(twin["shift"])
        else:
            src, dst, rel = rng.choice(names), rng.choice(names), rng.choice(RELATIONS)
            shift = [rng.choice([-1, 0, 0, 1]) for _ in range(dims)]
        edges.append({"id": rng.choice(["e", "e1", "x", "e1@"]) + str(i), "source": src, "target": dst,
                      "relation": rel, "shift": shift})
    return nodes, edges


def lift(nodes, edges, dims, sources, radius):
    """A finite projection of the images within ``radius`` (per axis) of the sources, with the image IDs."""
    centers = [parse_image_id(s, dims)[1] for s in sources]
    box = set()
    for c in centers:
        for off in product(range(-radius, radius + 1), repeat=dims):
            box.add(tuple(a + b for a, b in zip(c, off)))
    lifted_nodes = [{"id": image_id(n["id"], k), "types": n["types"]} for k in box for n in nodes]
    lifted_edges = []
    for e in edges:
        for k in box:
            far = tuple(a + b for a, b in zip(k, e["shift"]))
            if far in box:
                lifted_edges.append({"id": image_id(e["id"], k), "source": image_id(e["source"], k),
                                     "target": image_id(e["target"], far), "relation": e["relation"]})
    return Projection.build(nodes=lifted_nodes, edges=lifted_edges, relations=RELATIONS, node_types=TYPES)


def comparable(res):
    d = res.to_dict(timing=False)
    return {k: d[k] for k in ("status", "answer", "interruption", "error", "stats")}


def test_a_periodic_search_is_the_search_of_any_lift_that_holds_it():
    rng = random.Random("periodic")
    tally = {}
    for _ in range(600):
        dims = rng.randint(1, 3)
        nodes, edges = random_cell(rng, dims)
        pp = PeriodicProjection.build(nodes=nodes, edges=edges, dimensions=dims, relations=RELATIONS,
                                      node_types=TYPES)
        ids = [n["id"] for n in nodes]
        sources = sorted({image_id(rng.choice(ids), [rng.randint(-1, 1) for _ in range(dims)])
                          for _ in range(rng.randint(1, 2))})
        depth = rng.randint(0, 3 if dims < 3 else 2)
        op = rng.choice(["reachability", "dependency_impact"])
        raw = {"operation": op, ("sources" if op == "reachability" else "changed"): sources,
               "scope": random_scope(rng), "max_depth": depth}
        if rng.random() < 0.5:
            raw["limits"] = {"max_results": rng.randint(0, 12), "max_expansions": rng.randint(0, 15)}
        lifted = lift(nodes, edges, dims, sources, depth + 1)
        got = execute(pp, {**raw, "projection": pp.identity})
        want = execute(lifted, {**raw, "projection": lifted.identity})
        assert comparable(got) == comparable(want), raw
        assert verify_result(got, nodes=nodes, edges=edges, dimensions=dims) == []
        tally[(op, got.status)] = tally.get((op, got.status), 0) + 1
    assert all(tally.get((op, s), 0) >= 30 for op in ("reachability", "dependency_impact")
               for s in (COMPLETED, INTERRUPTED)), tally


def test_a_closure_made_without_an_edge_never_passes_where_the_edge_changes_the_answer():
    rng = random.Random("periodic-soundness")
    changed = 0
    for _ in range(800):
        dims = rng.randint(1, 2)
        nodes, edges = random_cell(rng, dims)
        if not edges:
            continue
        ids = [n["id"] for n in nodes]
        sources = [image_id(rng.choice(ids), [0] * dims)]
        raw = {"operation": "reachability", "sources": sources, "scope": random_scope(rng),
               "max_depth": rng.randint(1, 3)}
        full = PeriodicProjection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        gone = rng.choice(edges)
        less = [e for e in edges if e is not gone]
        part = PeriodicProjection.build(nodes=nodes, edges=less, dimensions=dims, relations=RELATIONS,
                                        node_types=TYPES)
        a = execute(full, {**raw, "projection": full.identity})
        b = execute(part, {**raw, "projection": part.identity})
        assert verify_result(b, nodes=nodes, edges=less, dimensions=dims) == []
        claim = [{e["node"]: e["depth"] for e in r.answer["reached"]} for r in (a, b)]
        if claim[0] != claim[1]:
            changed += 1
            assert verify_result(b, nodes=nodes, edges=edges, dimensions=dims)
    assert changed >= 100


def lattice(dims=2, periodic=None):
    """One atom per cell, a neighbor edge along each axis."""
    edges = [{"id": f"e{axis}", "source": "A", "target": "A", "relation": "neighbor",
              "shift": [1 if i == axis else 0 for i in range(dims)]} for axis in range(dims)
             if periodic is None or periodic[axis]]
    return PeriodicProjection.build(nodes=[{"id": "A", "types": ["Pt"]}], edges=edges, dimensions=dims,
                                    periodic=periodic)


BOTH = [("neighbor", "forward"), ("neighbor", "reverse")]


def test_shells_of_a_square_lattice():
    pp = lattice()
    res = execute(pp, pp.request("reachability", sources=["A@0,0"], scope=BOTH, max_depth=3))
    by_depth = {}
    for e in res.answer["reached"]:
        by_depth[e["depth"]] = by_depth.get(e["depth"], 0) + 1
    assert res.complete and by_depth == {0: 1, 1: 4, 2: 8, 3: 12}
    assert [h["edge"] for h in res.path_to("A@2,-1")] == ["e0@0,0", "e0@1,0", "e1@2,-1"]


def test_an_infinite_search_is_interrupted_never_complete():
    pp = lattice()
    res = execute(pp, pp.request("reachability", sources=["A@0,0"], scope=BOTH, limits={"max_results": 100}))
    assert res.status == INTERRUPTED and res.interruption["reason"] == "output_limit"
    res = execute(pp, pp.request("reachability", sources=["A@0,0"], scope=BOTH,
                                 limits={"max_expansions": 50, "max_results": 10**6}))
    assert res.status == INTERRUPTED and res.interruption["reason"] == "expansion_budget"
    assert res.reachable("A@1000,0") is None


def test_a_molecule_in_a_periodic_cell_is_finite():
    pp = PeriodicProjection.build(nodes=["C", "O"], edges=[
        {"id": "co", "source": "C", "target": "O", "relation": "bond", "shift": [0, 0, 0]}])
    res = execute(pp, pp.request("reachability", sources=["C@2,0,0"], scope=[("bond", "forward"),
                                                                           ("bond", "reverse")]))
    assert res.complete and [e["node"] for e in res.answer["reached"]] == ["C@2,0,0", "O@2,0,0"]
    assert res.reachable("O@0,0,0") is False


def test_axes_that_do_not_repeat():
    pp = lattice(3, periodic=[True, True, False])
    assert pp.periodic == (True, True, False)
    bad = execute(pp, pp.request("reachability", sources=["A@0,0,1"], scope=BOTH))
    assert bad.status == "failed" and bad.error["code"] == "unknown_node"
    with pytest.raises(ProjectionError):
        PeriodicProjection.build(nodes=["A"], edges=[{"id": "z", "source": "A", "target": "A", "relation": "n",
                                                      "shift": [0, 0, 1]}], periodic=[True, True, False])


@pytest.mark.parametrize("edges,dimensions", [
    ([{"id": "e", "source": "a", "target": "a", "relation": "R"}], None),
    ([{"id": "e", "source": "a", "target": "a", "relation": "R", "shift": [1, True]}], None),
    ([{"id": "e", "source": "a", "target": "a", "relation": "R", "shift": [1, 0]}], 3),
    ([{"id": "e", "source": "a", "target": "a", "relation": "R", "shift": [1, 0]},
      {"id": "f", "source": "a", "target": "a", "relation": "R", "shift": [1]}], None),
    ([], None),
    ([{"id": "e", "source": "a", "target": "a", "relation": "R", "shift": []}], None),
])
def test_malformed_cells_are_refused(edges, dimensions):
    with pytest.raises(ProjectionError):
        PeriodicProjection.build(nodes=["a"], edges=edges, dimensions=dimensions)


def test_identity_snapshot_and_immutability():
    rng = random.Random("periodic-identity")
    nodes, edges = random_cell(rng, 2)
    while not edges:
        nodes, edges = random_cell(rng, 2)
    pp = PeriodicProjection.build(nodes=nodes, edges=edges)
    again = PeriodicProjection.build(nodes=list(reversed(nodes)), edges=list(reversed(copy.deepcopy(edges))))
    assert again.identity == pp.identity
    moved = copy.deepcopy(edges)
    moved[0]["shift"] = [x + 1 for x in moved[0]["shift"]]
    assert PeriodicProjection.build(nodes=nodes, edges=moved).identity != pp.identity
    assert pp.identity != pp.cell.identity
    snap = json.loads(json.dumps(pp.to_dict()))
    assert PeriodicProjection.from_dict(snap) == pp
    with pytest.raises(ProjectionError):
        PeriodicProjection.from_dict({**snap, "dimensions": 3})
    with pytest.raises(AttributeError):
        pp.foo = 1
    import pickle
    assert pickle.loads(pickle.dumps(pp)) == pp


def test_only_set_operations_run_on_a_periodic_projection():
    pp = lattice()
    for raw in ({"operation": "shortest_path", "sources": ["A@0,0"], "targets": ["A@1,1"], "scope": BOTH,
                 "costs": {"by_relation": {"neighbor": 1}}},
                {"operation": "typed_path", "sources": ["A@0,0"], "pattern": [{"scope": BOTH}]}):
        res = execute(pp, {**raw, "projection": pp.identity})
        assert res.status == "unsupported" and res.error["code"] == "unsupported_operation"


def test_image_ids_are_written_one_way():
    assert parse_image_id("a@b@1,-2", 2) == ("a@b", (1, -2))
    for text in ("a@01,0", "a@+1,0", "a@-0,0", "a@1,0,", "a@1", "@1,0", "a1,0", "a@1, 0"):
        assert parse_image_id(text, 2) is None
