import pytest

from graph_runtime import FAILED, RequestError, UNSUPPORTED, execute, normalize_request, verify_result
from graph_runtime.request import DEFAULT_LIMITS

from conftest import EDGES, NODES

P = "sha256:" + "0" * 64


def test_normalization_sorts_dedupes_and_fills_defaults():
    r = normalize_request({"operation": "reachability", "projection": P, "sources": ["b", "a", "b"],
                           "scope": [("USES", "forward"), {"relation": "CITES", "direction": "reverse"},
                                     ["USES", "forward"]]})
    assert r.to_dict() == {
        "contract": "graph-runtime/v0.2", "operation": "reachability", "mode": "exact", "projection": P,
        "limits": {"max_expansions": DEFAULT_LIMITS["max_expansions"], "max_results": DEFAULT_LIMITS["max_results"]},
        "sources": ["a", "b"], "max_depth": None,
        "scope": [{"relation": "CITES", "direction": "reverse"}, {"relation": "USES", "direction": "forward"}]}


def test_equivalent_requests_share_one_identity():
    a = normalize_request({"operation": "typed_path", "projection": P, "sources": ["x", "y"],
                           "pattern": [{"scope": [("R", "forward"), ("S", "reverse")], "node_types": ["B", "A"]}]})
    b = normalize_request({"operation": "typed_path", "projection": P, "sources": ["y", "x", "x"], "targets": None,
                           "mode": "exact", "contract": "graph-runtime/v0.2",
                           "pattern": [{"node_types": ["A", "B", "A"],
                                        "scope": [{"relation": "S", "direction": "reverse"}, ("R", "forward")]}],
                           "limits": {"max_expansions": DEFAULT_LIMITS["max_expansions"]}})
    assert a == b and a.request_id == b.request_id
    c = normalize_request({**b.to_dict(), "limits": {"max_expansions": 5}})
    assert c.request_id != a.request_id
    assert normalize_request(a) is a


def test_requests_are_immutable():
    r = normalize_request({"operation": "reachability", "projection": P, "sources": ["a"], "scope": [("R", "forward")]})
    with pytest.raises(AttributeError):
        r.foo = 1
    with pytest.raises(TypeError):
        r["scope"][0]["relation"] = "S"
    d = r.to_dict()
    d["sources"].append("zz")
    assert list(r["sources"]) == ["a"]


BASE = {"operation": "reachability", "projection": P, "sources": ["a"], "scope": [("R", "forward")]}
SHORT = {"operation": "shortest_path", "projection": P, "sources": ["a"], "targets": ["b"], "scope": [("R", "forward")]}


@pytest.mark.parametrize("raw,status,code", [
    ([], FAILED, "invalid_request"),
    ({**BASE, "operation": None}, FAILED, "invalid_request"),
    ({**BASE, "operation": "pagerank"}, UNSUPPORTED, "unsupported_operation"),
    ({**BASE, "mode": "approximate"}, UNSUPPORTED, "unsupported_mode"),
    ({**BASE, "contract": "graph-runtime/v1"}, UNSUPPORTED, "unsupported_contract"),
    ({**BASE, "projection": None}, FAILED, "invalid_request"),
    ({**BASE, "projection": "sha256:xyz"}, FAILED, "invalid_request"),
    ({**BASE, "include_candidates": True}, FAILED, "invalid_request"),
    ({**BASE, "targets": ["b"]}, FAILED, "invalid_request"),
    ({**BASE, "sources": "a"}, FAILED, "invalid_request"),
    ({**BASE, "sources": []}, FAILED, "invalid_request"),
    ({**BASE, "sources": ["a", ""]}, FAILED, "invalid_request"),
    ({**BASE, "scope": []}, FAILED, "invalid_request"),
    ({**BASE, "scope": "*"}, FAILED, "invalid_request"),
    ({**BASE, "scope": [("R", "both")]}, FAILED, "invalid_request"),
    ({**BASE, "scope": [{"relation": "R", "direction": "forward", "weight": 2}]}, FAILED, "invalid_request"),
    ({**BASE, "max_depth": -1}, FAILED, "invalid_request"),
    ({**BASE, "max_depth": True}, FAILED, "invalid_request"),
    ({**BASE, "limits": {"max_expansions": -1}}, FAILED, "invalid_request"),
    ({**BASE, "limits": {"max_expansions": 1.5}}, FAILED, "invalid_request"),
    ({**BASE, "limits": {"deadline": 3}}, FAILED, "invalid_request"),
    ({"operation": "typed_path", "projection": P, "sources": ["a"], "pattern": []}, FAILED, "invalid_request"),
    ({"operation": "typed_path", "projection": P, "sources": ["a"], "pattern": [{"node_types": ["T"]}]},
     FAILED, "invalid_request"),
    ({"operation": "typed_path", "projection": P, "sources": ["a"],
      "pattern": [{"scope": [("R", "forward")], "node_types": []}]}, FAILED, "invalid_request"),
    ({"operation": "typed_path", "projection": P, "sources": ["a"], "pattern": [{"scope": [("R", "forward")]}],
      "limits": {"max_results": 3}}, FAILED, "invalid_request"),
    ({**SHORT}, FAILED, "invalid_request"),
    ({**SHORT, "costs": {"by_relation": {"R": 1}, "by_edge": {}}}, FAILED, "invalid_request"),
    ({**SHORT, "costs": {"per_hop": 1}}, FAILED, "invalid_request"),
    ({**SHORT, "costs": {"by_relation": {"R": True}}}, FAILED, "invalid_cost"),
    ({**SHORT, "costs": {"by_relation": {"R": "1"}}}, FAILED, "invalid_cost"),
    ({**SHORT, "costs": {"by_relation": {"R": None}}}, FAILED, "invalid_cost"),
    ({**SHORT, "costs": {"by_relation": {"R": float("inf")}}}, FAILED, "non_finite_cost"),
    ({**SHORT, "costs": {"by_relation": {"R": float("nan")}}}, FAILED, "non_finite_cost"),
    ({**SHORT, "costs": {"by_relation": {"R": -1}}}, UNSUPPORTED, "negative_cost"),
    ({**SHORT, "costs": {"by_edge": {"e1": -0.5}}}, UNSUPPORTED, "negative_cost"),
])
def test_malformed_and_unsupported_requests(raw, status, code):
    with pytest.raises(RequestError) as err:
        normalize_request(raw)
    assert (err.value.status, err.value.code) == (status, code)


def test_large_integer_and_float_costs_are_kept_exactly():
    r = normalize_request({**SHORT, "costs": {"by_relation": {"R": 2**80 + 1}}})
    assert r["costs"]["by_relation"]["R"] == 2**80 + 1
    r = normalize_request({**SHORT, "costs": {"by_edge": {"e1": 0.1, "e2": 0}}})
    assert r["costs"]["by_edge"]["e1"] == 0.1


@pytest.mark.parametrize("params,code", [
    ({"operation": "reachability", "sources": ["zz"], "scope": [("USES", "forward")]}, "unknown_node"),
    ({"operation": "dependency_impact", "changed": ["a", "zz"], "scope": [("USES", "forward")]}, "unknown_node"),
    ({"operation": "reachability", "sources": ["a"], "scope": [("USED", "forward")]}, "unknown_relation"),
    ({"operation": "typed_path", "sources": ["a"], "targets": ["zz"], "pattern": [{"scope": [("USES", "forward")]}]},
     "unknown_node"),
    ({"operation": "typed_path", "sources": ["a"],
      "pattern": [{"scope": [("USES", "forward")], "node_types": ["Sampel"]}]}, "unknown_node_type"),
    ({"operation": "shortest_path", "sources": ["a"], "targets": ["d"], "scope": [("USES", "forward")],
      "costs": {"by_relation": {"CITES": 1}}}, "missing_cost"),
    ({"operation": "shortest_path", "sources": ["a"], "targets": ["d"], "scope": [("USES", "forward")],
      "costs": {"by_relation": {"USES": 1, "MENTIONS": 1}}}, "unknown_relation"),
    ({"operation": "shortest_path", "sources": ["a"], "targets": ["d"], "scope": [("USES", "forward")],
      "costs": {"by_edge": {"e1": 1, "e2": 1, "e4": 1}}}, "missing_cost"),
    ({"operation": "shortest_path", "sources": ["a"], "targets": ["d"], "scope": [("USES", "forward")],
      "costs": {"by_edge": {"e1": 1, "e2": 1, "e4": 1, "e5": 1, "e99": 1}}}, "unknown_edge"),
])
def test_requests_naming_what_the_projection_lacks_fail(small, params, code):
    res = execute(small, {**params, "projection": small.identity})
    assert (res.status, res.error["code"], res.answer, res.complete) == (FAILED, code, None, False)
    assert verify_result(res, nodes=NODES, edges=EDGES) == []


def test_execute_reports_request_problems_without_raising(small):
    for raw, status in [({"operation": "pagerank", "projection": small.identity}, UNSUPPORTED),
                        ("not a request", FAILED),
                        ({**SHORT, "projection": small.identity, "costs": {"by_relation": {"R": -2}}}, UNSUPPORTED)]:
        res = execute(small, raw)
        assert res.status == status and res.answer is None and res.error["code"]
    res = execute(small, {"operation": "pagerank", "projection": small.identity})
    assert res.operation == "pagerank" and res.request is None
