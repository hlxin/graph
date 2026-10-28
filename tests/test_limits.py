import threading

import pytest

from conftest import EDGES, NODES
from graph_runtime import COMPLETED, INTERRUPTED, CancelToken, Projection, execute, verify_result

USES_F, USES_R = ("USES", "forward"), ("USES", "reverse")


def run(p, op, *, nodes=NODES, edges=EDGES, context=None, **params):
    res = execute(p, p.request(op, **params), **(context or {}))
    assert verify_result(res, nodes=nodes, edges=edges) == []
    return res


def listed(res, key="reached"):
    return [e["node"] for e in res.answer[key]]


def test_an_exhausted_expansion_budget_certifies_nothing(small):
    res = run(small, "reachability", sources=["a"], scope=[USES_F], limits={"max_expansions": 4})
    assert res.status == COMPLETED and res.reachable("e") is False
    res = run(small, "reachability", sources=["a"], scope=[USES_F], limits={"max_expansions": 3})
    # every node is listed, but d was never expanded: the answer cannot be called complete
    assert res.status == INTERRUPTED and not res.complete and listed(res) == ["a", "b", "c", "d"]
    assert res.interruption == {"reason": "expansion_budget", "expansions": 3, "limit": 3}
    assert res.reachable("d") is True and res.reachable("e") is None
    res = run(small, "reachability", sources=["a"], scope=[USES_F], limits={"max_expansions": 0})
    assert res.status == INTERRUPTED and listed(res) == ["a"]


def test_a_search_that_needs_no_expansion_completes_whatever_the_limits(small):
    res = run(small, "reachability", sources=["a"], scope=[USES_F], max_depth=0, limits={"max_expansions": 0},
              context={"deadline": -1.0})
    assert res.status == COMPLETED and listed(res) == ["a"]
    res = run(small, "shortest_path", sources=["d"], targets=["d"], scope=[USES_F],
              costs={"by_relation": {"USES": 1}}, limits={"max_expansions": 0})
    assert res.status == COMPLETED and res.answer["cost"] == 0


def test_output_limit(small):
    res = run(small, "reachability", sources=["a"], scope=[USES_F], limits={"max_results": 4})
    assert res.status == COMPLETED and res.answer["count"] == 4                 # a full answer that fits
    res = run(small, "reachability", sources=["a"], scope=[USES_F], limits={"max_results": 3})
    assert res.status == INTERRUPTED and res.interruption["reason"] == "output_limit"
    assert listed(res) == ["a", "b", "c"] and res.reachable("d") is None
    res = run(small, "reachability", sources=["a", "b"], scope=[USES_F], limits={"max_results": 1})
    assert res.status == INTERRUPTED and listed(res) == ["a"]
    res = run(small, "dependency_impact", changed=["d"], scope=[USES_R], limits={"max_results": 3})
    assert res.status == COMPLETED and listed(res, "affected") == ["a", "b", "c"]
    res = run(small, "dependency_impact", changed=["d"], scope=[USES_R], limits={"max_results": 2})
    assert res.status == INTERRUPTED and res.affected("a") is None and res.affected("b") is True
    res = run(small, "dependency_impact", changed=["e"], scope=[USES_R], limits={"max_results": 0})
    assert res.status == COMPLETED and res.affected("a") is False


class Clock:
    def __init__(self, step=1.0):
        self.now, self.step, self.calls = 0.0, step, 0

    def __call__(self):
        self.calls += 1
        self.now += self.step
        return self.now


def test_deadline(small):
    res = run(small, "reachability", sources=["a"], scope=[USES_F], context={"deadline": -1.0})
    assert res.status == INTERRUPTED and res.interruption["reason"] == "deadline"
    assert res.stats["expansions"] == 0 and res.reachable("b") is None
    clock = Clock()            # 1 when the deadline is set (so 3.5), then 2, 3 and 4 at the checks
    res = run(small, "reachability", sources=["a"], scope=[USES_F],
              context={"timeout": 2.5, "clock": clock, "check_interval": 1})
    assert res.status == INTERRUPTED and res.stats["expansions"] == 2 and res.interruption["limit"] is None
    assert listed(res) == ["a", "b", "c"]
    clock = Clock()            # checked only before the first expansion: a short search finishes
    res = run(small, "reachability", sources=["a"], scope=[USES_F], context={"timeout": 1.5, "clock": clock})
    assert res.status == COMPLETED and clock.calls == 2


def test_cancellation(small):
    token = CancelToken()
    token.cancel()
    event = threading.Event()
    event.set()
    for cancel in (token, event, lambda: True):
        res = run(small, "typed_path", sources=["a"], pattern=[{"scope": [USES_F]}] * 3, context={"cancel": cancel})
        assert res.status == INTERRUPTED and res.interruption["reason"] == "cancelled" and res.found is None
    calls = []
    res = run(small, "reachability", sources=["a"], scope=[USES_F],
              context={"cancel": lambda: calls.append(1) or len(calls) > 2, "check_interval": 1})
    assert res.status == INTERRUPTED and res.stats["expansions"] == 2 and listed(res) == ["a", "b", "c"]
    assert run(small, "reachability", sources=["a"], scope=[USES_F], context={"cancel": CancelToken()}).complete


def test_interrupted_shortest_path_gives_a_valid_but_unproven_witness():
    nodes = ["s", "m", "t"]
    edges = [{"id": "direct", "source": "s", "target": "t", "relation": "R"},
             {"id": "h1", "source": "s", "target": "m", "relation": "R"},
             {"id": "h2", "source": "m", "target": "t", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    params = {"sources": ["s"], "targets": ["t"], "scope": [("R", "forward")],
              "costs": {"by_edge": {"direct": 10, "h1": 1, "h2": 1}}}
    res = run(p, "shortest_path", nodes=nodes, edges=edges, limits={"max_expansions": 1}, **params)
    assert res.status == INTERRUPTED and res.found is True
    assert (res.answer["optimal"], res.answer["cost"], res.answer["lower_bound"]) == (False, 10, 1)
    assert [h["edge"] for h in res.witness] == ["direct"]
    assert any("not proven minimal" in x for x in res.limitations)
    res = run(p, "shortest_path", nodes=nodes, edges=edges, limits={"max_expansions": 2}, **params)
    assert res.status == COMPLETED and res.answer["cost"] == 2 and res.answer["optimal"] is True
    res = run(p, "shortest_path", nodes=nodes, edges=edges, limits={"max_expansions": 0}, **params)
    assert (res.status, res.found, res.answer["lower_bound"], res.witness) == (INTERRUPTED, None, 0, None)
    res = run(p, "shortest_path", nodes=nodes, edges=edges, limits={"max_expansions": 1},
              **{**params, "costs": {"by_edge": {"direct": 10.0, "h1": 0.5, "h2": 1}}})
    assert (res.answer["cost_exact"], res.answer["lower_bound_exact"]) == ("10", "1/2")


def test_interrupted_typed_path_is_unknown_not_negative(small):
    pattern = [{"scope": [USES_F]}, {"scope": [USES_F]}, {"scope": [USES_F]}]
    res = run(small, "typed_path", sources=["a"], targets=["d"], pattern=pattern, limits={"max_expansions": 2})
    assert res.status == INTERRUPTED and res.found is None and res.answer["witness"] is None
    res = run(small, "typed_path", sources=["a"], targets=["d"], pattern=pattern, limits={"max_expansions": 3})
    assert res.status == COMPLETED and res.found is True


def test_execution_arguments_are_checked(small):
    req = small.request("reachability", sources=["a"], scope=[USES_F])
    with pytest.raises(TypeError):
        execute(small, req, timeout=1, deadline=2)
    with pytest.raises(ValueError):
        execute(small, req, check_interval=0)
    with pytest.raises(TypeError):
        execute(small, req, cancel=object())
    with pytest.raises(TypeError):
        execute(small.to_dict(), req)
