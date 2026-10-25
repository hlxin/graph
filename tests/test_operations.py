from fractions import Fraction

import pytest

from conftest import EDGES, NODES
from graph_runtime import COMPLETED, Projection, execute, verify_result

USES_F, USES_R, CITES_F, CITES_R = ("USES", "forward"), ("USES", "reverse"), ("CITES", "forward"), ("CITES", "reverse")


def run(p, op, nodes=NODES, edges=EDGES, **params):
    res = execute(p, p.request(op, **params))
    assert verify_result(res, nodes=nodes, edges=edges) == []
    return res


def depths(res, key="reached"):
    return [(e["node"], e["depth"], e["via"] and e["via"]["edge"]) for e in res.answer[key]]


# ---------------------------------------------------------------------------------------------- reachability
def test_reachability_lists_least_depths_with_witnesses(small):
    res = run(small, "reachability", sources=["a"], scope=[USES_F])
    assert res.status == COMPLETED and res.complete
    assert depths(res) == [("a", 0, None), ("b", 1, "e1"), ("c", 2, "e2"), ("d", 3, "e4")]   # e4 before its twin e5
    assert [h["edge"] for h in res.path_to("d")] == ["e1", "e2", "e4"]
    assert res.reachable("d") is True and res.reachable("e") is False
    assert res.stats["expansions"] == 4


def test_reachability_respects_scope_depth_and_cycles(small):
    res = run(small, "reachability", sources=["a"], scope=[USES_F, CITES_F])
    assert depths(res) == [("a", 0, None), ("b", 1, "e1"), ("c", 1, "e3"), ("d", 2, "e4")]
    res = run(small, "reachability", sources=["a"], scope=[USES_F], max_depth=1)
    assert depths(res) == [("a", 0, None), ("b", 1, "e1")]
    assert res.complete and res.reachable("c") is False      # complete for this request: max_depth is part of it
    res = run(small, "reachability", sources=["a"], scope=[USES_F], max_depth=0)
    assert depths(res) == [("a", 0, None)] and res.stats["expansions"] == 0


def test_reverse_traversal_follows_edges_backwards(small):
    res = run(small, "reachability", sources=["d"], scope=[USES_R])
    assert depths(res) == [("d", 0, None), ("c", 1, "e4"), ("b", 2, "e2"), ("a", 3, "e1")]
    hop = res.path_to("c")[0]
    assert hop == {"edge": "e4", "relation": "USES", "direction": "reverse", "from": "d", "to": "c"}


def test_reachability_from_several_sources_and_isolated_nodes(small):
    res = run(small, "reachability", sources=["c", "a"], scope=[USES_F])
    assert depths(res) == [("a", 0, None), ("c", 0, None), ("b", 1, "e1"), ("d", 1, "e4")]
    res = run(small, "reachability", sources=["e"], scope=[USES_F, USES_R, CITES_F, CITES_R])
    assert depths(res) == [("e", 0, None)] and res.complete


def test_a_declared_relation_without_edges_gives_a_complete_empty_answer():
    p = Projection.build(nodes=NODES, edges=EDGES, relations=["USES", "CITES", "MENTIONS"])
    res = run(p, "reachability", sources=["a"], scope=[("MENTIONS", "forward")])
    assert depths(res) == [("a", 0, None)] and res.complete


# ---------------------------------------------------------------------------------------------- dependency impact
IMPACT_NODES = ["s1", "s2", "r1", "r2", "r3", "r4"]
IMPACT_EDGES = [{"id": "d1", "source": "r1", "target": "s1", "relation": "DEPENDS_ON"},
                {"id": "d2", "source": "r2", "target": "r1", "relation": "DEPENDS_ON"},
                {"id": "d3", "source": "r3", "target": "r2", "relation": "DEPENDS_ON"},
                {"id": "d4", "source": "r3", "target": "s2", "relation": "DEPENDS_ON"},
                {"id": "d5", "source": "s2", "target": "s1", "relation": "DEPENDS_ON"}]


def impact_projection():
    return Projection.build(nodes=IMPACT_NODES, edges=IMPACT_EDGES)


def test_dependency_impact_lists_what_depends_on_the_changes():
    p = impact_projection()
    res = run(p, "dependency_impact", IMPACT_NODES, IMPACT_EDGES, changed=["s1", "s2"],
              scope=[("DEPENDS_ON", "reverse")])
    assert list(res.answer["changed"]) == ["s1", "s2"]
    # s2 is changed and also depends on s1: it is affected; r3 is reached from s2, which ends its witness
    assert depths(res, "affected") == [("r1", 1, "d1"), ("s2", 1, "d5"), ("r3", 1, "d4"), ("r2", 2, "d2")]
    assert [h["edge"] for h in res.path_to("r3")] == ["d4"]
    assert [h["edge"] for h in res.path_to("r2")] == ["d1", "d2"]
    assert res.affected("r4") is False and res.affected("s1") is False
    res = run(p, "dependency_impact", IMPACT_NODES, IMPACT_EDGES, changed=["s1", "s2"],
              scope=[("DEPENDS_ON", "reverse")], max_depth=1)
    assert [e["node"] for e in res.answer["affected"]] == ["r1", "s2", "r3"]


def test_dependency_impact_through_a_cycle_reaches_the_change_itself():
    nodes = ["x", "y"]
    edges = [{"id": "k1", "source": "y", "target": "x", "relation": "DEPENDS_ON"},
             {"id": "k2", "source": "x", "target": "y", "relation": "DEPENDS_ON"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = run(p, "dependency_impact", nodes, edges, changed=["x"], scope=[("DEPENDS_ON", "reverse")])
    assert depths(res, "affected") == [("y", 1, "k1"), ("x", 2, "k2")]
    assert [(h["from"], h["to"]) for h in res.path_to("x")] == [("x", "y"), ("y", "x")]


# ---------------------------------------------------------------------------------------------- typed path
def hop_edges(res):
    return [h["edge"] for h in res.witness]


def test_typed_path_witness_may_repeat_nodes_and_edges(small):
    res = run(small, "typed_path", sources=["a"], targets=["b"],
              pattern=[{"scope": [USES_F]}, {"scope": [USES_R]}, {"scope": [USES_F]}])
    assert res.found is True and res.complete
    assert [(h["from"], h["to"], h["direction"]) for h in res.witness] == [
        ("a", "b", "forward"), ("b", "a", "reverse"), ("a", "b", "forward")]
    assert hop_edges(res) == ["e1", "e1", "e1"] and [h["position"] for h in res.witness] == [0, 1, 2]


def test_typed_path_around_a_cycle(small):
    res = run(small, "typed_path", sources=["a"],
              pattern=[{"scope": [USES_F]}] * 3 + [{"scope": [CITES_F]}, {"scope": [USES_F]}])
    assert hop_edges(res) == ["e1", "e2", "e4", "e6", "e1"]
    assert res.answer["witness"]["target"] == "b"


def test_typed_path_node_types_and_parallel_edges(small):
    res = run(small, "typed_path", sources=["a"], pattern=[{"scope": [USES_F]}, {"scope": [USES_F],
                                                                                  "node_types": ["Catalyst"]}])
    assert hop_edges(res) == ["e1", "e2"]
    res = run(small, "typed_path", sources=["c"], pattern=[{"scope": [USES_F], "node_types": ["Species"]}])
    assert hop_edges(res) == ["e4"]
    res = run(small, "typed_path", sources=["a"], pattern=[{"scope": [USES_F]}, {"scope": [USES_F],
                                                                                  "node_types": ["Species"]}])
    assert res.found is False and res.complete and res.witness is None


def test_typed_path_reverse_hops_and_targets(small):
    res = run(small, "typed_path", sources=["c"], pattern=[{"scope": [CITES_R]}])
    assert res.witness == [{"position": 0, "edge": "e3", "relation": "CITES", "direction": "reverse", "from": "c",
                            "to": "a"}]
    res = run(small, "typed_path", sources=["a"], targets=["e"], pattern=[{"scope": [USES_F, USES_R]}] * 4)
    assert res.found is False and res.complete


# ---------------------------------------------------------------------------------------------- shortest path
def test_shortest_path_takes_the_cheaper_parallel_edge(small):
    costs = {"by_edge": {"e1": 1, "e2": 1, "e4": 5, "e5": 2}}
    res = run(small, "shortest_path", sources=["a"], targets=["d"], scope=[USES_F], costs=costs)
    assert (res.found, res.answer["optimal"], res.answer["cost"]) == (True, True, 4)
    assert hop_edges(res) == ["e1", "e2", "e5"] and [h["cost"] for h in res.witness] == [1, 1, 2]
    res = run(small, "shortest_path", sources=["a"], targets=["d"], scope=[USES_F],
              costs={"by_edge": {"e1": 1, "e2": 1, "e4": 2, "e5": 2}})
    assert hop_edges(res) == ["e1", "e2", "e4"]                  # a tie between twins: the lower edge ID


def test_shortest_path_costs_by_relation_and_reverse(small):
    res = run(small, "shortest_path", sources=["a"], targets=["d"], scope=[USES_F, CITES_F],
              costs={"by_relation": {"USES": 1, "CITES": 5}})
    assert res.answer["cost"] == 3 and hop_edges(res) == ["e1", "e2", "e4"]
    res = run(small, "shortest_path", sources=["a"], targets=["d"], scope=[USES_F, CITES_F],
              costs={"by_relation": {"USES": 2, "CITES": 1}})
    assert res.answer["cost"] == 3 and hop_edges(res) == ["e3", "e4"]
    res = run(small, "shortest_path", sources=["d"], targets=["a"], scope=[USES_R],
              costs={"by_relation": {"USES": 1}})
    assert res.answer["cost"] == 3 and [h["direction"] for h in res.witness] == ["reverse"] * 3


def test_shortest_path_sources_targets_and_zero_costs(small):
    res = run(small, "shortest_path", sources=["a", "d"], targets=["d"], scope=[USES_F],
              costs={"by_relation": {"USES": 1}})
    assert (res.answer["cost"], res.witness, res.stats["expansions"]) == (0, [], 0)
    res = run(small, "shortest_path", sources=["a"], targets=["d"], scope=[USES_F, CITES_F],
              costs={"by_relation": {"USES": 0, "CITES": 0}})
    assert res.answer["cost"] == 0 and res.found
    res = run(small, "shortest_path", sources=["a"], targets=["e"], scope=[USES_F, CITES_F, USES_R],
              costs={"by_relation": {"USES": 1, "CITES": 1}})
    assert (res.found, res.complete, res.answer["cost"], res.witness) == (False, True, None, None)


def test_large_integer_costs_keep_their_order():
    nodes = ["s", "m", "t"]
    edges = [{"id": "big", "source": "s", "target": "t", "relation": "R"},
             {"id": "h1", "source": "s", "target": "m", "relation": "R"},
             {"id": "h2", "source": "m", "target": "t", "relation": "R"}]
    costs = {"by_edge": {"big": 2**64 + 2, "h1": 2**63 + 1, "h2": 2**63}}
    assert float(2**63 + 1) + float(2**63) == float(2**64 + 2)     # a float sum could not tell these apart
    p = Projection.build(nodes=nodes, edges=edges)
    res = run(p, "shortest_path", nodes, edges, sources=["s"], targets=["t"], scope=[("R", "forward")], costs=costs)
    assert res.answer["cost"] == 2**64 + 1 and type(res.answer["cost"]) is int and hop_edges(res) == ["h1", "h2"]
    assert "cost_exact" not in res.answer
    assert res.to_dict()["answer"]["cost"] == 18446744073709551617


def test_float_costs_are_added_exactly():
    nodes = ["s", "m", "t"]
    edges = [{"id": "big", "source": "s", "target": "t", "relation": "R"},
             {"id": "h1", "source": "s", "target": "m", "relation": "R"},
             {"id": "h2", "source": "m", "target": "t", "relation": "R"}]
    costs = {"by_edge": {"big": 2.0**60 + 256, "h1": 2.0**60, "h2": 255.0}}
    assert 2.0**60 + 255.0 == 2.0**60 + 256                       # in floats, the two paths tie
    p = Projection.build(nodes=nodes, edges=edges)
    res = run(p, "shortest_path", nodes, edges, sources=["s"], targets=["t"], scope=[("R", "forward")], costs=costs)
    assert hop_edges(res) == ["h1", "h2"]
    assert Fraction(res.answer["cost_exact"]) == 2**60 + 255
    assert res.answer["cost"] == float(2**60 + 255)
    res = run(p, "shortest_path", nodes, edges, sources=["s"], targets=["t"], scope=[("R", "forward")],
              costs={"by_edge": {"big": 0.3, "h1": 0.1, "h2": 0.2}})
    assert Fraction(0.3) < Fraction(0.1) + Fraction(0.2)           # as binary fractions, exactly
    assert hop_edges(res) == ["big"] and res.answer["cost_exact"] == str(Fraction(0.3))


@pytest.mark.parametrize("op", ["reachability", "typed_path", "shortest_path", "dependency_impact"])
def test_the_same_request_gives_the_same_result(small, op):
    params = {"reachability": {"sources": ["a"], "scope": [USES_F, CITES_R]},
              "dependency_impact": {"changed": ["d"], "scope": [USES_R]},
              "typed_path": {"sources": ["a"], "pattern": [{"scope": [USES_F, CITES_F]}] * 2},
              "shortest_path": {"sources": ["a"], "targets": ["d"], "scope": [USES_F, CITES_F],
                                "costs": {"by_relation": {"USES": 1.5, "CITES": 2}}}}[op]
    first = execute(small, small.request(op, **params))
    again = execute(Projection.build(nodes=list(reversed(NODES)), edges=list(reversed(EDGES)),
                                     bindings=dict(small.bindings)), small.request(op, **params))
    assert first.result_id == again.result_id and first.to_dict(timing=False) == again.to_dict(timing=False)
