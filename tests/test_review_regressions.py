"""Regressions at the edges: three-valued staleness, malformed requests and results, costs beyond the floats, and
validation gaps in observations, dates, attributes and histories."""

import json

import pytest

from conftest import EDGES, NODES
from graph_runtime import (COMPLETED, FAILED, INTERRUPTED, UNSUPPORTED, Projection, Result, execute, json_schema,
                           normalize_request, verify_result)

R = ("R", "forward")


def chain():
    nodes = ["a", "b", "c"]
    edges = [{"id": "e1", "source": "a", "target": "b", "relation": "R"},
             {"id": "e2", "source": "b", "target": "c", "relation": "R"}]
    return Projection.build(nodes=nodes, edges=edges), nodes, edges


@pytest.mark.parametrize("raw,status", [
    ({"operation": "reachability", "sources": ["\ud800"], "scope": [R]}, FAILED),
    ({"operation": "reachability", "sources": ["a"], "scope": [R], "limits": {"max_expansions": 10**5000}}, FAILED),
    ({"operation": "shortest_path", "sources": ["a"], "targets": ["c"], "scope": [R],
      "costs": {"by_relation": {"R": 10**5000}}}, FAILED),
    ({"operation": "reachability", "contract": float("nan"), "sources": ["a"], "scope": [R]}, UNSUPPORTED),
    ({"operation": "reachability", "mode": b"exact", "sources": ["a"], "scope": [R]}, UNSUPPORTED),
    ({"operation": "\ud800", "sources": ["a"]}, UNSUPPORTED),
    ({"operation": "reachability", "sources": ["a"], "scope": [R], "\ud800": 1}, FAILED),
])
def test_execute_reports_every_request_problem_in_a_result(raw, status):
    p, _, _ = chain()
    res = execute(p, {**raw, "projection": p.identity})
    assert res.status == status and res.answer is None
    json.loads(res.to_json())                                      # the refusal itself travels as JSON
    assert verify_result(res, nodes=[], edges=[]) == []


def test_an_answer_too_large_for_json_is_reported_not_raised():
    p, _, _ = chain()
    big = 9 * 10**4299                              # 4,300 digits: the request is writable, the sum is not
    res = execute(p, p.request("shortest_path", sources=["a"], targets=["c"], scope=[R],
                               costs={"by_relation": {"R": big}}))
    assert (res.status, res.error["code"]) == (FAILED, "unrepresentable_result")


def test_float_costs_beyond_the_floats_keep_their_exact_value():
    p, nodes, edges = chain()
    res = execute(p, p.request("shortest_path", sources=["a"], targets=["c"], scope=[R],
                               costs={"by_relation": {"R": 1e308}}))
    assert res.status == COMPLETED and res.answer["cost"] is None
    assert int(res.answer["cost_exact"]) == 2 * int(1e308)
    assert verify_result(res, nodes=nodes, edges=edges) == []
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(res.to_dict(), json_schema("result"))
    res = execute(p, p.request("shortest_path", sources=["a"], targets=["c"], scope=[R],
                               costs={"by_edge": {"e1": 0.5, "e2": 10**400}}))
    assert res.status == COMPLETED and res.answer["cost"] is None and res.answer["cost_exact"] == f"{2 * 10**400 + 1}/2"


def forge(data):
    from graph_runtime._canonical import digest
    body = {k: v for k, v in data.items() if k not in ("result_id", "timing")}
    return {**body, "result_id": digest(body)}


def test_a_result_read_back_must_keep_the_contract():
    p, _, _ = chain()
    cut = execute(p, p.request("reachability", sources=["a"], scope=[R], limits={"max_expansions": 1}))
    assert cut.status == INTERRUPTED and cut.reachable("c") is None
    data = forge({**cut.to_dict(timing=False), "complete": True})
    with pytest.raises(ValueError, match="complete is true exactly"):
        Result.from_dict(data)
    typed = execute(p, p.request("typed_path", sources=["a"], pattern=[{"scope": [R]}] * 2,
                                 limits={"max_expansions": 1})).to_dict(timing=False)
    assert typed["status"] == INTERRUPTED
    with pytest.raises(ValueError, match="found is false only"):
        Result.from_dict(forge({**typed, "answer": {"found": False, "witness": None}}))
    assert Result.from_dict(forge(typed)).found is None
    with pytest.raises(AttributeError):
        del cut._data


def test_verify_result_reports_malformed_results():
    p, nodes, edges = chain()
    good = execute(p, p.request("typed_path", sources=["a"], pattern=[{"scope": [R]}])).to_dict(timing=False)
    longer = json.loads(json.dumps(good))
    longer["answer"]["witness"]["hops"].append(dict(longer["answer"]["witness"]["hops"][0], position=1))
    longer["answer"]["witness"]["length"] = 2
    no_witness = {**good, "answer": {"found": True, "witness": None}}
    null_hop = json.loads(json.dumps(good))
    null_hop["answer"]["witness"]["hops"] = [None]
    bool_position = json.loads(json.dumps(good))
    bool_position["answer"]["witness"]["hops"][0]["position"] = False
    for bad in (longer, no_witness, null_hop, bool_position, {"status": "completed"}, {}):
        problems = verify_result(bad, nodes=nodes, edges=edges)
        assert problems and all(isinstance(x, str) for x in problems)


def test_requests_cannot_be_unfrozen():
    req = normalize_request({"operation": "reachability", "projection": "sha256:" + "0" * 64, "sources": ["a"],
                             "scope": [R]})
    for name in ("_id", "_data"):
        with pytest.raises(AttributeError):
            delattr(req, name)


def test_source_records_may_state_an_established_status():
    p = Projection.build(nodes=["a", "b"], edges=[{"id": "e", "source": "a", "target": "b", "relation": "R",
                                                    "epistemic_status": "established"}])
    assert p.has_edge("e")
