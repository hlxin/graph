import copy
import doctest
import json

import pytest

import graph_runtime
from conftest import EDGES, NODES
from graph_runtime import (COMPLETED, STALE, HypothesisCandidate, Projection, Result, execute, verify_result)

USES_F = ("USES", "forward")


def test_results_are_immutable_and_round_trip(small):
    res = execute(small, small.request("reachability", sources=["a"], scope=[USES_F]))
    with pytest.raises(AttributeError):
        res.status = "failed"
    with pytest.raises(TypeError):
        res.answer["reached"][0]["node"] = "z"
    data = json.loads(res.to_json(timing=True))
    back = Result.from_dict(data)
    assert back.result_id == res.result_id and back.to_dict(timing=False) == res.to_dict(timing=False)
    assert back.path_to("d") == res.path_to("d")
    data["answer"]["reached"][1]["depth"] = 7
    with pytest.raises(ValueError):
        Result.from_dict(data)
    with pytest.raises(TypeError):
        Result()


def test_timing_is_measured_but_not_identified(small):
    req = small.request("reachability", sources=["a"], scope=[USES_F])
    a, b = execute(small, req), execute(small, req)
    assert a.result_id == b.result_id
    assert set(a.timing) == {"bind_s", "search_s", "witness_s", "result_s", "total_s"}
    assert all(v >= 0 for v in a.timing.values())
    assert "timing" not in json.loads(a.to_json()) and "timing" in a.to_dict()


def test_result_names_runtime_contract_projection_and_request(small):
    req = small.request("reachability", sources=["a"], scope=[USES_F])
    res = execute(small, req)
    d = res.to_dict()
    assert d["kind"] == "graph_runtime.result" and d["contract"] == "graph-runtime/v0.2"
    assert d["runtime"] == {"name": "graph-runtime", "version": graph_runtime.__version__}
    assert (d["projection"], d["request_id"], d["request"]) == (small.identity, req.request_id, req.to_dict())
    assert d["mode"] == "exact" and d["complete"] is True and d["status"] == COMPLETED
    assert any("not in the literature or the physical world" in x for x in d["limitations"])


def test_reading_an_answer_of_another_operation_is_an_error(small):
    res = execute(small, small.request("reachability", sources=["a"], scope=[USES_F]))
    with pytest.raises(ValueError):
        res.found
    with pytest.raises(ValueError):
        res.affected("a")
    with pytest.raises(KeyError):
        res.path_to("e")
    res = execute(small, small.request("typed_path", sources=["a"], pattern=[{"scope": [USES_F]}]))
    with pytest.raises(ValueError):
        res.reachable("a")


def test_a_request_for_another_projection_is_stale(small):
    req = small.request("reachability", sources=["a"], scope=[USES_F])
    newer = Projection.build(nodes=NODES, edges=EDGES[:-1], bindings=dict(small.bindings))
    res = execute(newer, req)
    assert (res.status, res.error["code"], res.answer) == (STALE, "projection_mismatch", None)
    assert res.error["detail"] == {"expected": small.identity, "actual": newer.identity}
    assert verify_result(res, nodes=NODES, edges=EDGES) == []


def test_changed_bindings_make_a_projection_stale(small):
    req = small.request("reachability", sources=["a"], scope=[USES_F])
    assert execute(small, req, current_bindings=dict(small.bindings)).status == COMPLETED
    for now in ({**small.bindings, "source": "doc@2"}, {**small.bindings, "policy": "institution"},
                {"source": "doc@1", "schema": "s@1"}):
        res = execute(small, req, current_bindings=now)
        assert (res.status, res.error["code"]) == (STALE, "bindings_changed")


def test_source_records_are_unchanged_by_execution_and_candidates():
    nodes, edges = copy.deepcopy(NODES), copy.deepcopy(EDGES)
    p = Projection.build(nodes=nodes, edges=edges)
    for op, params in [("reachability", {"sources": ["a"], "scope": [USES_F]}),
                       ("dependency_impact", {"changed": ["d"], "scope": [("USES", "reverse")]}),
                       ("typed_path", {"sources": ["a"], "pattern": [{"scope": [USES_F]}] * 2}),
                       ("shortest_path", {"sources": ["a"], "targets": ["d"], "scope": [USES_F],
                                          "costs": {"by_relation": {"USES": 1}}})]:
        res = execute(p, p.request(op, **params))
        assert res.status == COMPLETED
    basis = [{"type": "node", "ref": "a"}]
    tests = [{"description": "d", "outcomes": [{"observation": "x", "bearing": "supports"},
                                               {"observation": "y", "bearing": "opposes"}]}]
    c = HypothesisCandidate.create(statement="s", scope="synthetic", projection=p,
                                   producer={"name": "t", "version": "0"}, basis=basis, proposed_tests=tests,
                                   at="2026-10-25")
    c.withdraw(at="2026-10-26", reason="reformulated")
    basis[0]["ref"] = "b"                         # the caller's objects are copied, never kept
    tests[0]["outcomes"].clear()
    assert c.basis[0]["ref"] == "a" and len(c["proposed_tests"][0]["outcomes"]) == 2
    assert (nodes, edges) == (NODES, EDGES)
    assert Projection.build(nodes=nodes, edges=edges).identity == p.identity


def test_package_doctest():
    assert doctest.testmod(graph_runtime).failed == 0
