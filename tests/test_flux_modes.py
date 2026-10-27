"""Elementary flux modes by double description. The oracle enumerates supports: a set J of reactions supports a mode
exactly when the columns of J have a one-dimensional kernel whose vector is non-zero on all of J (rank |J| - 1), and
that vector (or its negative) runs every irreversible reaction forwards; it is computed here by plain Fraction
elimination, independently of the runtime's algebra."""

import copy
import itertools
import json
import random
import time
from fractions import Fraction
from math import gcd

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from test_certificates import reseal

COEFFICIENTS = {"by_relation": {"CONSUMES": -1, "PRODUCES": 1}}
SCOPE = [("CONSUMES", "forward"), ("PRODUCES", "forward")]


def network(rng, n_species, n_reactions, density=0.35, stoich=(1, 1, 1, 2)):
    species = [{"id": f"s{i}", "types": ["Species"]} for i in range(n_species)]
    reactions = [{"id": f"r{j}", "types": ["Reaction"]} for j in range(n_reactions)]
    edges = []
    for j in range(n_reactions):
        for i in range(n_species):
            if rng.random() < density:
                coeff = rng.choice(stoich)
                for k in range(coeff):                        # a coefficient of 2 is two parallel edges
                    if rng.random() < 0.5:
                        edges.append({"id": f"c{i}-{j}-{k}", "source": f"s{i}", "target": f"r{j}",
                                      "relation": "CONSUMES"})
                    else:
                        edges.append({"id": f"p{i}-{j}-{k}", "source": f"r{j}", "target": f"s{i}",
                                      "relation": "PRODUCES"})
    return species, reactions, edges


def stoichiometry(species, reactions, edges):
    s = {(n["id"], r["id"]): 0 for n in species for r in reactions}
    for e in edges:
        if e["relation"] == "CONSUMES":
            s[(e["source"], e["target"])] -= 1
        else:
            s[(e["target"], e["source"])] += 1
    return s


def kernel_vector(columns, rows, s):
    """The kernel of S restricted to the columns, by Fraction Gauss-Jordan: one primitive integer vector when it is
    one-dimensional, else None."""
    m = [[Fraction(s[(u, c)]) for c in columns] for u in rows]
    pivots, r = [], 0
    for c in range(len(columns)):
        k = next((i for i in range(r, len(m)) if m[i][c] != 0), None)
        if k is None:
            continue
        m[r], m[k] = m[k], m[r]
        m[r] = [x / m[r][c] for x in m[r]]
        for i in range(len(m)):
            if i != r and m[i][c] != 0:
                f = m[i][c]
                m[i] = [a - f * b for a, b in zip(m[i], m[r])]
        pivots.append(c)
        r += 1
    free = [c for c in range(len(columns)) if c not in pivots]
    if len(free) != 1:
        return None
    f = free[0]
    vec = [Fraction(0)] * len(columns)
    vec[f] = Fraction(1)
    for i, c in enumerate(pivots):
        vec[c] = -m[i][f]
    den = 1
    for x in vec:
        den = den * x.denominator // gcd(den, x.denominator)
    ints = [int(x * den) for x in vec]
    g = 0
    for x in ints:
        g = gcd(g, x)
    return [x // g for x in ints]


def oracle(species, reactions, edges, reversible):
    s = stoichiometry(species, reactions, edges)
    rows = [n["id"] for n in species]
    ids = [r["id"] for r in reactions]
    modes = set()
    for size in range(1, len(ids) + 1):
        for cols in itertools.combinations(ids, size):
            vec = kernel_vector(cols, rows, s)
            if vec is None or any(x == 0 for x in vec):
                continue
            for sign in (1, -1):
                v = [sign * x for x in vec]
                if all(x > 0 or c in reversible for c, x in zip(cols, v)):
                    modes.add(tuple(sorted(zip(cols, v))))
    return sorted(modes, key=lambda m: (len(m), list(m)))


def build(nodes, edges):
    return Projection.build(nodes=nodes, edges=edges, relations=["CONSUMES", "PRODUCES"],
                            node_types=["Reaction", "Species"])


def request(p, reversible, **kw):
    return p.request("flux_modes", rows={"node_types": ["Species"]}, columns={"node_types": ["Reaction"]},
                     scope=SCOPE, coefficients=COEFFICIENTS, reversible=reversible, **kw)


def listed(res):
    return [tuple((x["reaction"], x["value"]) for x in m["vector"]) for m in res.answer["modes"]]


def test_modes_agree_with_support_enumeration_and_prove_themselves():
    rng = random.Random("flux modes")
    tally = {"none": 0, "some": 0, "reversible": 0}
    for _ in range(250):
        species, reactions, edges = network(rng, rng.randint(1, 5), rng.randint(1, 7))
        reversible = sorted(r["id"] for r in reactions if rng.random() < 0.3)
        p = build(species + reactions, edges)
        res = execute(p, request(p, reversible, certificate=True, limits={"max_expansions": 10 ** 7}))
        assert res.status == COMPLETED, res.error
        nodes = species + reactions
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        want = oracle(species, reactions, edges, set(reversible))
        assert listed(res) == want, (reversible, edges)
        assert res.answer["total"] == len(want)
        tally["some" if want else "none"] += 1
        tally["reversible"] += any(x < 0 for m in want for _, x in m)
    assert min(tally.values()) >= 30, tally


def test_a_textbook_network():
    """Uptake into A; A to B to C, or A straight to C; C exported. Irreversible: two routes. With A to B, B to C
    and A to C reversible, the internal cycle r1 + r2 - r3 runs either way too."""
    nodes = [{"id": x, "types": ["Species"]} for x in "ABC"] + [{"id": f"r{j}", "types": ["Reaction"]} for j in range(5)]
    edges = []
    for r, ins, outs in (("r0", "", "A"), ("r1", "A", "B"), ("r2", "B", "C"), ("r3", "A", "C"), ("r4", "C", "")):
        edges += [{"id": f"{x}>{r}", "source": x, "target": r, "relation": "CONSUMES"} for x in ins]
        edges += [{"id": f"{r}>{x}", "source": r, "target": x, "relation": "PRODUCES"} for x in outs]
    p = build(nodes, edges)
    assert listed(execute(p, request(p, []))) == [(("r0", 1), ("r3", 1), ("r4", 1)),
                                                 (("r0", 1), ("r1", 1), ("r2", 1), ("r4", 1))]
    assert listed(execute(p, request(p, ["r1", "r2", "r3"]))) == [
        (("r0", 1), ("r3", 1), ("r4", 1)), (("r1", -1), ("r2", -1), ("r3", 1)), (("r1", 1), ("r2", 1), ("r3", -1)),
        (("r0", 1), ("r1", 1), ("r2", 1), ("r4", 1))]


def test_forged_modes_are_refused():
    nodes = [{"id": x, "types": ["Species"]} for x in "AB"] + [{"id": f"r{j}", "types": ["Reaction"]} for j in range(3)]
    edges = [{"id": "r0>A", "source": "r0", "target": "A", "relation": "PRODUCES"},
             {"id": "A>r1", "source": "A", "target": "r1", "relation": "CONSUMES"},
             {"id": "r1>B", "source": "r1", "target": "B", "relation": "PRODUCES"},
             {"id": "B>r2", "source": "B", "target": "r2", "relation": "CONSUMES"}]
    p = build(nodes, edges)
    res = execute(p, request(p, [], certificate=True))
    assert listed(res) == [(("r0", 1), ("r1", 1), ("r2", 1))] and verify_result(res, nodes=nodes, edges=edges) == []

    def forged(change):
        data = copy.deepcopy(thaw(res.to_dict()))
        change(data["answer"]["modes"][0])
        return verify_result(reseal(data), nodes=nodes, edges=edges)
    assert forged(lambda m: m["vector"][1].__setitem__("value", 2))             # not balanced
    assert forged(lambda m: [e.__setitem__("value", 2) for e in m["vector"]])  # not primitive
    assert forged(lambda m: [e.__setitem__("value", -1) for e in m["vector"]])  # irreversible run backwards
    assert forged(lambda m: m["certificate"].__setitem__("columns", ["r0", "r0"]))
    assert forged(lambda m: m["certificate"].__setitem__("rows", ["A"]))


def test_an_interrupted_enumeration_lists_nothing():
    rng = random.Random("flux interrupted")
    seen = set()
    for _ in range(150):
        species, reactions, edges = network(rng, rng.randint(2, 5), rng.randint(3, 7))
        p = build(species + reactions, edges)
        res = execute(p, request(p, [], limits={"max_expansions": rng.randint(0, 80)}))
        assert contract_problems(res.to_dict()) == []
        assert verify_result(res, nodes=species + reactions, edges=edges) == []
        if res.status == INTERRUPTED and res.interruption["reason"] != "output_limit":
            assert res.answer["modes"] == () and res.answer["total"] is None
        seen.add(res.status)
    assert seen == {COMPLETED, INTERRUPTED}


def test_an_output_limit_keeps_the_total():
    rng = random.Random("flux limit")
    for _ in range(100):
        species, reactions, edges = network(rng, rng.randint(1, 4), rng.randint(3, 7))
        p = build(species + reactions, edges)
        want = oracle(species, reactions, edges, set())
        res = execute(p, request(p, [], limits={"max_results": 1, "max_expansions": 10 ** 7}))
        assert listed(res) == want[:1] and res.answer["total"] == len(want)
        assert (res.status == INTERRUPTED) is (len(want) > 1)
        assert contract_problems(res.to_dict()) == []


def test_flux_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    from graph_runtime._validation import shape_problems
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("flux schema")
    for _ in range(60):
        species, reactions, edges = network(rng, rng.randint(1, 4), rng.randint(1, 6))
        p = build(species + reactions, edges)
        for limits in ({}, {"max_expansions": rng.randint(0, 30)}, {"max_results": 1}):
            data = json.loads(json.dumps(thaw(execute(p, request(p, [], certificate=True, limits=limits)).to_dict())))
            assert [e.message for e in validator.iter_errors(data)] == [] and shape_problems("result", data) == []


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("flux revalidation")
    tally = {}
    for _ in range(200):
        species, reactions, edges = network(rng, rng.randint(1, 4), rng.randint(2, 6))
        edges2 = [dict(e) for e in edges]
        if edges2 and rng.random() < 0.7:
            edges2.pop(rng.randrange(len(edges2)))
        else:
            r, s = rng.choice(reactions)["id"], rng.choice(species)["id"]
            edges2.append({"id": f"new-{r}-{s}", "source": r, "target": s, "relation": "PRODUCES"})
        nodes = species + reactions
        p, q = build(nodes, edges), build(nodes, edges2)
        old = execute(p, request(p, [], certificate=True))
        verdict = revalidate(old, q, diff=diff(p, q)).verdict
        fresh = execute(q, request(q, [], certificate=True))
        if verdict == FAILS:
            assert verify_result(old, nodes=nodes, edges=edges2)
        tally[verdict] = tally.get(verdict, 0) + 1
    assert tally.get(FAILS, 0) >= 20, tally


def test_a_larger_network_within_seconds():
    """A layered network of 12 species and 24 reactions with branching: its modes, enumerated and proven."""
    rng = random.Random("flux larger")
    species, reactions, edges = network(rng, 12, 24, density=0.12, stoich=(1,))
    p = build(species + reactions, edges)
    start = time.perf_counter()
    res = execute(p, request(p, [], certificate=True, limits={"max_expansions": 10 ** 8, "max_results": 10 ** 6}))
    assert res.status == COMPLETED and time.perf_counter() - start < 30
    assert verify_result(res, nodes=species + reactions, edges=edges) == []


def test_a_zero_matrix_is_charged_before_its_modes_are_built():
    """No species: every reaction alone is a mode (its column is zero). Building the 30,000 starting rays is charged,
    so a zero budget, a timeout or a cancellation stops it before they exist."""
    n = 30_000
    reactions = [{"id": f"r{j:05d}", "types": ["Reaction"]} for j in range(n)]
    p = build(reactions, [])
    for kw in ({"limits": {"max_expansions": 0}}, {"limits": {"max_expansions": 10 ** 9}}):
        start = time.perf_counter()
        res = execute(p, request(p, [], **kw))
        if kw["limits"]["max_expansions"] == 0:
            assert res.status == INTERRUPTED and res.answer["total"] is None and time.perf_counter() - start < 1
        else:
            assert res.answer["total"] == n
    cancelled = execute(p, request(p, []), cancel=lambda: True)
    assert cancelled.status == INTERRUPTED and cancelled.answer["total"] is None


def test_a_new_mode_keeps_revalidation_from_holding():
    """S = [[1, -1, 0], [0, 0, 1]] has one mode (r0, r1); dropping r2's last entry adds the mode r2 alone, while the
    old mode and its minor still check: the cached list is not claimed complete."""
    nodes = [{"id": "s0", "types": ["Species"]}, {"id": "s1", "types": ["Species"]}] + \
        [{"id": f"r{j}", "types": ["Reaction"]} for j in range(3)]
    edges = [{"id": "a", "source": "r0", "target": "s0", "relation": "PRODUCES"},
             {"id": "b", "source": "s0", "target": "r1", "relation": "CONSUMES"},
             {"id": "c", "source": "r2", "target": "s1", "relation": "PRODUCES"}]
    p, q = build(nodes, edges), build(nodes, edges[:2])
    old = execute(p, request(p, [], certificate=True))
    assert listed(old) == [(("r0", 1), ("r1", 1))]
    assert revalidate(old, q).verdict != HOLDS
    assert execute(q, request(q, [])).answer["total"] == 2


@pytest.mark.parametrize("shape", ["empty", "identity"])
def test_sparse_and_full_rank_networks_stay_within_their_controls(shape):
    """10,000 species and reactions with no edges (an empty matrix), or an identity (full column rank: no ray at
    all): splitting reads only entries, and an empty ray set ends the enumeration at once."""
    n = 10_000 if shape == "empty" else 4000
    species = [{"id": f"s{i:05d}", "types": ["Species"]} for i in range(n)]
    reactions = [{"id": f"r{j:05d}", "types": ["Reaction"]} for j in range(n)]
    edges = [] if shape == "empty" else [{"id": f"e{i:05d}", "source": f"r{i:05d}", "target": f"s{i:05d}",
                                         "relation": "PRODUCES"} for i in range(n)]
    p = build(species + reactions, edges)
    start = time.perf_counter()
    res = execute(p, request(p, [], limits={"max_expansions": n}))
    assert time.perf_counter() - start < 2
    if shape == "identity":
        done = execute(p, request(p, [], limits={"max_expansions": 10 ** 8}))
        assert done.status == COMPLETED and done.answer["total"] == 0


def test_proofs_are_charged_and_checked():
    """7,000 reactions with zero columns, proven: each singleton mode's proof is charged before it is built, so a
    deadline stops the proofs too."""
    n = 7000
    species = [{"id": f"s{i:05d}", "types": ["Species"]} for i in range(n)]
    reactions = [{"id": f"r{j:05d}", "types": ["Reaction"]} for j in range(n)]
    p = build(species + reactions, [])
    stamps = []
    start = time.perf_counter()
    res = execute(p, request(p, [], certificate=True, limits={"max_expansions": 10 ** 8}), check_interval=1,
                  cancel=lambda: stamps.append(time.perf_counter()) and False)
    assert res.status in (COMPLETED, INTERRUPTED)
    assert max(b - a for a, b in zip(stamps, stamps[1:])) < 0.5 and time.perf_counter() - stamps[-1] < 0.5
