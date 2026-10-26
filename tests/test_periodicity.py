"""Periodicity: the translation lattice of each component of an infinite periodic graph, from its quotient (the cell
with image shifts): rank 0 for a finite component, 1 for a chain, 2 for a layer, 3 for a framework, a canonical basis
(Hermite normal form) with witnesses, and the number of interpenetrating copies. An oracle lifts a large box of
images, searches it breadth first, and takes the lattice the translations it finds generate."""

import random
from itertools import product

import pytest

from graph_runtime import Projection, Result, execute, verify_result
from graph_runtime.lattice import copies, hermite
from graph_runtime.periodic import PeriodicProjection, image_id, parse_image_id

BOND = [("bond", "forward"), ("bond", "reverse")]


def cell(rng, dims):
    names = rng.sample(["a", "b", "c", "d"], rng.randint(1, 4))
    edges = [{"id": f"e{i}", "source": rng.choice(names), "target": rng.choice(names), "relation": "bond",
              "shift": [rng.choice([-1, 0, 0, 1, 2]) for _ in range(dims)]} for i in range(rng.randint(0, 5))]
    return names, edges


def oracle(names, edges, dims, seed, radius):
    """The lattice of the seed's component, from a box: every translation t such that the seed's node at the seed's
    image plus t is reached, by plain breadth-first search over the lifted box; and what it reaches."""
    node, k = parse_image_id(seed, dims)
    hops = {}
    for e in edges:
        hops.setdefault(e["source"], []).append((e["target"], tuple(e["shift"])))
        hops.setdefault(e["target"], []).append((e["source"], tuple(-x for x in e["shift"])))
    inside = lambda img: all(abs(a - b) <= radius for a, b in zip(img, k))
    seen, todo = {(node, k)}, [(node, k)]
    while todo:
        u, img = todo.pop()
        for v, s in hops.get(u, ()):
            nxt = (v, tuple(a + b for a, b in zip(img, s)))
            if inside(nxt[1]) and nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    translations = [tuple(a - b for a, b in zip(img, k)) for u, img in seen if u == node]
    return hermite(translations, dims), {image_id(u, img) for u, img in seen}


def test_random_cells_match_the_box_oracle():
    rng = random.Random("periodicity")
    ranks = {}
    for _ in range(400):
        dims = rng.randint(1, 3)
        names, edges = cell(rng, dims)
        pp = PeriodicProjection.build(nodes=names, edges=edges, dimensions=dims, relations=["bond"])
        seeds = sorted({image_id(rng.choice(names), [rng.randint(-1, 1) for _ in range(dims)]) for _ in range(3)})
        res = execute(pp, pp.request("periodicity", nodes=seeds, scope=BOND))
        assert res.complete and verify_result(res, nodes=names, edges=edges, dimensions=dims) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        radius = {1: 24, 2: 9, 3: 5}[dims]
        for comp in res.answer["components"]:
            basis, reached = oracle(names, edges, dims, comp["seeds"][0], radius)
            assert [tuple(r) for r in comp["basis"]] == basis, (edges, comp)
            assert comp["copies"] == copies(basis)
            assert all(s in reached for s in comp["seeds"])
            for other in res.answer["components"]:            # seeds of other components are not reached
                if other is not comp:
                    assert not any(s in reached for s in other["seeds"])
            ranks[comp["rank"]] = ranks.get(comp["rank"], 0) + 1
    assert all(ranks.get(r, 0) >= 20 for r in range(4)), ranks


def test_an_answer_made_without_an_edge_is_refused_where_the_edge_belongs():
    rng = random.Random("periodicity-soundness")
    refused = 0
    for _ in range(400):
        dims = rng.randint(1, 3)
        names, edges = cell(rng, dims)
        if not edges:
            continue
        seeds = [image_id(rng.choice(names), [0] * dims)]
        gone = rng.choice(edges)
        less = [e for e in edges if e is not gone]
        part = PeriodicProjection.build(nodes=names, edges=less, dimensions=dims, relations=["bond"])
        full = PeriodicProjection.build(nodes=names, edges=edges, dimensions=dims, relations=["bond"])
        a = execute(full, full.request("periodicity", nodes=seeds, scope=BOND)).answer["components"][0]
        b = execute(part, part.request("periodicity", nodes=seeds, scope=BOND))
        assert verify_result(b, nodes=names, edges=less, dimensions=dims) == []
        claim = lambda c: ({n["node"] for n in c["nodes"]}, c["basis"])
        if claim(a) != claim(b.answer["components"][0]):
            refused += 1
            assert verify_result(b, nodes=names, edges=edges, dimensions=dims)
    assert refused >= 100


def structure(edges, nodes=None):
    nodes = nodes or sorted({e["source"] for e in edges} | {e["target"] for e in edges})
    return PeriodicProjection.build(nodes=nodes, edges=[{"relation": "bond", **e} for e in edges])


def components(pp, seeds):
    res = execute(pp, pp.request("periodicity", nodes=seeds, scope=BOND))
    assert res.complete
    return [(c["seeds"], c["rank"], c["basis"], c["copies"]) for c in res.answer["components"]]


def test_classic_structures():
    graphite = structure([{"id": "a", "source": "C1", "target": "C2", "shift": [0, 0, 0]},
                          {"id": "b", "source": "C2", "target": "C1", "shift": [1, 0, 0]},
                          {"id": "c", "source": "C2", "target": "C1", "shift": [0, 1, 0]}])
    assert components(graphite, ["C1@0,0,0", "C2@3,-2,0", "C1@0,0,1"]) == [
        (("C1@0,0,0", "C2@3,-2,0"), 2, ((1, 0, 0), (0, 1, 0)), 1),          # one layer
        (("C1@0,0,1",), 2, ((1, 0, 0), (0, 1, 0)), 1)]                      # the layer above it
    chain = structure([{"id": "x", "source": "A", "target": "A", "shift": [0, 0, 1]}])
    assert components(chain, ["A@0,0,0"]) == [(("A@0,0,0",), 1, ((0, 0, 1),), 1)]
    molecule = structure([{"id": "co", "source": "C", "target": "O", "shift": [0, 0, 0]}])
    assert components(molecule, ["C@4,4,4", "O@4,4,4", "O@4,4,5"]) == [
        (("C@4,4,4", "O@4,4,4"), 0, (), 1), (("O@4,4,5",), 0, (), 1)]
    diamond = structure([{"id": f"d{i}", "source": "A", "target": "B", "shift": s}
                         for i, s in enumerate([[0, 0, 0], [-1, 0, 0], [0, -1, 0], [0, 0, -1]])])
    assert components(diamond, ["A@0,0,0"]) == [(("A@0,0,0",), 3, ((1, 0, 0), (0, 1, 0), (0, 0, 1)), 1)]
    twofold = structure([{"id": f"t{i}", "source": "A", "target": "A", "shift": s}
                         for i, s in enumerate([[1, 1, 0], [1, -1, 0], [0, 0, 1]])])    # two interpenetrating nets
    assert components(twofold, ["A@0,0,0", "A@1,0,0", "A@2,0,0"]) == [
        (("A@0,0,0", "A@2,0,0"), 3, ((1, 1, 0), (0, 2, 0), (0, 0, 1)), 2), (("A@1,0,0",), 3, ((1, 1, 0), (0, 2, 0),
                                                                                           (0, 0, 1)), 2)]


def test_witnesses_and_tree_name_real_walks():
    pp = structure([{"id": "a", "source": "C1", "target": "C2", "shift": [0, 0, 0]},
                    {"id": "b", "source": "C2", "target": "C1", "shift": [1, 0, 0]},
                    {"id": "c", "source": "C2", "target": "C1", "shift": [0, 1, 0]}])
    res = execute(pp, pp.request("periodicity", nodes=["C2@0,0,0"], scope=BOND))
    comp = res.answer["components"][0]
    # the tree follows C2's forward hops first: b reaches C1 one cell over; a and c then close cycles
    assert [(n["node"], n["image"], n["via"] and n["via"]["edge"]) for n in comp["nodes"]] == [
        ("C1", (1, 0, 0), "b@0,0,0"), ("C2", (0, 0, 0), None)]
    assert {c["edge"]: c["net"] for c in comp["cycles"]} == {"a": (1, 0, 0), "c": (-1, 1, 0)}
    assert [[(u["edge"], u["times"]) for u in w] for w in comp["witnesses"]] == [[("a", 1)], [("a", 1), ("c", 1)]]


def test_periodicity_refusals():
    plain = Projection.build(nodes=["a"], edges=[{"id": "e", "source": "a", "target": "a", "relation": "bond"}])
    res = execute(plain, plain.request("periodicity", nodes=["a"], scope=BOND))
    assert res.status == "unsupported" and res.error["code"] == "unsupported_operation"
    pp = structure([{"id": "x", "source": "A", "target": "A", "shift": [1]}])
    one_way = execute(pp, pp.request("periodicity", nodes=["A@0"], scope=[("bond", "forward")]))
    assert one_way.status == "failed" and one_way.error["code"] == "invalid_request"
    budget = execute(pp, pp.request("periodicity", nodes=["A@0"], scope=BOND, limits={"max_expansions": 0}))
    assert budget.status == "interrupted" and budget.answer["components"] == ()


@pytest.mark.parametrize("vectors,expected", [([(2,), (3,)], [(1,)]), ([(4, 6)], [(4, 6)]),
                                              ([(1, 1), (1, -1)], [(1, 1), (0, 2)]), ([], [])])
def test_hermite_normal_forms(vectors, expected):
    assert hermite(vectors, len(expected[0]) if expected else 2) == expected


def test_periodic_records_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from graph_runtime import json_schema
    pp = structure([{"id": "a", "source": "C1", "target": "C2", "shift": [0, 0, 0]},
                    {"id": "b", "source": "C2", "target": "C1", "shift": [1, 0, 0]}])
    results = [execute(pp, pp.request("periodicity", nodes=["C1@0,0,0", "C1@0,1,0"], scope=BOND)),
               execute(pp, pp.request("periodicity", nodes=["C1@0,0,0"], scope=BOND, limits={"max_expansions": 0})),
               execute(pp, pp.request("reachability", sources=["C1@0,0,0"], scope=BOND, max_depth=3)),
               execute(pp, pp.request("dependency_impact", changed=["C2@0,0,0"], scope=BOND, max_depth=2,
                                      limits={"max_results": 2}))]
    result = jsonschema.Draft202012Validator(json_schema("result"))
    for r in results:
        assert list(result.iter_errors(json.loads(r.to_json()))) == []
    snapshot = jsonschema.Draft202012Validator(json_schema("periodic_projection"))
    assert list(snapshot.iter_errors(json.loads(json.dumps(pp.to_dict())))) == []
