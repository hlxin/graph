"""Support: what stops standing when some nodes fail, and what stands on less than before. A node with no support
stands; a node of an all_of type needs every support, any other node one; what stands is the least fixpoint, so a
circle of support grounds nothing. Checked against an independent oracle on random graphs, under limits, and on
named scenarios."""

import random

import pytest

from graph_runtime import (COMPLETED, FAILED, INTERRUPTED, Projection, Result, execute, json_schema,
                           normalize_request, verify_result)
from graph_runtime.errors import RequestError
from oracle import RELATIONS, TYPES, node_id, random_graph, random_scope, support_answer

SUPPORT = [("SUPPORTED_BY", "forward"), ("CITES", "forward")]


def entries(answer, key):
    return {e["node"]: {"supports": e["supports"], "standing": e["standing"],
                        "failing": [h["edge"] for h in e["failing"]], "causes": list(e["causes"])}
            for e in answer[key]}


def run(p, nodes, edges, type_parents=None, relation_parents=None, **params):
    res = execute(p, p.request("support", **params))
    assert verify_result(res, nodes=nodes, edges=edges, type_parents=type_parents,
                         relation_parents=relation_parents) == []
    assert Result.from_dict(res.to_dict()).result_id == res.result_id
    return res


def random_hierarchies(rng):
    if rng.random() < 0.5:
        return None, None
    return {"A": ["B"]} if rng.random() < 0.5 else {"A": ["B"], "B": ["C"]}, \
        ({"R": ["S"]} if rng.random() < 0.5 else None)


def test_random_graphs_match_the_oracle():
    rng = random.Random("support")
    seen = set()
    for _ in range(700):
        nodes, edges = random_graph(rng, max_nodes=9, max_edges=20)
        tp, rp = random_hierarchies(rng)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES, type_parents=tp,
                             relation_parents=rp)
        ids = [node_id(n) for n in nodes]
        failed = rng.sample(ids, rng.randint(1, min(3, len(ids))))
        scope = random_scope(rng)
        all_of = rng.choice([None, ["A"], ["B"], ["A", "C"]])
        res = run(p, nodes, edges, tp, rp, failed=failed, scope=scope, all_of=all_of)
        assert res.status == COMPLETED
        want = support_answer(nodes, edges, failed, scope, all_of, tp, rp)
        assert entries(res.answer, "lost") == want["lost"]
        assert entries(res.answer, "weakened") == want["weakened"]
        assert dict(res.answer["counts"]) == want["counts"]
        seen |= {k for k in ("lost", "weakened", "ungrounded") if want["counts"][k]}
        seen |= {"cycle"} if any(len(e["causes"]) > 1 for e in want["lost"].values()) else set()
    assert seen == {"lost", "weakened", "ungrounded", "cycle"}


def test_limits_never_certify_and_an_output_limit_lists_a_prefix():
    rng = random.Random("support-limits")
    kinds = set()
    for _ in range(500):
        nodes, edges = random_graph(rng, max_nodes=9, max_edges=20)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(n) for n in nodes]
        params = {"failed": rng.sample(ids, rng.randint(1, min(3, len(ids)))), "scope": random_scope(rng),
                  "all_of": rng.choice([None, ["A"]])}
        full = run(p, nodes, edges, **params)
        limits = rng.choice([{"max_expansions": rng.randint(0, 30)}, {"max_results": rng.randint(0, 4)}])
        res = run(p, nodes, edges, **params, limits=limits)
        if res.status == COMPLETED:
            assert res.answer == full.answer
            kinds.add("completed")
            continue
        assert res.status == INTERRUPTED
        reason = res.interruption["reason"]
        kinds.add(reason)
        if reason == "expansion_budget":
            assert res.answer["lost"] is None and res.answer["weakened"] is None and res.answer["counts"] is None
        else:
            assert res.answer["counts"] == full.answer["counts"]
            listed = list(res.answer["lost"]) + list(res.answer["weakened"])
            assert len(listed) == limits["max_results"]
            assert res.answer["lost"] == full.answer["lost"][:len(res.answer["lost"])]
            if res.answer["weakened"]:
                assert res.answer["lost"] == full.answer["lost"]
                assert res.answer["weakened"] == full.answer["weakened"][:len(res.answer["weakened"])]
    assert kinds == {"completed", "expansion_budget", "output_limit"}


def test_more_failures_never_bring_a_node_back():
    rng = random.Random("support-monotone")
    for _ in range(300):
        nodes, edges = random_graph(rng)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        ids = [node_id(n) for n in nodes]
        scope, all_of = random_scope(rng), rng.choice([None, ["A"]])
        small = rng.sample(ids, 1)
        large = sorted(set(small) | set(rng.sample(ids, rng.randint(1, len(ids)))))
        a = run(p, nodes, edges, failed=small, scope=scope, all_of=all_of).answer
        b = run(p, nodes, edges, failed=large, scope=scope, all_of=all_of).answer
        assert {e["node"] for e in a["lost"]} <= {e["node"] for e in b["lost"]} | set(large)


# ------------------------------------------------------------------------------------------ scenarios
NODES = [{"id": "paper", "types": ["Source"]}, {"id": "notes", "types": ["Source"]},
         {"id": "q1", "types": ["Item"]}, {"id": "q2", "types": ["Item"]}, {"id": "claim", "types": ["Claim"]},
         {"id": "model", "types": ["Model"]}, {"id": "other", "types": ["Claim"]}]
EDGES = [{"id": "c1", "source": "q1", "target": "paper", "relation": "CITES"},
         {"id": "c2", "source": "q2", "target": "notes", "relation": "CITES"},
         {"id": "s1", "source": "claim", "target": "q1", "relation": "SUPPORTED_BY"},
         {"id": "s2", "source": "claim", "target": "q1", "relation": "SUPPORTED_BY"},       # parallel to s1
         {"id": "s3", "source": "claim", "target": "q2", "relation": "SUPPORTED_BY"},
         {"id": "s4", "source": "model", "target": "claim", "relation": "SUPPORTED_BY"},
         {"id": "s5", "source": "model", "target": "other", "relation": "SUPPORTED_BY"},
         {"id": "s6", "source": "other", "target": "other", "relation": "SUPPORTED_BY"}]  # supports only itself


@pytest.fixture
def p():
    return Projection.build(nodes=NODES, edges=EDGES)


def test_parallel_edges_to_one_support_are_one_support(p):
    res = run(p, NODES, EDGES, failed=["paper"], scope=SUPPORT)
    claim = next(e for e in res.answer["weakened"] if e["node"] == "claim")
    assert claim["supports"] == 2 and claim["standing"] == 1
    assert [h["edge"] for h in claim["failing"]] == ["s1"]          # the first of the two parallel hops
    assert [e["node"] for e in res.answer["lost"]] == ["q1"]


def test_a_node_that_supports_only_itself_never_stood(p):
    # other supports itself and nothing else: it never stood, so an AND model that needs it never stood either
    res = run(p, NODES, EDGES, failed=["paper"], scope=SUPPORT, all_of=["Model"])
    assert res.answer["counts"] == {"lost": 1, "weakened": 1, "ungrounded": 1}
    assert [e["node"] for e in res.answer["lost"]] == ["q1"]       # model was not standing: it is not lost
    # with every node needing one support, the model stands on the claim alone
    res = run(p, NODES, EDGES, failed=["notes", "paper"], scope=SUPPORT)
    lost = {e["node"]: e for e in res.answer["lost"]}
    assert set(lost) == {"claim", "model", "q1", "q2"}
    assert lost["model"]["standing"] == 0 and [h["to"] for h in lost["model"]["failing"]] == ["claim"]
    assert lost["model"]["causes"] == ("notes", "paper") and res.answer["counts"]["ungrounded"] == 0


def test_a_failure_nothing_depends_on_loses_nothing(p):
    res = run(p, NODES, EDGES, failed=["model"], scope=SUPPORT)
    assert res.status == COMPLETED and res.answer["lost"] == () and res.answer["weakened"] == ()
    assert res.answer["counts"] == {"lost": 0, "weakened": 0, "ungrounded": 0}


def test_only_the_region_the_failure_reaches_is_read():
    """Nodes that neither depend on a failed node nor support one that does are never read: a long chain beside the
    failure costs nothing."""
    nodes = [f"n{i:04d}" for i in range(2000)] + ["root", "leaf", "top"]
    edges = [{"id": f"e{i:04d}", "source": f"n{i:04d}", "target": f"n{i + 1:04d}", "relation": "SUPPORTED_BY"}
             for i in range(1999)]
    edges += [{"id": "x1", "source": "top", "target": "leaf", "relation": "SUPPORTED_BY"},
              {"id": "x2", "source": "leaf", "target": "root", "relation": "SUPPORTED_BY"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = run(p, nodes, edges, failed=["root"], scope=[("SUPPORTED_BY", "forward")])
    assert [e["node"] for e in res.answer["lost"]] == ["leaf", "top"]
    assert res.stats["discovered"] == 3 and res.stats["expansions"] < 20


def test_a_scope_on_a_relation_covers_the_relations_below_it():
    nodes = ["paper", "quote", "claim"]
    edges = [{"id": "c", "source": "quote", "target": "paper", "relation": "CITES_PAGE"},
             {"id": "s", "source": "claim", "target": "quote", "relation": "SUPPORTED_BY"}]
    rp = {"CITES_PAGE": ["CITES"]}
    p = Projection.build(nodes=nodes, edges=edges, relations=["CITES", "CITES_PAGE", "SUPPORTED_BY"],
                         relation_parents=rp)
    res = run(p, nodes, edges, relation_parents=rp, failed=["paper"], scope=SUPPORT)
    assert [e["node"] for e in res.answer["lost"]] == ["claim", "quote"]
    assert res.answer["lost"][1]["failing"][0]["relation"] == "CITES_PAGE"


@pytest.mark.parametrize("params, code", [
    ({"failed": [], "scope": SUPPORT}, "invalid_request"),
    ({"failed": ["paper"], "scope": SUPPORT, "all_of": []}, "invalid_request"),
    ({"failed": ["paper"], "scope": SUPPORT, "all_of": "Model"}, "invalid_request"),
    ({"failed": ["paper"], "scope": SUPPORT, "all_of": ["Model"], "max_depth": 2}, "invalid_request"),
    ({"failed": ["paper"]}, "invalid_request"),
    ({"failed": ["nowhere"], "scope": SUPPORT}, "unknown_node"),
    ({"failed": ["paper"], "scope": SUPPORT, "all_of": ["Theory"]}, "unknown_node_type"),
    ({"failed": ["paper"], "scope": [("DERIVES", "forward")]}, "unknown_relation"),
])
def test_bad_support_requests_fail_in_the_result(p, params, code):
    res = execute(p, {"operation": "support", "projection": p.identity, **params})
    assert res.status == FAILED and res.error["code"] == code


def test_a_support_request_normalizes_its_sets():
    a = normalize_request({"operation": "support", "projection": "sha256:" + "0" * 64, "failed": ["b", "a", "b"],
                           "scope": [("S", "forward"), ("S", "forward")], "all_of": ["Y", "X"]})
    b = normalize_request({"operation": "support", "projection": "sha256:" + "0" * 64, "failed": ["a", "b"],
                           "scope": [{"relation": "S", "direction": "forward"}], "all_of": ["X", "Y"],
                           "limits": {"max_expansions": 1_000_000}})
    assert a == b and a["failed"] == ("a", "b") and a["all_of"] == ("X", "Y")
    with pytest.raises(RequestError):
        normalize_request({"operation": "support", "projection": "sha256:" + "0" * 64, "failed": ["a"],
                           "scope": [("S", "forward")], "limits": {"max_depth": 1}})


def test_the_contract_refuses_a_support_answer_that_breaks_its_invariants(p):
    import copy
    from graph_runtime.result import contract_problems
    good = execute(p, p.request("support", failed=["notes", "paper"], scope=SUPPORT)).to_dict()
    assert contract_problems(good) == []
    for change in (lambda a: a["lost"].pop(),                                    # a complete answer lists every one
                   lambda a: a["lost"][0].update(causes=[]),                     # every loss has a cause
                   lambda a: a["lost"][0].update(causes=["claim"]),              # a cause is a failed node
                   lambda a: a["lost"][0].update(standing=a["lost"][0]["supports"]),
                   lambda a: a.update(lost=None),                                # unknown only when interrupted
                   lambda a: a["lost"].append(dict(a["lost"][0])),               # unique
                   lambda a: a.update(failed=["paper"])):                        # echoes the request
        bad = copy.deepcopy(good)
        change(bad["answer"])
        assert contract_problems(bad), change


def test_the_schema_describes_support():
    import jsonschema
    v = jsonschema.Draft202012Validator(json_schema("result"))
    nodes, edges = NODES, EDGES
    p = Projection.build(nodes=nodes, edges=edges)
    for limits in ({}, {"max_results": 1}, {"max_expansions": 2}):
        res = execute(p, p.request("support", failed=["paper"], scope=SUPPORT, all_of=["Model"], limits=limits))
        assert list(v.iter_errors(res.to_dict())) == []
