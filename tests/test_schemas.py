"""The JSON Schemas describe what the runtime writes: every result, request, candidate and snapshot it makes
validates, and records that break the contract do not."""

import json
import random

import pytest

from graph_runtime import CancelToken, HypothesisCandidate, Projection, execute, json_schema
from oracle import RELATIONS, TYPES, node_id, random_graph, random_scope
from test_hypotheses import TESTS

jsonschema = pytest.importorskip("jsonschema")


def validator(entry):
    return jsonschema.Draft202012Validator(json_schema(entry))


def errors(entry, record):
    return [e.message for e in validator(entry).iter_errors(json.loads(json.dumps(record)))]


def test_the_schema_document_is_valid():
    jsonschema.Draft202012Validator.check_schema(json_schema())
    with pytest.raises(KeyError):
        json_schema("answer")


def test_every_result_and_request_validates():
    rng = random.Random("schemas")
    seen = set()
    for i in range(150):
        nodes, edges = random_graph(rng)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        assert errors("projection", p.to_dict()) == []
        ids = [node_id(n) for n in nodes]
        scope = random_scope(rng)
        costs = rng.choice([{"by_relation": {r: rng.choice([0, 1, 2**70, 0.5]) for r in RELATIONS}},
                            {"by_relation": {r: rng.randint(0, 3) for r in RELATIONS}}])
        requests = [
            {"operation": "reachability", "sources": ids[:2], "scope": scope, "max_depth": rng.choice([None, 1])},
            {"operation": "dependency_impact", "changed": ids[-2:], "scope": scope},
            {"operation": "typed_path", "sources": ids[:1], "targets": rng.choice([None, ids[-1:]]),
             "pattern": [{"scope": random_scope(rng), "node_types": rng.choice([None, ["A"]])}] * rng.randint(1, 3)},
            {"operation": "shortest_path", "sources": ids[:1], "targets": ids[-2:], "scope": scope, "costs": costs},
        ]
        for raw in requests:
            limits = {"max_expansions": rng.randint(0, 4)}
            if raw["operation"] in ("reachability", "dependency_impact"):
                limits["max_results"] = rng.randint(0, 4)
            cancel = [CancelToken(), lambda: True][i % 2]
            for extra, ctx in (({}, {}), ({"limits": limits}, {}), ({}, {"cancel": cancel})):
                res = execute(p, {**raw, **extra, "projection": p.identity}, **ctx)
                assert errors("result", res.to_dict()) == [], res.to_dict()
                assert errors("request", res.to_dict()["request"]) == []
                seen.add((res.operation, res.status, str((res.answer or {}).get("found"))))
    for raw in ({"operation": "pagerank", "projection": p.identity}, {"operation": "reachability"},
                {"operation": "reachability", "projection": "sha256:" + "1" * 64, "sources": ["x"],
                 "scope": [("R", "forward")]},
                {"operation": "reachability", "projection": p.identity, "sources": ["nowhere"],
                 "scope": [("R", "forward")]}):
        res = execute(p, raw)
        assert errors("result", res.to_dict()) == []
        seen.add((res.operation, res.status, "-"))
    statuses = {s for _, s, _ in seen}
    assert statuses == {"completed", "interrupted", "unsupported", "failed", "stale_snapshot"}
    assert ("shortest_path", "interrupted", "True") in seen and ("typed_path", "completed", "False") in seen


def candidate():
    p = Projection.build(nodes=["x"], edges=[])
    return HypothesisCandidate.create(statement="s", scope="synthetic", projection=p,
                                      producer={"name": "t", "version": "0"}, basis=[("node", "x")],
                                      proposed_tests=TESTS, at="2026-10-25",
                                      scores=[{"value": 0.8, "method": "m", "method_version": "0",
                                               "candidate_set": "c", "direction": "higher_first"}])


def test_candidates_validate_at_every_revision():
    c1 = candidate()
    c2 = c1.attach_assessment(stance="opposing", evidence=[("external", "doi:x")], conditions="c", assessor="a",
                              at="2026-10-26T10:00:00Z")
    c3 = c2.supersede(by="lab:H-2", at="2026-10-27", reason="narrower")
    for c in (c1, c2, c3):
        assert errors("hypothesis_candidate", c.to_dict()) == []


@pytest.mark.parametrize("change", [
    {"epistemic_status": "supported"},
    {"kind": "assertion"},
    {"status": "verified"},
    {"proposed_tests": []},
    {"basis": []},
    {"scores": [{"value": 0.8, "method": "m", "method_version": "0", "candidate_set": "c",
                 "direction": "higher_first", "interpretation": "probability", "calibration": None}]},
    {"lifecycle": [{"state": "withdrawn", "at": "2026-10-25", "reason": "r", "superseded_by": None}]},
    {"lifecycle": [{"state": "active", "at": "2026-10-25", "reason": None, "superseded_by": None},
                   {"state": "superseded", "at": "2026-10-26", "reason": "r", "superseded_by": None}]},
    {"revision": 2},
])
def test_schema_refuses_broken_candidates(change):
    assert errors("hypothesis_candidate", {**candidate().to_dict(), **change})


def test_schema_refuses_broken_results():
    p = Projection.build(nodes=["a", "b"], edges=[{"id": "e", "source": "a", "target": "b", "relation": "R"}])
    good = execute(p, p.request("typed_path", sources=["a"], pattern=[{"scope": [("R", "forward")]}])).to_dict()
    assert errors("result", good) == []
    for bad in ({**good, "complete": False}, {**good, "status": "interrupted"},
                {**good, "answer": {"found": False, "witness": good["answer"]["witness"]}},
                {**good, "status": "failed", "complete": False}):
        assert errors("result", bad)
