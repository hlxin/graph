"""Incidence: the exact rank and canonical kernel bases of the matrix of edge coefficients between row and column
nodes. The oracle is sympy: the matrix built from the records, its rank, and its nullspace (sympy's basis is the
reduced row echelon one, a 1 at each free coordinate), each vector scaled to a primitive integer vector, compared
entry by entry. Certificates are checked by verify_result, and forged vectors and minors refused."""

import random
from fractions import Fraction
from math import gcd

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.tools import summarize
from test_certificates import reseal

sympy = pytest.importorskip("sympy")


def chosen(nodes, spec):
    out = []
    for n in nodes:
        nid = n if isinstance(n, str) else n["id"]
        types = set() if isinstance(n, str) else set(n.get("types") or ())
        if (spec.get("nodes") is None or nid in spec["nodes"]) and (
                spec.get("node_types") is None or types & set(spec["node_types"])):
            out.append(nid)
    return sorted(out)


def sympy_answer(nodes, edges, raw):
    """(rank, left kernel, right kernel) by sympy, each kernel as [[node, value], ...] vectors."""
    rows, cols = chosen(nodes, raw["rows"]), chosen(nodes, raw["columns"])
    pairs = {(s["relation"], s["direction"]) for s in raw["scope"]}
    (kind, table), = raw["coefficients"].items()
    m = sympy.zeros(len(rows), len(cols))
    ri, ci = {r: i for i, r in enumerate(rows)}, {c: j for j, c in enumerate(cols)}
    for e in edges:
        if not ((e["relation"], "forward") in pairs or (e["relation"], "reverse") in pairs):
            continue
        a, b = e["source"], e["target"]
        if a in ci and b in ri:
            a, b = b, a
        if a in ri and b in ci:
            x = Fraction(table[e["relation"] if kind == "by_relation" else e["id"]])
            m[ri[a], ci[b]] += sympy.Rational(x.numerator, x.denominator)

    def basis(mat, names):
        out = []
        for v in mat.nullspace():
            vals = [Fraction(int(x.p), int(x.q)) for x in v]
            den = 1
            for x in vals:
                den = den * x.denominator // gcd(den, x.denominator)
            ints = [int(x * den) for x in vals]
            g = 0
            for x in ints:
                g = gcd(g, x)
            out.append([[names[k], x // g] for k, x in enumerate(ints) if x])
        return out
    return m.rank(), basis(m.T, rows), basis(m, cols)


def network(rng, n_species, n_reactions):
    species = [f"S{i:02d}" for i in range(n_species)]
    reactions = [f"R{j:02d}" for j in range(n_reactions)]
    nodes = [{"id": s, "types": ["Species"]} for s in species] + [{"id": r, "types": ["Reaction"]} for r in reactions]
    edges = []
    for r in reactions:
        for s in rng.sample(species, rng.randint(1, min(3, n_species))):
            rel = rng.choice(["CONSUMES", "PRODUCES"])
            edges.append({"id": f"{r}-{s}-{len(edges)}", "source": r, "target": s, "relation": rel})
            if rng.random() < 0.1:                       # a parallel edge adds to the same entry
                edges.append({"id": f"{r}-{s}-{len(edges)}", "source": s, "target": r, "relation": rel})
    return nodes, edges


def test_incidence_matches_sympy():
    rng = random.Random("incidence")
    laws = 0
    for _ in range(250):
        nodes, edges = network(rng, rng.randint(1, 8), rng.randint(1, 8))
        p = Projection.build(nodes=nodes, edges=edges, relations=["CONSUMES", "PRODUCES"],
                             node_types=["Reaction", "Species"])
        coefficients = rng.choice([{"by_relation": {"CONSUMES": -1, "PRODUCES": 1}},
                                   {"by_relation": {"CONSUMES": -0.5, "PRODUCES": 2}},
                                   {"by_edge": {e["id"]: rng.choice([-3, -2, -1, 1, 2, 0.25]) for e in edges}}])
        raw = {"rows": {"node_types": ["Species"], "nodes": None}, "columns": {"node_types": ["Reaction"], "nodes": None},
               "scope": [{"relation": "CONSUMES", "direction": "forward"},
                         {"relation": "PRODUCES", "direction": "reverse"}], "coefficients": coefficients}
        res = execute(p, p.request("incidence", kernels=["left", "right"], certificate=rng.random() < 0.7,
                                   **{k: v for k, v in raw.items()}))
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        rank, left, right = sympy_answer(nodes, edges, raw)
        got = {k: [[[e["node"], e["value"]] for e in vec] for vec in res.answer[k]]
               for k in ("left_kernel", "right_kernel")}
        assert (res.answer["rank"], got["left_kernel"], got["right_kernel"]) == (rank, left, right)
        laws += len(left)
    assert laws > 300


def test_an_interrupted_reduction_claims_nothing():
    rng = random.Random("incidence-limits")
    stopped = 0
    for _ in range(100):
        nodes, edges = network(rng, rng.randint(2, 6), rng.randint(2, 6))
        p = Projection.build(nodes=nodes, edges=edges, relations=["CONSUMES", "PRODUCES"],
                             node_types=["Reaction", "Species"])
        res = execute(p, p.request("incidence", rows={"node_types": ["Species"]}, columns={"node_types": ["Reaction"]},
                                   scope=[("CONSUMES", "forward"), ("PRODUCES", "forward")],
                                   coefficients={"by_relation": {"CONSUMES": -1, "PRODUCES": 1}}, kernels=["left"],
                                   certificate=True, limits={"max_expansions": rng.randint(0, 8)}))
        assert verify_result(res, nodes=nodes, edges=edges) == []
        if res.status == INTERRUPTED:
            stopped += 1
            assert res.answer["rank"] is None and res.answer["left_kernel"] is None
    assert stopped > 30


NODES = [{"id": x, "types": ["Species"]} for x in "ABCD"] + [{"id": x, "types": ["Reaction"]} for x in ("R1", "R2", "R3")]
EDGES = [{"id": f"s{k + 1}", "source": s, "target": t, "relation": r} for k, (s, t, r) in enumerate(
    [("R1", "A", "CONSUMES"), ("R1", "B", "CONSUMES"), ("R1", "C", "PRODUCES"), ("R2", "C", "CONSUMES"),
     ("R2", "A", "PRODUCES"), ("R2", "B", "PRODUCES"), ("R3", "C", "CONSUMES"), ("R3", "D", "PRODUCES")])]


def forged(res, change):
    data = res.to_dict()
    change(data)
    return reseal(data)


def test_forged_bases_and_minors_are_refused():
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("incidence", rows={"node_types": ["Species"]}, columns={"node_types": ["Reaction"]},
                               scope=[("CONSUMES", "forward"), ("PRODUCES", "forward")],
                               coefficients={"by_relation": {"CONSUMES": -1, "PRODUCES": 1}}, kernels=["left"],
                               certificate=True))
    assert verify_result(res, nodes=NODES, edges=EDGES) == []

    def wrong_law(data):                                 # not conserved: A + C
        data["answer"]["left_kernel"][1] = [{"node": "A", "value": 1}, {"node": "C", "value": 1}]
    assert verify_result(forged(res, wrong_law), nodes=NODES, edges=EDGES) != []

    def dependent(data):                                 # the same law twice: not a basis
        data["answer"]["left_kernel"][1] = [{"node": "A", "value": -1}, {"node": "B", "value": 1}]
    assert verify_result(forged(res, dependent), nodes=NODES, edges=EDGES) != []

    def singular(data):                                  # a minor whose determinant is zero
        data["answer"]["certificate"]["minor"] = {"rows": ["A", "B"], "columns": ["R1", "R2"]}
    assert verify_result(forged(res, singular), nodes=NODES, edges=EDGES) != []

    def overrank(data):                                  # rank 3 with a 3 by 3 minor that is singular
        a = data["answer"]
        a["rank"] = 3
        a["left_kernel"] = a["left_kernel"][:1]
        a["certificate"]["minor"] = {"rows": ["A", "B", "C"], "columns": ["R1", "R2", "R3"]}
        a["certificate"]["kernel"] = []
    assert verify_result(forged(res, overrank), nodes=NODES, edges=EDGES) != []


def test_a_float_entry_cannot_pass_for_an_integer():
    """A forged rank of 1 for a matrix whose determinant is -1: a kernel vector [1.0, 1] would multiply to zero in
    floating point (2^53 + 1 rounds to 2^53), so entries must be integers and the products exact."""
    from graph_runtime.result import contract_problems
    big = 2 ** 53
    nodes = [{"id": "r1", "types": ["Row"]}, {"id": "r2", "types": ["Row"]}, {"id": "c1", "types": ["Column"]},
             {"id": "c2", "types": ["Column"]}]
    edges = [{"id": "a", "source": "r1", "target": "c1", "relation": "R"},
             {"id": "b", "source": "r1", "target": "c2", "relation": "R"},
             {"id": "c", "source": "r2", "target": "c1", "relation": "R"},
             {"id": "d", "source": "r2", "target": "c2", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("incidence", rows={"node_types": ["Row"]}, columns={"node_types": ["Column"]},
                               scope=[("R", "forward")], kernels=["right"], certificate=True,
                               coefficients={"by_edge": {"a": big + 1, "b": -big, "c": 1, "d": -1}}))
    assert res.answer["rank"] == 2 and verify_result(res, nodes=nodes, edges=edges) == []

    def forge(data):
        a = data["answer"]
        a["rank"] = 1
        a["right_kernel"] = [[{"node": "c1", "value": 1.0}, {"node": "c2", "value": 1}]]
        a["certificate"]["minor"] = {"rows": ["r2"], "columns": ["c1"]}
    bad = forged(res, forge)
    assert contract_problems(bad) and verify_result(bad, nodes=nodes, edges=edges)


def test_summaries_and_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    p = Projection.build(nodes=NODES, edges=EDGES)
    for extra in ({"kernels": ["left", "right"]}, {"certificate": True}, {"limits": {"max_expansions": 1}}):
        res = execute(p, p.request("incidence", rows={"node_types": ["Species"]}, columns={"node_types": ["Reaction"]},
                                   scope=[("CONSUMES", "forward"), ("PRODUCES", "forward")],
                                   coefficients={"by_relation": {"CONSUMES": -1, "PRODUCES": 1}}, **extra))
        assert list(validator.iter_errors(res.to_dict())) == []
        assert summarize(res)["summary"]["rank"] == res.answer["rank"]


@pytest.mark.parametrize("extra", [{"kernels": ["up"]}, {"kernels": "left"}, {"rows": {"node_types": None}},
                                   {"coefficients": {"by_relation": {"CONSUMES": float("nan")}}},
                                   {"columns": {"nodes": ["A", "R1"]}}])
def test_requests_are_checked(extra):
    p = Projection.build(nodes=NODES, edges=EDGES)
    raw = {"operation": "incidence", "projection": p.identity, "rows": {"node_types": ["Species"]},
           "columns": {"node_types": ["Reaction"]}, "scope": [("CONSUMES", "forward"), ("PRODUCES", "forward")],
           "coefficients": {"by_relation": {"CONSUMES": -1, "PRODUCES": 1}}, **extra}
    res = execute(p, raw)
    assert res.status == "failed" and res.error["code"] in ("invalid_request", "non_finite_cost")
