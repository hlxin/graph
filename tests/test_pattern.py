"""Tree patterns: the rows of a match and its steps, in canonical order, found without dead ends, counted exactly
without listing them, under the same limits and witnesses as every operation."""

import random
import time

import pytest

from graph_runtime import (COMPLETED, FAILED, INTERRUPTED, CancelToken, Projection, Result, execute, json_schema,
                           normalize_request, verify_result)
from graph_runtime.errors import RequestError
from oracle import RELATIONS, TYPES, node_id, pattern_rows, random_graph, random_scope

NODES = [{"id": "t1", "types": ["Test"]}, {"id": "t2", "types": ["Test"]}, {"id": "s1", "types": ["Sample"]},
         {"id": "s2", "types": ["Sample"]}, {"id": "c1", "types": ["Catalyst"]}, {"id": "cu", "types": ["Species"]},
         {"id": "zn", "types": ["Species"]}]
EDGES = [{"id": "u1", "source": "t1", "target": "s1", "relation": "USES"},
         {"id": "u2", "source": "t2", "target": "s2", "relation": "USES"},
         {"id": "u3", "source": "t1", "target": "s2", "relation": "USES"},
         {"id": "f1", "source": "s1", "target": "c1", "relation": "HAS_FORMULATION"},
         {"id": "h1", "source": "c1", "target": "cu", "relation": "HAS_COMPONENT"},
         {"id": "h2", "source": "c1", "target": "zn", "relation": "HAS_COMPONENT"},
         {"id": "h3", "source": "c1", "target": "zn", "relation": "HAS_COMPONENT"}]        # parallel to h2
TESTS = {"var": "test", "node_types": ["Test"]}
USES = {"from": "test", "var": "sample", "scope": [("USES", "forward")], "edge_var": "use"}
FORMULATION = {"from": "sample", "var": "catalyst", "scope": [("HAS_FORMULATION", "forward")]}
COMPONENT = {"from": "catalyst", "var": "species", "scope": [("HAS_COMPONENT", "forward")]}


@pytest.fixture
def p():
    return Projection.build(nodes=NODES, edges=EDGES)


def run(p, **params):
    res = execute(p, p.request("pattern", **params))
    assert verify_result(res, nodes=NODES, edges=EDGES) == []
    assert Result.from_dict(res.to_dict()).result_id == res.result_id
    return res


def table(res):
    return [(dict(r["nodes"]), list(r["links"])) for r in res.answer["rows"]]


def test_a_chain_lists_its_rows_in_canonical_order(p):
    res = run(p, match=TESTS, steps=[USES, FORMULATION, COMPONENT])
    assert res.status == COMPLETED and res.exists is True and res.answer["total"] == 3
    assert table(res) == [
        ({"test": "t1", "sample": "s1", "catalyst": "c1", "species": "cu"}, ["u1", "f1", "h1"]),
        ({"test": "t1", "sample": "s1", "catalyst": "c1", "species": "zn"}, ["u1", "f1", "h2"]),   # parallel edges:
        ({"test": "t1", "sample": "s1", "catalyst": "c1", "species": "zn"}, ["u1", "f1", "h3"])]   # two rows
    assert res.answer["rows"][0]["edges"] == {"use": "u1"}
    assert res.answer["coverage"]["vars"][0] == {"var": "test", "candidates": 2, "kept": 1}       # t2 pruned


def test_optional_steps_keep_their_rows(p):
    res = run(p, match=TESTS, steps=[USES, {**FORMULATION, "optional": True}, {**COMPONENT, "optional": True,
                                                                               "node_types": ["Species"]}])
    assert table(res) == [
        ({"test": "t1", "sample": "s1", "catalyst": "c1", "species": "cu"}, ["u1", "f1", "h1"]),
        ({"test": "t1", "sample": "s1", "catalyst": "c1", "species": "zn"}, ["u1", "f1", "h2"]),
        ({"test": "t1", "sample": "s1", "catalyst": "c1", "species": "zn"}, ["u1", "f1", "h3"]),
        ({"test": "t1", "sample": "s2", "catalyst": None, "species": None}, ["u3", None, None]),
        ({"test": "t2", "sample": "s2", "catalyst": None, "species": None}, ["u2", None, None])]
    assert res.answer["total"] == 5


def test_branches_multiply_and_constraints_narrow(p):
    steps = [USES, {"from": "test", "var": "other", "scope": [("USES", "forward")]}]
    res = run(p, match={"var": "test", "nodes": ["t1"]}, steps=steps)
    assert res.answer["total"] == 4                               # 2 x 2 from t1
    res = run(p, match=TESTS, steps=[USES, FORMULATION, {**COMPONENT, "nodes": ["zn"]}])
    assert [r[1] for r in table(res)] == [["u1", "f1", "h2"], ["u1", "f1", "h3"]]
    res = run(p, match=TESTS, steps=[USES, FORMULATION, {**COMPONENT, "edges": ["h3"]}])
    assert [r[1] for r in table(res)] == [["u1", "f1", "h3"]]
    res = run(p, match=TESTS, steps=[{"from": "test", "var": "s", "scope": [("USES", "forward")]},
                                      {"from": "s", "var": "back", "scope": [("USES", "reverse")]}])
    assert ({"test": "t1", "s": "s2", "back": "t2"}, ["u3", "u2"]) in table(res)    # a variable may revisit a node
    res = run(p, match=TESTS, steps=[USES, FORMULATION, {**COMPONENT, "node_types": ["Test"]}])
    assert (res.status, res.exists, res.answer["total"], table(res)) == (COMPLETED, False, 0, [])


def test_counting_and_existence_list_nothing(p):
    for answer, want in (("count", 3), ("exists", 3)):
        res = run(p, match=TESTS, steps=[USES, FORMULATION, COMPONENT], answer=answer)
        assert res.answer["total"] == want and res.answer["rows"] == () and res.exists is True


def star(fan):
    """A hub with ``fan`` spokes, each spoke with ``fan`` leaves: a pattern of three steps matches fan**3 rows."""
    nodes = [{"id": "hub", "types": ["Hub"]}]
    edges = []
    for i in range(fan):
        nodes.append(f"spoke{i:04d}")
        edges.append({"id": f"a{i:04d}", "source": "hub", "target": f"spoke{i:04d}", "relation": "R"})
        for j in range(fan):
            leaf = f"leaf{i:04d}-{j:04d}"
            nodes.append(leaf)
            edges.append({"id": f"b{i:04d}-{j:04d}", "source": f"spoke{i:04d}", "target": leaf, "relation": "R"})
    return Projection.build(nodes=nodes, edges=edges)


def test_counts_far_beyond_what_could_be_listed():
    p = star(60)
    steps = [{"from": "h", "var": "s1", "scope": [("R", "forward")]},
             {"from": "s1", "var": "l1", "scope": [("R", "forward")]},
             {"from": "h", "var": "s2", "scope": [("R", "forward")]},
             {"from": "s2", "var": "l2", "scope": [("R", "forward")]}]
    t = time.perf_counter()
    res = execute(p, p.request("pattern", match={"var": "h", "node_types": ["Hub"]}, steps=steps, answer="count"))
    assert res.answer["total"] == 60**4 and time.perf_counter() - t < 2      # 12,960,000 rows, none listed
    res = execute(p, p.request("pattern", match={"var": "h", "node_types": ["Hub"]}, steps=steps,
                               limits={"max_results": 10}))
    assert res.status == INTERRUPTED and res.interruption["reason"] == "output_limit"
    assert (res.answer["count"], res.answer["total"]) == (10, 60**4)


def test_no_dead_ends_are_explored():
    """A selective last step: naive step-by-step expansion builds every intermediate row; the reduced search scans
    each candidate once and enumerates only the rows that complete."""
    nodes = [{"id": "root", "types": ["Root"]}, {"id": "goal", "types": ["Goal"]}]
    edges = []
    for i in range(300):
        nodes.append(f"m{i:03d}")
        edges.append({"id": f"a{i:03d}", "source": "root", "target": f"m{i:03d}", "relation": "R"})
        for j in range(20):
            nodes.append(f"m{i:03d}-{j:02d}")
            edges.append({"id": f"b{i:03d}-{j:02d}", "source": f"m{i:03d}", "target": f"m{i:03d}-{j:02d}",
                          "relation": "R"})
    edges.append({"id": "z", "source": "m007-03", "target": "goal", "relation": "R"})
    p = Projection.build(nodes=nodes, edges=edges)
    steps = [{"from": "r", "var": "a", "scope": [("R", "forward")]}, {"from": "a", "var": "b", "scope": [("R", "forward")]},
             {"from": "b", "var": "g", "scope": [("R", "forward")], "node_types": ["Goal"]}]
    res = execute(p, p.request("pattern", match={"var": "r", "node_types": ["Root"]}, steps=steps))
    assert [list(r["links"]) for r in res.answer["rows"]] == [["a007", "b007-03", "z"]]
    naive = 300 + 300 * 20                     # the partial rows a step-by-step expansion would build
    assert res.stats["expansions"] <= 1 + 300 + 6000 and res.answer["coverage"]["vars"][1]["kept"] == 1
    assert naive > 50 * res.answer["count"]


def test_limits_never_claim_absence(p):
    res = execute(p, p.request("pattern", match=TESTS, steps=[USES, FORMULATION], limits={"max_expansions": 1}))
    assert (res.status, res.exists, res.answer["total"]) == (INTERRUPTED, None, None)
    token = CancelToken()
    token.cancel()
    res = execute(p, p.request("pattern", match=TESTS, steps=[USES]), cancel=token)
    assert res.status == INTERRUPTED and res.exists is None
    res = execute(p, p.request("pattern", match=TESTS, steps=[USES], limits={"max_results": 3}))
    assert res.status == COMPLETED and res.answer["count"] == 3                    # a full answer that fits


@pytest.mark.parametrize("params,code", [
    ({"match": {"var": "t"}}, "invalid_request"),
    ({"match": {"var": "1t", "node_types": ["Test"]}}, "invalid_request"),
    ({"match": TESTS, "steps": [{**USES, "from": "nowhere"}]}, "invalid_request"),
    ({"match": TESTS, "steps": [USES, {**USES, "from": "test"}]}, "invalid_request"),         # var bound twice
    ({"match": TESTS, "steps": [{**USES, "optional": True}, FORMULATION]}, "invalid_request"),
    ({"match": TESTS, "steps": [USES, {**FORMULATION, "from": "use"}]}, "invalid_request"),   # from an edge var
    ({"match": TESTS, "answer": "all"}, "invalid_request"),
    ({"match": TESTS, "steps": [{**USES, "scope": []}]}, "invalid_request"),
])
def test_malformed_patterns(p, params, code):
    with pytest.raises(RequestError) as err:
        normalize_request({"operation": "pattern", "projection": p.identity, **params})
    assert err.value.code == code


@pytest.mark.parametrize("params,code", [
    ({"match": {"var": "t", "nodes": ["zz"]}}, "unknown_node"),
    ({"match": {"var": "t", "node_types": ["Tset"]}}, "unknown_node_type"),
    ({"match": TESTS, "steps": [{**USES, "scope": [("USED", "forward")]}]}, "unknown_relation"),
    ({"match": TESTS, "steps": [{**USES, "edges": ["u9"]}]}, "unknown_edge"),
])
def test_patterns_naming_what_the_projection_lacks_fail(p, params, code):
    res = execute(p, {"operation": "pattern", "projection": p.identity, **params})
    assert (res.status, res.error["code"]) == (FAILED, code)


def random_pattern(rng, ids):
    names = ["v0"]
    steps = []
    optional_vars = set()
    for i in range(rng.randint(0, 4)):
        parent = rng.choice(names)
        optional = parent in optional_vars or rng.random() < 0.3
        var = f"v{i + 1}"
        step = {"from": parent, "var": var, "scope": random_scope(rng), "optional": optional,
                "node_types": rng.choice([None, None, rng.sample(TYPES, rng.randint(1, 2))]),
                "nodes": rng.choice([None, None, None, rng.sample(ids, rng.randint(1, len(ids)))])}
        if rng.random() < 0.3:
            step["edge_var"] = f"e{i + 1}"
        steps.append(step)
        names.append(var)
        if optional:
            optional_vars.add(var)
    match = {"var": "v0", "node_types": rng.choice([None, rng.sample(TYPES, rng.randint(1, 3))]),
             "nodes": None}
    if match["node_types"] is None:
        match["nodes"] = rng.sample(ids, rng.randint(1, len(ids)))
    return match, steps


def test_patterns_match_brute_force_row_for_row():
    rng = random.Random("patterns")
    checked = 0
    for _ in range(400):
        nodes, edges = random_graph(rng, max_nodes=6, max_edges=12)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(n) for n in nodes]
        match, steps = random_pattern(rng, ids)
        want = pattern_rows(nodes, edges, match, steps)
        res = execute(p, p.request("pattern", match=match, steps=steps, limits={"max_results": 10**6}))
        assert res.status == COMPLETED, res.error
        assert table(res) == [(dict(b), links) for b, links in want]
        assert res.answer["total"] == len(want)
        count = execute(p, p.request("pattern", match=match, steps=steps, answer="count"))
        assert count.answer["total"] == len(want) and count.exists is bool(want)
        assert verify_result(res, nodes=nodes, edges=edges) == []
        checked += len(want)
    assert checked > 500


def test_pattern_results_validate_against_the_schema(p):
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    for answer in ("rows", "count", "exists"):
        res = execute(p, p.request("pattern", match=TESTS, steps=[USES, {**FORMULATION, "optional": True}],
                                   answer=answer))
        assert list(validator.iter_errors(res.to_dict())) == []
    res = execute(p, p.request("pattern", match=TESTS, steps=[USES], limits={"max_expansions": 0}))
    assert list(validator.iter_errors(res.to_dict())) == []
