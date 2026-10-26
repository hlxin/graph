"""Type and relation hierarchies: a scope on a relation follows the relations below it, a constraint on a type
matches the nodes of its subtypes, and witnesses keep naming each edge's own relation."""

import json
import random

import pytest

from graph_runtime import COMPLETED, FAILED, Projection, ProjectionError, execute, verify_result
from oracle import (RELATIONS, TYPES, closed_nodes, expand_scope, least_depths, node_id, random_graph,
                    random_scope, typed_ends)

NODES = [{"id": "dark", "types": ["DarkControl"]}, {"id": "plain", "types": ["Experiment"]},
         {"id": "run", "types": ["CatalyticTest"]}, {"id": "both", "types": ["Hybrid"]}, "sample"]
EDGES = [{"id": "c1", "source": "dark", "target": "run", "relation": "DARK_CONTROL_FOR"},
         {"id": "c2", "source": "plain", "target": "run", "relation": "CONTROL_FOR"},
         {"id": "u1", "source": "run", "target": "sample", "relation": "USES"},
         {"id": "u2", "source": "both", "target": "sample", "relation": "USES"}]
TYPE_PARENTS = {"CatalyticTest": ["Experiment"], "DarkControl": ["Experiment"], "Hybrid": ["Experiment", "Model"]}
RELATION_PARENTS = {"DARK_CONTROL_FOR": ["CONTROL_FOR"]}
CR, DR = ("CONTROL_FOR", "reverse"), ("DARK_CONTROL_FOR", "reverse")


@pytest.fixture
def p():
    return Projection.build(nodes=NODES, edges=EDGES, type_parents=TYPE_PARENTS, relation_parents=RELATION_PARENTS)


def check(p, res):
    assert verify_result(res, nodes=NODES, edges=EDGES, type_parents=TYPE_PARENTS,
                         relation_parents=RELATION_PARENTS) == []
    return res


def test_a_scope_follows_the_relations_below_it(p):
    res = check(p, execute(p, p.request("reachability", sources=["run"], scope=[CR])))
    assert {(e["node"], e["via"] and e["via"]["relation"]) for e in res.answer["reached"]} == {
        ("run", None), ("dark", "DARK_CONTROL_FOR"), ("plain", "CONTROL_FOR")}
    res = check(p, execute(p, p.request("reachability", sources=["run"], scope=[DR])))
    assert [e["node"] for e in res.answer["reached"]] == ["dark", "run"]           # never upwards
    assert p.covered("CONTROL_FOR") == ("CONTROL_FOR", "DARK_CONTROL_FOR")
    res = check(p, execute(p, p.request("dependency_impact", changed=["run"], scope=[CR])))
    assert sorted(e["node"] for e in res.answer["affected"]) == ["dark", "plain"]


def test_a_type_matches_the_nodes_of_its_subtypes(p):
    pattern = [{"scope": [("USES", "reverse")], "node_types": ["Experiment"]}]
    res = check(p, execute(p, p.request("typed_path", sources=["sample"], pattern=pattern)))
    assert res.found and res.witness[0]["to"] == "run"           # in-edges by ID: u1 (from run) comes first
    pattern = [{"scope": [("USES", "reverse")], "node_types": ["Model"]}]                # a second parent
    res = check(p, execute(p, p.request("typed_path", sources=["sample"], pattern=pattern)))
    assert res.witness[0]["to"] == "both"
    pattern = [{"scope": [("USES", "reverse")], "node_types": ["DarkControl"]}]
    res = check(p, execute(p, p.request("typed_path", sources=["sample"], pattern=pattern)))
    assert res.found is False and res.complete
    assert p.node("both").types == ("Hybrid",)                   # a node keeps the types it declares


def test_costs_name_the_relations_edges_have(p):
    res = execute(p, p.request("shortest_path", sources=["run"], targets=["dark"], scope=[CR],
                               costs={"by_relation": {"CONTROL_FOR": 1}}))
    assert (res.status, res.error["code"]) == (FAILED, "missing_cost")
    assert list(res.error["detail"]["relations"]) == ["DARK_CONTROL_FOR"]
    res = check(p, execute(p, p.request("shortest_path", sources=["run"], targets=["dark"], scope=[CR],
                                        costs={"by_relation": {"CONTROL_FOR": 1, "DARK_CONTROL_FOR": 2}})))
    assert res.answer["cost"] == 2 and res.witness[0]["relation"] == "DARK_CONTROL_FOR"


def test_verification_needs_the_hierarchy(p):
    res = execute(p, p.request("reachability", sources=["run"], scope=[CR]))
    assert any("outside the requested scope" in x for x in verify_result(res, nodes=NODES, edges=EDGES))
    res = execute(p, p.request("typed_path", sources=["sample"],
                               pattern=[{"scope": [("USES", "reverse")], "node_types": ["Experiment"]}]))
    assert any("none of the types" in x for x in verify_result(res, nodes=NODES, edges=EDGES,
                                                                relation_parents=RELATION_PARENTS))


def test_hierarchies_are_part_of_the_identity_and_the_snapshot(p):
    same = Projection.build(nodes=list(reversed(NODES)), edges=list(reversed(EDGES)),
                            type_parents={k: list(reversed(v)) for k, v in reversed(list(TYPE_PARENTS.items()))},
                            relation_parents=RELATION_PARENTS)
    assert same.identity == p.identity
    flat = Projection.build(nodes=NODES, edges=EDGES, node_types=p.node_types, relations=p.relations)
    assert flat.identity != p.identity
    snap = json.loads(json.dumps(p.to_dict()))
    assert snap["relation_parents"] == {"DARK_CONTROL_FOR": ["CONTROL_FOR"]}
    assert snap["type_parents"]["Hybrid"] == ["Experiment", "Model"]
    assert Projection.from_dict(snap) == p
    assert set(p.node_types) == {"CatalyticTest", "DarkControl", "Experiment", "Hybrid", "Model"}


@pytest.mark.parametrize("kw,code", [
    ({"type_parents": {"A": ["B"], "B": ["A"]}}, "hierarchy_cycle"),
    ({"relation_parents": {"R": ["R"]}}, "hierarchy_cycle"),
    ({"type_parents": {"A": ["B"], "B": ["C"], "C": ["A"]}}, "hierarchy_cycle"),
    ({"type_parents": ["A"]}, "invalid_hierarchy"),
    ({"type_parents": {"A": "B"}}, "invalid_hierarchy"),
    ({"relation_parents": {"R": [""]}}, "invalid_record"),
    ({"relation_parents": {"R": ["S"]}, "relations": ["R"]}, "undeclared_relation"),
    ({"type_parents": {"A": ["B"]}, "node_types": ["A"]}, "undeclared_node_type"),
])
def test_bad_hierarchies_are_refused(kw, code):
    with pytest.raises(ProjectionError) as err:
        Projection.build(nodes=[{"id": "x", "types": ["A"]}], edges=[], **kw)
    assert err.value.code == code


def random_hierarchy(rng, names):
    """An acyclic hierarchy: a name's parents come earlier in a shuffled order."""
    order = list(names)
    rng.shuffle(order)
    return {n: rng.sample(order[:i], rng.randint(0, min(2, i))) for i, n in enumerate(order) if i and rng.random() < 0.6}


def test_hierarchies_match_an_independent_expansion():
    rng = random.Random("hierarchies")
    for _ in range(250):
        nodes, edges = random_graph(rng)
        tp, rp = random_hierarchy(rng, TYPES), random_hierarchy(rng, RELATIONS)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES, type_parents=tp,
                             relation_parents=rp)
        ids = [node_id(n) for n in nodes]
        scope = random_scope(rng)
        sources = rng.sample(ids, rng.randint(1, min(2, len(ids))))
        res = execute(p, p.request("reachability", sources=sources, scope=scope))
        assert verify_result(res, nodes=nodes, edges=edges, type_parents=tp, relation_parents=rp) == []
        want = least_depths(edges, sources, expand_scope(scope, rp))
        assert {e["node"]: e["depth"] for e in res.answer["reached"]} == want
        flat_nodes = closed_nodes(nodes, tp)
        pattern = [{"scope": random_scope(rng), "node_types": rng.choice([None, rng.sample(TYPES, 1)])}
                   for _ in range(rng.randint(1, 3))]
        res = execute(p, p.request("typed_path", sources=sources, pattern=pattern))
        assert res.status == COMPLETED
        assert verify_result(res, nodes=nodes, edges=edges, type_parents=tp, relation_parents=rp) == []
        expanded = [{"scope": expand_scope(pos["scope"], rp), "node_types": pos["node_types"]} for pos in pattern]
        assert res.found is bool(typed_ends(flat_nodes, edges, sources, expanded))
