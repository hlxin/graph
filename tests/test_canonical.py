"""Canonical forms and isomorphism of induced subgraphs. networkx's isomorphism test (VF2, with node colors and the
multiset of edge relations between each ordered pair) is the oracle; a relabeled copy must get the same canonical
identity, and a perturbed one a different identity exactly when networkx says the two are not isomorphic."""

import copy
import json
import random
import time

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from test_certificates import reseal
from test_revalidate import edited

nx = pytest.importorskip("networkx")
TYPES = ("A", "B")
RELATIONS = ("R", "S")


def random_graph(rng, n, m, prefix):
    nodes = [{"id": f"{prefix}{i:02d}", "types": rng.sample(TYPES, rng.randint(0, 1))} for i in range(n)]
    edges = [{"id": f"{prefix}e{k:03d}", "source": nodes[rng.randrange(n)]["id"],
              "target": nodes[rng.randrange(n)]["id"], "relation": rng.choice(RELATIONS)} for k in range(m)]
    return nodes, edges


def relabeled(rng, nodes, edges, prefix):
    """The same graph under a random renaming of its nodes and edges."""
    perm = list(range(len(nodes)))
    rng.shuffle(perm)
    name = {n["id"]: f"{prefix}{perm[k]:02d}" for k, n in enumerate(nodes)}
    new_nodes = [{"id": name[n["id"]], "types": list(n["types"])} for n in nodes]
    order = list(range(len(edges)))
    rng.shuffle(order)
    new_edges = [{"id": f"{prefix}e{order[k]:03d}", "source": name[e["source"]], "target": name[e["target"]],
                  "relation": e["relation"]} for k, e in enumerate(edges)]
    return new_nodes, new_edges


def perturbed(rng, nodes, edges):
    nodes, edges = copy.deepcopy(nodes), copy.deepcopy(edges)
    kind = rng.choice(["relation", "type", "move", "drop"])
    if kind == "relation" and edges:
        e = rng.choice(edges)
        e["relation"] = "S" if e["relation"] == "R" else "R"
    elif kind == "type":
        n = rng.choice(nodes)
        n["types"] = [] if n["types"] else ["A"]
    elif kind == "move" and edges:
        rng.choice(edges)["target"] = rng.choice(nodes)["id"]
    elif edges:
        edges.pop(rng.randrange(len(edges)))
    return nodes, edges


def as_nx(nodes, edges, directed):
    g = nx.MultiDiGraph() if directed else nx.MultiGraph()
    for n in nodes:
        g.add_node(n["id"], types=tuple(sorted(n["types"])))
    for e in edges:
        g.add_edge(e["source"], e["target"], relation=e["relation"])
    return g


def oracle(left, right, directed):
    gl, gr = as_nx(*left, directed), as_nx(*right, directed)
    def match(a, b):                                        # the multiset of relations between a pair, not the set
        return sorted(d["relation"] for d in a.values()) == sorted(d["relation"] for d in b.values())
    return nx.is_isomorphic(gl, gr, node_match=lambda a, b: a["types"] == b["types"], edge_match=match)


def pair(rng, n, m, change):
    left = random_graph(rng, n, m, "L")
    right = relabeled(rng, *left, "R")
    if change:
        right = perturbed(rng, *right)
    p = Projection.build(nodes=left[0] + right[0], edges=left[1] + right[1], relations=RELATIONS, node_types=TYPES)
    return left, right, p


def ids(nodes):
    return [n["id"] for n in nodes]


def test_isomorphism_agrees_with_networkx_and_proves_itself():
    rng = random.Random("isomorphism")
    tally = {"iso": 0, "not": 0, "certified": 0}
    for _ in range(500):
        n, m = rng.randint(1, 7), rng.randint(0, 12)
        left, right, p = pair(rng, n, m, rng.random() < 0.5)
        directed = rng.random() < 0.6
        res = execute(p, p.request("isomorphism", left=ids(left[0]), right=ids(right[0]), scope=[("R", "forward"),
                                   ("S", "forward")], directed=directed, certificate=True))
        assert res.status == COMPLETED, res.error
        records = (left[0] + right[0], left[1] + right[1])
        assert verify_result(res, nodes=records[0], edges=records[1]) == [] and contract_problems(res.to_dict()) == []
        want = oracle(left, right, directed)
        assert res.answer["isomorphic"] is want
        tally["iso" if want else "not"] += 1
        tally["certified"] += res.answer["certificate"] is not None
    assert min(tally.values()) >= 80, tally


def test_relabeled_copies_share_their_canonical_identity():
    rng = random.Random("canonical")
    for _ in range(300):
        n, m = rng.randint(1, 8), rng.randint(0, 14)
        left, right, p = pair(rng, n, m, rng.random() < 0.4)
        directed = rng.random() < 0.5
        forms = [execute(p, p.request("canonical_form", nodes=ids(side[0]), scope=[("R", "forward"), ("S", "forward")],
                                      directed=directed)) for side in (left, right)]
        records = (left[0] + right[0], left[1] + right[1])
        for f in forms:
            assert f.status == COMPLETED and verify_result(f, nodes=records[0], edges=records[1]) == []
        same = forms[0].answer["canonical_id"] == forms[1].answer["canonical_id"]
        assert same is oracle(left, right, directed)


@pytest.mark.parametrize("name", ["complete8", "petersen", "hypercube4", "grid5", "hexagons", "cubical"])
def test_symmetric_graphs_are_canonized_within_the_budget(name):
    """Graphs with large automorphism groups: the orbit pruning keeps the search small, and a relabeled copy gets
    the same identity."""
    g = {"complete8": nx.complete_graph(8), "petersen": nx.petersen_graph(), "hypercube4": nx.hypercube_graph(4),
         "grid5": nx.grid_2d_graph(5, 5), "hexagons": nx.hexagonal_lattice_graph(3, 3),
         "cubical": nx.cubical_graph()}[name]
    g = nx.convert_node_labels_to_integers(g)
    rng = random.Random(name)
    left_nodes = [{"id": f"L{v:03d}", "types": []} for v in g]
    left_edges = [{"id": f"Le{k:04d}", "source": f"L{a:03d}", "target": f"L{b:03d}", "relation": "R"}
                  for k, (a, b) in enumerate(g.edges())]
    right_nodes, right_edges = relabeled(rng, left_nodes, left_edges, "R")
    p = Projection.build(nodes=left_nodes + right_nodes, edges=left_edges + right_edges, relations=RELATIONS,
                         node_types=TYPES)
    start = time.perf_counter()
    res = execute(p, p.request("isomorphism", left=ids(left_nodes), right=ids(right_nodes), scope=[("R", "forward")],
                               directed=False))
    assert res.status == COMPLETED and res.answer["isomorphic"] is True
    assert verify_result(res, nodes=left_nodes + right_nodes, edges=left_edges + right_edges) == []
    assert time.perf_counter() - start < 10


def test_regular_graphs_refinement_cannot_separate():
    """The Petersen graph and the pentagonal prism are both 3-regular on 10 nodes: refinement gives every node one
    color, so the negative comes from the canonical search alone and has no certificate."""
    pet = nx.convert_node_labels_to_integers(nx.petersen_graph())
    prism = nx.convert_node_labels_to_integers(nx.circular_ladder_graph(5))
    nodes = [{"id": f"P{v}", "types": []} for v in pet] + [{"id": f"Q{v}", "types": []} for v in prism]
    edges = [{"id": f"Pe{k:02d}", "source": f"P{a}", "target": f"P{b}", "relation": "R"} for k, (a, b) in
             enumerate(pet.edges())] + [{"id": f"Qe{k:02d}", "source": f"Q{a}", "target": f"Q{b}", "relation": "R"}
                                        for k, (a, b) in enumerate(prism.edges())]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("isomorphism", left=[f"P{v}" for v in pet], right=[f"Q{v}" for v in prism],
                               scope=[("R", "forward")], directed=False, certificate=True))
    assert res.answer["isomorphic"] is False and res.answer["certificate"] is None
    assert verify_result(res, nodes=nodes, edges=edges) == []
    forms = [execute(p, p.request("canonical_form", nodes=side, scope=[("R", "forward")], directed=False))
             for side in ([f"P{v}" for v in pet], [f"Q{v}" for v in prism])]
    assert forms[0].answer["canonical_id"] != forms[1].answer["canonical_id"]


def test_forged_answers_are_refused():
    rng = random.Random("iso forged")
    left = ([{"id": f"L{i}", "types": []} for i in range(4)],
            [{"id": f"Le{i}", "source": f"L{i}", "target": f"L{(i + 1) % 4}", "relation": "R"} for i in range(4)])
    right = relabeled(rng, *left, "R")
    records = (left[0] + right[0], left[1] + right[1])
    p = Projection.build(nodes=records[0], edges=records[1])
    res = execute(p, p.request("isomorphism", left=ids(left[0]), right=ids(right[0]), scope=[("R", "forward")]))
    assert res.answer["isomorphic"] is True and verify_result(res, nodes=records[0], edges=records[1]) == []

    def forged(result, change):
        data = copy.deepcopy(thaw(result.to_dict()))
        change(data["answer"])
        return verify_result(reseal(data), nodes=records[0], edges=records[1])

    def swap(a):                                            # two right nodes exchanged: a cycle no longer kept
        m = a["mapping"]
        m[0]["right"], m[1]["right"] = m[1]["right"], m[0]["right"]
    assert forged(res, swap)
    assert forged(res, lambda a: a["edge_mapping"].pop())
    form = execute(p, p.request("canonical_form", nodes=ids(left[0]), scope=[("R", "forward")]))
    assert verify_result(form, nodes=records[0], edges=records[1]) == []
    assert forged(form, lambda a: a["encoding"]["edges"].pop())
    def swapped(a):                                         # the first two positions exchanged: not an automorphism
        a["order"][0], a["order"][1] = a["order"][1], a["order"][0]
    assert forged(form, swapped)
    assert forged(form, lambda a: a.__setitem__("canonical_id", "sha256:" + "0" * 64))
    tri = ([{"id": "T0", "types": []}, {"id": "T1", "types": []}, {"id": "T2", "types": []},
            {"id": "T3", "types": []}],
           [{"id": f"Te{i}", "source": f"T{i}", "target": f"T{(i + 1) % 3}", "relation": "R"} for i in range(3)]
           + [{"id": "Te3", "source": "T0", "target": "T3", "relation": "R"}])    # T0 leads out twice
    records = (left[0] + tri[0], left[1] + tri[1])
    q = Projection.build(nodes=records[0], edges=records[1])
    neg = execute(q, q.request("isomorphism", left=ids(left[0]), right=ids(tri[0]), scope=[("R", "forward")],
                               certificate=True))
    assert neg.answer["isomorphic"] is False and neg.answer["certificate"]["kind"] == "refinement"
    assert verify_result(neg, nodes=records[0], edges=records[1]) == []
    assert neg.answer["certificate"]["rounds"] == 1                 # no types: only T0's two hops out separate
    assert forged(neg, lambda a: a["certificate"].__setitem__("rounds", 0))
    assert forged(neg, lambda a: a.__setitem__("certificate", {"kind": "counts"}))


def test_interrupted_searches_claim_nothing():
    rng = random.Random("iso interrupted")
    seen = set()
    for _ in range(200):
        left, right, p = pair(rng, rng.randint(2, 8), rng.randint(0, 12), rng.random() < 0.5)
        records = (left[0] + right[0], left[1] + right[1])
        for op, kw in (("isomorphism", {"left": ids(left[0]), "right": ids(right[0]), "certificate": True}),
                       ("canonical_form", {"nodes": ids(left[0])})):
            res = execute(p, p.request(op, scope=[("R", "forward"), ("S", "forward")],
                                       limits={"max_expansions": rng.randint(0, 60)}, **kw))
            assert verify_result(res, nodes=records[0], edges=records[1]) == []
            assert contract_problems(res.to_dict()) == []
            seen.add(res.status)
    assert seen == {COMPLETED, INTERRUPTED}


def test_canonical_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    from graph_runtime._validation import shape_problems
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("iso schema")
    for _ in range(60):
        left, right, p = pair(rng, rng.randint(1, 6), rng.randint(0, 10), rng.random() < 0.5)
        for op, kw in (("isomorphism", {"left": ids(left[0]), "right": ids(right[0]), "certificate": True}),
                       ("canonical_form", {"nodes": ids(left[0])})):
            for limits in ({}, {"max_expansions": rng.randint(0, 20)}):
                data = json.loads(json.dumps(thaw(execute(p, p.request(op, scope=[("R", "forward")], limits=limits,
                                                                       **kw)).to_dict())))
                assert [e.message for e in validator.iter_errors(data)] == [] and shape_problems("result", data) == []


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("iso revalidation")
    tally = {}
    for _ in range(300):
        left, right, p = pair(rng, rng.randint(2, 6), rng.randint(1, 9), rng.random() < 0.5)
        nodes, edges = left[0] + right[0], left[1] + right[1]
        nodes2, edges2 = edited(rng, nodes, edges)
        kept = {n["id"] for n in nodes2}
        if not set(ids(left[0]) + ids(right[0])) <= kept:
            continue
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS + ("T",), node_types=TYPES + ("C",))
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS + ("T",), node_types=TYPES + ("C",))
        for op, kw in (("isomorphism", {"left": ids(left[0]), "right": ids(right[0]), "certificate": True}),
                       ("canonical_form", {"nodes": ids(left[0])})):
            raw = {"operation": op, "scope": [{"relation": "R", "direction": "forward"}], **kw}
            old = execute(p, {**raw, "projection": p.identity})
            verdict = revalidate(old, q, diff=diff(p, q)).verdict
            fresh = execute(q, {**raw, "projection": q.identity})
            if verdict == HOLDS:
                key = "isomorphic" if op == "isomorphism" else "canonical_id"
                assert fresh.answer[key] == old.answer[key]
            elif verdict == FAILS:
                assert verify_result(old, nodes=nodes2, edges=edges2)
            tally[verdict] = tally.get(verdict, 0) + 1
    assert tally.get(HOLDS, 0) >= 30 and tally.get(FAILS, 0) >= 30, tally


def test_reading_the_sets_is_charged_and_checked():
    """A one-node set whose node has 100,000 edges elsewhere: reading it is charged, so a small budget stops it."""
    n = 100_000
    nodes = ["hub"] + [f"x{i:06d}" for i in range(n)]
    edges = [{"id": f"e{i:06d}", "source": "hub", "target": f"x{i:06d}", "relation": "R"} for i in range(n)]
    p = Projection.build(nodes=nodes, edges=edges)
    start = time.perf_counter()
    res = execute(p, p.request("canonical_form", nodes=["hub"], scope=[("R", "forward")],
                               limits={"max_expansions": 10}))
    assert res.status == INTERRUPTED and res.stats["edges_examined"] == 0 and time.perf_counter() - start < 1
    done = execute(p, p.request("canonical_form", nodes=["hub"], scope=[("R", "forward")]))
    assert done.status == COMPLETED and done.stats["edges_examined"] == n


def test_forged_nodes_and_duplicate_mappings_are_refused():
    nodes = ["a", "b", "c", "d"]
    edges = [{"id": "e", "source": "a", "target": "b", "relation": "R"},
             {"id": "f", "source": "c", "target": "d", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("isomorphism", left=["a", "b"], right=["c", "d"], scope=[("R", "forward")]))
    assert res.answer["isomorphic"] is True and verify_result(res, nodes=nodes, edges=edges) == []

    def forged(result, change, on=(nodes, edges)):
        data = copy.deepcopy(thaw(result.to_dict()))
        change(data)
        return verify_result(reseal(data), nodes=on[0], edges=on[1])
    assert forged(res, lambda d: d["answer"].__setitem__("edge_mapping", [{"left": "e", "right": "e"},
                                                                         {"left": "e", "right": "f"}]))
    form = execute(p, p.request("canonical_form", nodes=["a"], scope=[("R", "forward")]))
    only_b = (["b"], [])
    assert verify_result(form, nodes=only_b[0], edges=only_b[1])                # a is not in these records
    assert forged(res, lambda d: None, on=(["b", "c", "d"], edges[1:]))        # nor here


def test_a_type_edit_does_not_let_a_canonical_form_hold():
    p = Projection.build(nodes=["a", "b"], edges=[{"id": "e", "source": "a", "target": "b", "relation": "R"}],
                         node_types=["A"])
    res = execute(p, p.request("canonical_form", nodes=["a", "b"], scope=[("R", "forward")]))
    q = Projection.build(nodes=[{"id": "a", "types": ["A"]}, {"id": "b", "types": ["A"]}],
                         edges=[{"id": "e", "source": "a", "target": "b", "relation": "R"}], node_types=["A"])
    assert revalidate(res, q).verdict != HOLDS
    fresh = execute(q, q.request("canonical_form", nodes=["a", "b"], scope=[("R", "forward")]))
    assert fresh.answer["canonical_id"] != res.answer["canonical_id"]


def test_a_relabeling_is_not_taken_for_the_canonical_form():
    nodes = ["a", "b"]
    edges = [{"id": "e", "source": "a", "target": "b", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("canonical_form", nodes=nodes, scope=[("R", "forward")]))
    from graph_runtime._canonical import digest
    data = copy.deepcopy(thaw(res.to_dict()))
    order = list(data["answer"]["order"])
    other = order[::-1]                                     # another relabeling, valid but not the least
    pos = {n: i for i, n in enumerate(other)}
    enc = {"nodes": [[], []], "edges": [[pos["a"], pos["b"], "R"]], "directed": True}
    data["answer"].update(order=other, encoding=enc,
                          canonical_id=digest({"kind": "graph_runtime.canonical_form", **enc}))
    data = reseal(data)
    assert verify_result(data, nodes=nodes, edges=edges) == []          # a relabeling, as far as a checker sees
    q = Projection.build(nodes=nodes + ["z"], edges=edges)
    assert revalidate(data, q).verdict != HOLDS
    floats = copy.deepcopy(thaw(res.to_dict()))
    floats["answer"]["encoding"]["edges"][0][0] = float(floats["answer"]["encoding"]["edges"][0][0])
    assert verify_result(reseal(floats), nodes=nodes, edges=edges)


def test_revalidation_trusts_a_result_only_on_its_own_projection():
    """A genuine canonical form holds on its own projection; on another one, a valid relabeling is not taken for the
    canonical form (unknown), and a contradicted one is refuted."""
    nodes = ["a", "b"]
    edges = [{"id": "e", "source": "a", "target": "b", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges, relations=["R"])
    res = execute(p, p.request("canonical_form", nodes=nodes, scope=[("R", "forward")]))
    assert revalidate(res, p).verdict == HOLDS
    plus = Projection.build(nodes=nodes + ["z"], edges=edges, relations=["R"])
    assert revalidate(res, plus).verdict not in (HOLDS, FAILS)
    reversed_edge = Projection.build(nodes=nodes, edges=[{**edges[0], "source": "b", "target": "a"}], relations=["R"])
    assert revalidate(res, reversed_edge).verdict == FAILS
