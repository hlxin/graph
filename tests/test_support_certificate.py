"""Support certificates: with ``certificate: true`` a complete support answer carries both least fixpoints, each node
the analysis read with its rank before and after the failure (null where it does not stand). A checker confirms the
ranks rule by rule (a ground, or supports of lower rank that meet the node's rule) and the unranked nodes as unfounded
(failed, or their rule fails on unranked supports), so the answer is proven without recomputing either fixpoint, and
a cached answer is revalidated by checking its certificate against the edited records."""

import random

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.revalidate import revalidate
from oracle import RELATIONS, TYPES, node_id, random_graph, random_scope, support_answer
from test_certificates import reseal
from test_revalidate import edited
from test_support import EDGES, NODES, SUPPORT, entries, random_hierarchies


def request(rng, ids):
    return {"failed": rng.sample(ids, rng.randint(1, min(3, len(ids)))), "scope": random_scope(rng),
            "all_of": rng.choice([None, ["A"], ["B"], ["A", "C"]]), "certificate": True}


def cases(rng, n, **graph):
    for _ in range(n):
        nodes, edges = random_graph(rng, **graph)
        tp, rp = random_hierarchies(rng)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES, type_parents=tp,
                             relation_parents=rp)
        yield nodes, edges, tp, rp, p, request(rng, [node_id(x) for x in nodes])


def test_the_certificate_proves_the_oracle_answer():
    rng = random.Random("support-certificate")
    ranked = unfounded = 0
    for nodes, edges, tp, rp, p, params in cases(rng, 600, max_nodes=9, max_edges=20):
        res = execute(p, p.request("support", **params))
        assert res.status == COMPLETED
        assert verify_result(res, nodes=nodes, edges=edges, type_parents=tp, relation_parents=rp) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        want = support_answer(nodes, edges, params["failed"], params["scope"], params["all_of"], tp, rp)
        assert entries(res.answer, "lost") == want["lost"] and entries(res.answer, "weakened") == want["weakened"]
        cert = res.answer["certificate"]
        assert cert["kind"] == "fixpoints"
        assert all(e["after"] is None for e in cert["nodes"] if e["node"] in params["failed"])
        ranked += sum(e["before"] is not None and e["before"] > 0 for e in cert["nodes"])
        unfounded += sum(e["before"] is None for e in cert["nodes"])
        plain = execute(p, p.request("support", **{**params, "certificate": False}))
        assert plain.answer["certificate"] is None
        assert {k: v for k, v in plain.answer.items() if k != "certificate"} == \
            {k: v for k, v in res.answer.items() if k != "certificate"}
    assert ranked > 200 and unfounded > 200, (ranked, unfounded)


def forged(res, change):
    data = res.to_dict()
    change(data["answer"]["certificate"]["nodes"])
    return reseal(data)


def test_any_single_change_of_standing_is_refused():
    """Flipping one node between standing and not, in either fixpoint, breaks its own rule: a ranked node's rule
    holds on its ranked supports, and an unranked one's fails on its unranked supports, so neither can switch alone."""
    rng = random.Random("support-certificate-forged")
    tried = 0
    for nodes, edges, tp, rp, p, params in cases(rng, 300, max_nodes=9, max_edges=20):
        res = execute(p, p.request("support", **params))
        for k, e in enumerate(res.answer["certificate"]["nodes"]):
            for field in ("before", "after"):
                def flip(entries, k=k, field=field):
                    entries[k][field] = 0 if entries[k][field] is None else None
                bad = forged(res, flip)
                assert verify_result(bad, nodes=nodes, edges=edges, type_parents=tp, relation_parents=rp), \
                    (params, e, field)
                tried += 1
    assert tried > 2000


def test_unclassified_low_and_circular_ranks_are_refused():
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("support", failed=["paper"], scope=SUPPORT, certificate=True))
    assert verify_result(res, nodes=NODES, edges=EDGES) == []
    ranks = {e["node"]: (e["before"], e["after"]) for e in res.answer["certificate"]["nodes"]}
    assert ranks == {"paper": (0, None), "notes": (0, 0), "q1": (1, None), "q2": (1, 1), "claim": (2, 2),
                     "model": (3, 3), "other": (None, None)}         # other supports only itself: unfounded

    def drop(name):
        return lambda entries: entries.remove(next(e for e in entries if e["node"] == name))

    def rank(name, field, value):
        def change(entries):
            next(e for e in entries if e["node"] == name)[field] = value
        return change
    for change, problem in ((drop("q1"), "unclassified"),                   # in the region
                            (drop("q2"), "unclassified"),                   # a support of the region
                            (drop("notes"), "lower rank"),                  # q2's rule then rests on nothing
                            (rank("claim", "before", 1), "lower rank"),     # its supports rank 1: not below
                            (rank("other", "before", 4), "lower rank"),     # a circle grounds nothing
                            (rank("paper", "after", 0), "failed node")):
        problems = verify_result(forged(res, change), nodes=NODES, edges=EDGES)
        assert any(problem in x for x in problems), (problem, problems)


def test_revalidation_by_certificate_agrees_with_fresh_runs():
    rng = random.Random("support-certificate-revalidate")
    tally = {}
    for nodes, edges, tp, rp, p, params in cases(rng, 700, max_nodes=8, max_edges=16):
        old = execute(p, p.request("support", **params))
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES, type_parents=tp,
                             relation_parents=rp)
        check = revalidate(old, q)
        fresh = execute(q, {**old.request, "projection": q.identity})

        def claims(res):
            return {k: v for k, v in res.answer.items() if k != "certificate"}
        if check.verdict == "holds":
            assert fresh.status == COMPLETED and claims(fresh) == claims(old)
            assert verify_result(old, nodes=nodes2, edges=edges2, type_parents=tp, relation_parents=rp) == []
        elif check.verdict == "fails":
            assert fresh.status != COMPLETED or claims(fresh) != claims(old), check.reasons
        tally[check.verdict] = tally.get(check.verdict, 0) + 1
    assert tally.get("holds", 0) >= 150 and tally.get("fails", 0) >= 50 and tally.get("unknown", 0) >= 50, tally


def test_an_edit_that_breaks_only_the_proof_leaves_the_answer_unknown():
    """Grounding the circle of 'other' with a new source changes no listed entry (it is not in the failure's
    region), but the certificate called it unfounded; one that no longer checks proves nothing either way."""
    p = Projection.build(nodes=NODES, edges=EDGES)
    old = execute(p, p.request("support", failed=["paper"], scope=SUPPORT, certificate=True))
    nodes = NODES + [{"id": "lab", "types": ["Source"]}]
    edges = EDGES + [{"id": "s7", "source": "other", "target": "lab", "relation": "SUPPORTED_BY"}]
    check = revalidate(old, Projection.build(nodes=nodes, edges=edges))
    assert check.verdict == "unknown" and check.reasons[0]["code"] == "certificate_broken"
    plain = execute(p, p.request("support", failed=["paper"], scope=SUPPORT))
    assert revalidate(plain, Projection.build(nodes=nodes, edges=edges)).reasons[0]["code"] == "no_certificate"


def test_a_support_edit_refutes_the_entry_that_counted_it():
    p = Projection.build(nodes=NODES, edges=EDGES)
    old = execute(p, p.request("support", failed=["paper"], scope=SUPPORT, certificate=True))
    edges = EDGES + [{"id": "s8", "source": "q1", "target": "notes", "relation": "CITES"}]   # q1 now cites notes
    check = revalidate(old, Projection.build(nodes=NODES, edges=edges))
    assert check.verdict == "fails" and check.reasons[0]["code"] == "answer_refuted"


def test_an_interrupted_analysis_carries_no_certificate():
    rng = random.Random("support-certificate-limits")
    stopped = set()
    for nodes, edges, tp, rp, p, params in cases(rng, 300, max_nodes=9, max_edges=20):
        limits = rng.choice([{"max_expansions": rng.randint(0, 30)}, {"max_results": rng.randint(0, 3)}])
        res = execute(p, p.request("support", **params, limits=limits))
        assert verify_result(res, nodes=nodes, edges=edges, type_parents=tp, relation_parents=rp) == []
        if res.status == INTERRUPTED:
            assert res.answer["certificate"] is None
            stopped.add(res.interruption["reason"])
        else:
            assert res.answer["certificate"] is not None
    assert stopped == {"expansion_budget", "output_limit"}


def test_checking_a_certificate_is_linear_in_the_failures():
    """16,000 isolated nodes, all failed: each is a ground that stood and fell. Checking the result once rebuilt the
    failed set per certificate node (16,000**2 steps, 9 s)."""
    import time
    nodes = [f"n{i:05d}" for i in range(16000)]
    p = Projection.build(nodes=nodes, edges=[], relations=["R"])
    res = execute(p, p.request("support", failed=nodes, scope=[("R", "forward")], certificate=True))
    start = time.perf_counter()
    assert Result.from_dict(res.to_dict()).result_id == res.result_id
    assert verify_result(res, nodes=nodes, edges=[]) == []
    assert time.perf_counter() - start < 3                   # about 0.1 s
    assert res.answer["counts"] == {"lost": 0, "weakened": 0, "ungrounded": 0}


def test_support_certificates_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    p = Projection.build(nodes=NODES, edges=EDGES)
    for certificate in (True, False):
        res = execute(p, p.request("support", failed=["paper"], scope=SUPPORT, certificate=certificate))
        assert list(validator.iter_errors(res.to_dict())) == []
