"""Minimum cycle bases (de Pina): rings and independent loops. The oracle shares nothing with the engine: every edge
subset of a small multigraph that is a simple cycle, taken lightest first while independent (the cycle space is a
matroid, so this greedy basis is minimum), gives the least total weight; networkx agrees on simple graphs; and the
verifier proves each basis from the records, recomputing every witness's lightest odd cycle."""

import copy
import random
from fractions import Fraction
from itertools import combinations

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from test_certificates import reseal
from oracle import RELATIONS, TYPES, node_id, random_graph, random_scope
from test_revalidate import edited


def links_of(edges, scope):
    rels = {(s["relation"] if isinstance(s, dict) else s[0]) for s in scope}
    return [e for e in edges if e["relation"] in rels]


def least_basis_weight(nodes, edges, scope, weights):
    """(rank, least total weight) by brute force: every simple cycle among the links, then matroid greedy."""
    links = links_of(edges, scope)
    kind, table = next(iter(weights.items())) if weights else (None, None)
    rational = table is not None and any(isinstance(x, float) for x in table.values())

    def w(e):
        if table is None:
            return 1
        x = table[e["relation"] if kind == "by_relation" else e["id"]]
        return Fraction(x) if rational else x
    cycles = []
    for k in range(1, len(links) + 1):
        for subset in combinations(range(len(links)), k):
            degree, seen = {}, set()
            for i in subset:
                for end in (links[i]["source"], links[i]["target"]):
                    degree[end] = degree.get(end, 0) + 1
            if any(d != 2 for d in degree.values()):
                continue
            start = links[subset[0]]["source"]                # connected: one cycle, not two
            reach, grew = {start}, True
            while grew:
                grew = False
                for i in subset:
                    a, b = links[i]["source"], links[i]["target"]
                    if (a in reach) != (b in reach):
                        reach |= {a, b}
                        grew = True
            if reach == set(degree):
                cycles.append((sum(w(links[i]) for i in subset), sum(1 << i for i in subset)))
    cycles.sort(key=lambda c: c[0])
    basis, total = {}, 0
    for weight, bits in cycles:
        while bits:
            top = bits.bit_length() - 1
            if top not in basis:
                basis[top] = bits
                total += weight
                break
            bits ^= basis[top]
    return len(basis), total


def random_weights(rng, edges):
    if rng.random() < 0.35:
        return None
    pick = (lambda: rng.randint(0, 4)) if rng.random() < 0.7 else (lambda: rng.choice([0.5, 1, 2.25, 0]))
    if rng.random() < 0.5:
        return {"by_relation": {r: pick() for r in RELATIONS}}
    return {"by_edge": {e["id"]: pick() for e in edges}}


def run(rng, n=6, m=11):
    nodes, edges = random_graph(rng, max_nodes=n, max_edges=m)
    p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
    raw = {"operation": "cycle_basis", "scope": random_scope(rng), "weights": random_weights(rng, edges),
           "certificate": rng.random() < 0.7}
    return nodes, edges, p, raw


def total(a):
    return Fraction(a["weight_exact"]) if "weight_exact" in a else a["weight"]


def test_the_basis_is_minimum_and_proves_itself():
    rng = random.Random("cycle basis")
    seen = set()
    for _ in range(500):
        nodes, edges, p, raw = run(rng)
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        rank, least = least_basis_weight(nodes, edges, raw["scope"], raw["weights"])
        a = res.answer
        assert (a["rank"], a["count"], total(a)) == (rank, rank, least), raw
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        seen.add((rank > 0, any(c["length"] == 1 for c in a["cycles"]), any(c["length"] == 2 for c in a["cycles"])))
    assert {(True, True, False), (True, False, True), (True, False, False)} <= seen     # loops, parallels, rings


def test_networkx_agrees_on_simple_graphs():
    nx = pytest.importorskip("networkx")
    rng = random.Random("networkx basis")
    for _ in range(30):
        n = 25
        pairs = sorted({tuple(sorted((rng.randrange(n), rng.randrange(n)))) for _ in range(45)})
        pairs = [(a, b) for a, b in pairs if a != b]
        nodes = [f"v{i:02d}" for i in range(n)]
        edges = [{"id": f"e{k:03d}", "source": nodes[a], "target": nodes[b], "relation": "R"}
                 for k, (a, b) in enumerate(pairs)]
        table = {e["id"]: rng.randint(1, 9) for e in edges}
        g = nx.Graph()
        g.add_nodes_from(nodes)
        for e in edges:
            g.add_edge(e["source"], e["target"], weight=table[e["id"]])
        want = sum(sum(g[u][v]["weight"] for u, v in zip(c, c[1:] + c[:1]))
                   for c in nx.minimum_cycle_basis(g, weight="weight"))
        p = Projection.build(nodes=nodes, edges=edges)
        res = execute(p, p.request("cycle_basis", scope=[("R", "forward")], weights={"by_edge": table},
                                   certificate=True))
        assert res.answer["weight"] == want
        assert verify_result(res, nodes=nodes, edges=edges) == []


def ring_projection(bonds):
    nodes = sorted({x for b in bonds for x in b})
    edges = [{"id": f"b{k:02d}", "source": a, "target": b, "relation": "BOND"} for k, (a, b) in enumerate(bonds)]
    return nodes, edges, Projection.build(nodes=nodes, edges=edges)


def test_rings_of_molecules():
    naphthalene = [("C1", "C2"), ("C2", "C3"), ("C3", "C4"), ("C4", "C5"), ("C5", "C6"), ("C6", "C1"),
                   ("C5", "C7"), ("C7", "C8"), ("C8", "C9"), ("C9", "C10"), ("C10", "C6")]
    cube = [("A", "B"), ("B", "C"), ("C", "D"), ("D", "A"), ("E", "F"), ("F", "G"), ("G", "H"), ("H", "E"),
            ("A", "E"), ("B", "F"), ("C", "G"), ("D", "H")]                       # cubane's carbon skeleton
    for bonds, sizes in ((naphthalene, [6, 6]), (cube, [4, 4, 4, 4, 4])):
        nodes, edges, p = ring_projection(bonds)
        res = execute(p, p.request("cycle_basis", scope=[("BOND", "forward")], certificate=True))
        assert sorted(c["length"] for c in res.answer["cycles"]) == sizes
        assert verify_result(res, nodes=nodes, edges=edges) == []


def test_forged_bases_are_refused():
    bonds = [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"), ("e", "c"), ("a", "e")]
    nodes, edges, p = ring_projection(bonds)
    res = execute(p, p.request("cycle_basis", scope=[("BOND", "forward")], certificate=True))
    assert res.answer["rank"] == 3 and verify_result(res, nodes=nodes, edges=edges) == []

    def forged(change):
        data = copy.deepcopy(thaw(res.to_dict()))
        change(data["answer"])
        return verify_result(reseal(data), nodes=nodes, edges=edges)

    def heavier(a):                                       # a heavier independent cycle in place of the first
        everything = {"b00", "b01", "b03", "b04", "b06"}  # a-b-c-d-e-a
        a["cycles"][0] = {"nodes": ["a", "b", "c", "d", "e"], "edges": sorted(everything), "length": 5, "weight": 5}
        a["weight"] = sum(c["weight"] for c in a["cycles"])
    assert forged(heavier)
    assert forged(lambda a: a["cycles"].__setitem__(1, copy.deepcopy(a["cycles"][0])))     # dependent
    assert forged(lambda a: a["cycles"][0]["edges"].pop())                                 # not a cycle
    assert forged(lambda a: a["certificate"]["witnesses"].reverse())                       # witnesses out of order
    assert forged(lambda a: a.__setitem__("rank", 2))


def test_an_interrupted_basis_lists_a_proven_prefix():
    rng = random.Random("basis interrupted")
    seen = set()
    for _ in range(300):
        nodes, edges, p, raw = run(rng)
        raw["certificate"] = True
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 30)}})
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        if res.status == INTERRUPTED:
            assert res.answer["weight"] is None
            seen.add(res.answer["count"] > 0)
    assert seen == {True, False}


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("basis revalidation")
    tally = {}
    for _ in range(300):
        nodes, edges, p, raw = run(rng)
        raw["certificate"] = True
        if raw["weights"] and "by_edge" in raw["weights"]:
            raw["weights"] = {"by_relation": {r: 1 for r in RELATIONS}}
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES)
        old = execute(p, {**raw, "projection": p.identity})
        verdict = revalidate(old, q, diff=diff(p, q)).verdict
        fresh = execute(q, {**raw, "projection": q.identity})
        if verdict == HOLDS:
            assert verify_result(old, nodes=nodes2, edges=edges2) == []
            assert (fresh.answer["rank"], total(fresh.answer)) == (old.answer["rank"], total(old.answer))
        elif verdict == FAILS:
            assert verify_result(old, nodes=nodes2, edges=edges2)
        tally[verdict] = tally.get(verdict, 0) + 1
    assert tally.get(HOLDS, 0) >= 20 and tally.get(FAILS, 0) >= 20, tally


def test_basis_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from graph_runtime import json_schema
    from graph_runtime._validation import shape_problems
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("basis schema")
    for _ in range(80):
        nodes, edges, p, raw = run(rng)
        for limits in ({}, {"max_expansions": rng.randint(0, 9)}):
            data = json.loads(json.dumps(thaw(execute(p, {**raw, "projection": p.identity, "limits": limits})
                                              .to_dict())))
            assert [e.message for e in validator.iter_errors(data)] == [] and shape_problems("result", data) == []


def test_a_witness_with_a_repeated_link_is_refused():
    """Three parallel links weighing 2, 1, 1: the minimum basis weighs 2 + 3 = 5. A forged basis of weight 6 with a
    witness that names one link twice (its parity would differ by how it is counted) is refused."""
    nodes = ["a", "b"]
    edges = [{"id": f"e{k}", "source": "a", "target": "b", "relation": "R"} for k in range(3)]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("cycle_basis", scope=[("R", "forward")], weights={"by_edge": {"e0": 2, "e1": 1,
                                                                                              "e2": 1}},
                               certificate=True))
    assert res.answer["weight"] == 5 and verify_result(res, nodes=nodes, edges=edges) == []
    data = thaw(res.to_dict())
    data["answer"]["cycles"] = [{"nodes": ["a", "b"], "edges": ["e0", "e2"], "length": 2, "weight": 3},
                                {"nodes": ["a", "b"], "edges": ["e0", "e1"], "length": 2, "weight": 3}]
    data["answer"]["weight"] = 6
    data["answer"]["certificate"] = {"kind": "witnesses", "witnesses": [["e0"], ["e0", "e0"]]}
    assert verify_result(reseal(data), nodes=nodes, edges=edges)


def test_an_exhausted_budget_allocates_no_witnesses():
    import tracemalloc
    nodes = ["a", "b"]
    edges = [{"id": f"e{k:05d}", "source": "a", "target": "b", "relation": "R"} for k in range(30000)]
    p = Projection.build(nodes=nodes, edges=edges)
    request = p.request("cycle_basis", scope=[("R", "forward")], limits={"max_expansions": 2})
    tracemalloc.start()
    res = execute(p, request)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert res.status == INTERRUPTED and res.answer["rank"] == 29999 and res.answer["count"] == 0
    assert peak < 40_000_000, peak                         # no k-by-m bitsets


def test_a_total_beyond_the_floats_is_written_exactly():
    nodes = ["a", "b"]
    edges = [{"id": "e0", "source": "a", "target": "b", "relation": "R"},
             {"id": "e1", "source": "a", "target": "b", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("cycle_basis", scope=[("R", "forward")], weights={"by_relation": {"R": 1e308}},
                               certificate=True))
    assert res.status == COMPLETED and res.answer["weight"] is None and res.answer["weight_exact"]
    assert contract_problems(res.to_dict()) == [] and verify_result(res, nodes=nodes, edges=edges) == []
    from graph_runtime import Result
    assert Result.from_dict(res.to_dict()).result_id == res.result_id
    q = Projection.build(nodes=nodes + ["c"], edges=edges)
    assert revalidate(res, q).verdict == HOLDS             # an isolated node: the rank stays 2 - 3 + 2 = 1
