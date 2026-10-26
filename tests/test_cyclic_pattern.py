"""Patterns with cycles: a step with ``to`` instead of ``var`` needs a hop between two variables bound before it. Rows
come by a generic join (each binding intersects what every closing step allows, from the smallest side) in the same
canonical order as tree patterns; a brute-force enumerator over the edges, with no pruning and no join, is the
oracle, row for row."""

import random

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, normalize_request, verify_result
from graph_runtime.errors import RequestError
from oracle import RELATIONS, TYPES, node_id, pattern_rows, random_graph, random_scope

import pytest


def random_cyclic(rng, ids):
    names, steps = ["v0"], []
    optional = set()
    for i in range(rng.randint(1, 3)):
        parent = rng.choice(names)
        opt = parent in optional or rng.random() < 0.15
        step = {"from": parent, "var": f"v{i + 1}", "scope": random_scope(rng), "optional": opt,
                "node_types": rng.choice([None, None, rng.sample(TYPES, 1)])}
        if rng.random() < 0.3:
            step["edge_var"] = f"e{i + 1}"
        steps.append(step)
        names.append(f"v{i + 1}")
        if opt:
            optional.add(f"v{i + 1}")
    required = [n for n in names if n not in optional]
    for k in range(rng.randint(1, 2)):
        closing = {"from": rng.choice(required), "to": rng.choice(required), "scope": random_scope(rng)}
        if rng.random() < 0.3:
            closing["edge_var"] = f"c{k}"
        steps.insert(rng.randint(len(steps) - sum(1 for s in steps if "to" in s), len(steps)), closing)
    # a closing step goes after both its variables are bound
    bound, ordered, pending = {"v0"}, [], list(steps)
    while pending:
        for s in pending:
            if ("to" not in s and s["from"] in bound) or ("to" in s and s["from"] in bound and s["to"] in bound):
                ordered.append(s)
                pending.remove(s)
                if "var" in s:
                    bound.add(s["var"])
                break
    match = {"var": "v0", "node_types": rng.choice([None, rng.sample(TYPES, 2)]), "nodes": None}
    if match["node_types"] is None:
        match["nodes"] = rng.sample(ids, rng.randint(1, len(ids)))
    return match, ordered


def table(res):
    return [(dict(r["nodes"]), list(r["links"])) for r in res.answer["rows"]]


def test_cyclic_patterns_match_brute_force_row_for_row():
    rng = random.Random("cyclic-patterns")
    rows = cycles = 0
    for _ in range(300):
        nodes, edges = random_graph(rng, max_nodes=6, max_edges=16)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        match, steps = random_cyclic(rng, [node_id(n) for n in nodes])
        want = pattern_rows(nodes, edges, match, steps)
        if len(want) > 50_000:                            # a few random patterns explode; they prove nothing more
            continue
        res = execute(p, p.request("pattern", match=match, steps=steps, limits={"max_results": 10**6}))
        assert res.status == COMPLETED, res.error
        assert table(res) == [(dict(b), links) for b, links in want], (match, steps)
        assert res.answer["total"] == len(want) and res.exists is bool(want)
        for answer in ("count", "exists"):
            other = execute(p, p.request("pattern", match=match, steps=steps, answer=answer))
            assert other.answer["total"] == len(want) and other.answer["rows"] == ()
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        rows += len(want)
        cycles += bool(want)
    assert rows > 400 and cycles > 75


def test_limits_list_a_prefix_and_never_deny():
    rng = random.Random("cyclic-limits")
    unknown_totals = 0
    for _ in range(300):
        nodes, edges = random_graph(rng, max_nodes=6, max_edges=16)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        match, steps = random_cyclic(rng, [node_id(n) for n in nodes])
        want = pattern_rows(nodes, edges, match, steps)
        limits = {"max_expansions": rng.randint(0, 40), "max_results": rng.randint(0, 4)}
        res = execute(p, p.request("pattern", match=match, steps=steps, limits=limits))
        assert verify_result(res, nodes=nodes, edges=edges) == []
        got = table(res)
        assert got == [(dict(b), links) for b, links in want][:len(got)]          # a prefix, in canonical order
        if res.status == INTERRUPTED:
            assert res.exists in (True, None) and (res.exists is None or want)
            assert res.answer["total"] in (None, len(want))
            unknown_totals += res.answer["total"] is None and res.exists is True
        else:
            assert res.answer["total"] == len(want)
    assert unknown_totals >= 10


NODES = [{"id": "claim", "types": ["Claim"]}, {"id": "m1", "types": ["Measurement"]},
         {"id": "m2", "types": ["Measurement"]}, {"id": "m3", "types": ["Measurement"]},
         {"id": "d1", "types": ["Dataset"]}, {"id": "d2", "types": ["Dataset"]}]
EDGES = [{"id": "s1", "source": "claim", "target": "m1", "relation": "SUPPORTED_BY"},
         {"id": "s2", "source": "claim", "target": "m2", "relation": "SUPPORTED_BY"},
         {"id": "s3", "source": "claim", "target": "m3", "relation": "SUPPORTED_BY"},
         {"id": "i1", "source": "m1", "target": "d1", "relation": "IN"},
         {"id": "i2", "source": "m2", "target": "d1", "relation": "IN"},
         {"id": "i3", "source": "m3", "target": "d2", "relation": "IN"}]


def test_measurements_of_one_claim_from_the_same_dataset():
    """A diamond: two supports of a claim whose measurements come from one dataset (a variable may bind the same
    node as another, so each measurement also pairs with itself)."""
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("pattern", match={"var": "c", "node_types": ["Claim"]}, steps=[
        {"from": "c", "var": "a", "scope": [("SUPPORTED_BY", "forward")]},
        {"from": "c", "var": "b", "scope": [("SUPPORTED_BY", "forward")]},
        {"from": "a", "var": "d", "scope": [("IN", "forward")]},
        {"from": "b", "to": "d", "scope": [("IN", "forward")], "edge_var": "same"}]))
    pairs = [(r["nodes"]["a"], r["nodes"]["b"]) for r in res.answer["rows"]]
    assert pairs == [("m1", "m1"), ("m1", "m2"), ("m2", "m1"), ("m2", "m2"), ("m3", "m3")]
    assert res.answer["rows"][1]["edges"] == {"same": "i2"}
    assert verify_result(res, nodes=NODES, edges=EDGES) == []


def test_closing_steps_are_checked():
    base = {"operation": "pattern", "projection": "sha256:" + "0" * 64, "match": {"var": "x", "nodes": ["a"]}}
    for steps in ([{"from": "x", "to": "y", "scope": [("R", "forward")]}],
                  [{"from": "x", "var": "y", "scope": [("R", "forward")], "optional": True},
                   {"from": "y", "to": "x", "scope": [("R", "forward")]}],
                  [{"from": "x", "to": "x", "scope": [("R", "forward")], "optional": True}],
                  [{"from": "x", "to": "x", "scope": [("R", "forward")], "node_types": ["A"]}]):
        with pytest.raises(RequestError) as err:
            normalize_request({**base, "steps": steps})
        assert err.value.code == "invalid_request"
    tree = normalize_request({**base, "steps": [{"from": "x", "var": "y", "scope": [("R", "forward")]}]})
    assert "to" not in tree["steps"][0]                     # tree steps keep their form and their request IDs


def test_cyclic_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    p = Projection.build(nodes=NODES, edges=EDGES)
    steps = [{"from": "c", "var": "a", "scope": [("SUPPORTED_BY", "forward")]},
             {"from": "c", "var": "b", "scope": [("SUPPORTED_BY", "forward")]},
             {"from": "a", "var": "d", "scope": [("IN", "forward")]},
             {"from": "b", "to": "d", "scope": [("IN", "forward")], "edge_var": "same"}]
    seen = set()
    for answer in ("rows", "count", "exists"):
        for limits in ({}, {"max_results": 2}, {"max_expansions": 16}, {"max_expansions": 20}, {"max_expansions": 3}):
            res = execute(p, p.request("pattern", match={"var": "c", "node_types": ["Claim"]}, steps=steps,
                                       answer=answer, limits=limits))
            assert list(validator.iter_errors(res.to_dict())) == [], (answer, limits)
            assert Result.from_dict(res.to_dict()).result_id == res.result_id
            seen.add((res.status, res.answer["total"] is None, res.answer["exists"]))
    assert (INTERRUPTED, True, True) in seen and (COMPLETED, False, True) in seen
