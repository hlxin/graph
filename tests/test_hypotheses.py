import json

import pytest

from graph_runtime import (CandidateError, HypothesisCandidate, Projection, execute, order_candidates, staleness,
                           validate_candidate, verify_revisions, witness_refs)
from graph_runtime._canonical import digest

TESTS = [{"description": "Recompute the ranking with the revised input.",
          "outcomes": [{"observation": "the ranking changes", "bearing": "supports"},
                       {"observation": "the ranking is unchanged", "bearing": "opposes"}]}]
NODES = ["input:v1", "model:m", "pred:p1", "pred:p2", "rank:r"]
EDGES = [{"id": "dep1", "source": "model:m", "target": "input:v1", "relation": "DEPENDS_ON"},
         {"id": "dep2", "source": "pred:p1", "target": "model:m", "relation": "DEPENDS_ON"},
         {"id": "dep3", "source": "pred:p2", "target": "model:m", "relation": "DEPENDS_ON"},
         {"id": "dep4", "source": "rank:r", "target": "pred:p1", "relation": "DEPENDS_ON"},
         {"id": "dep5", "source": "rank:r", "target": "pred:p2", "relation": "DEPENDS_ON"}]


@pytest.fixture
def proj():
    return Projection.build(nodes=NODES, edges=EDGES, bindings={"source": "synthetic"})


def make(proj, **kw):
    args = {"statement": "Revising input:v1 may change the order of pred:p1 and pred:p2 in rank:r.",
            "scope": "synthetic example; the ranking rank:r only", "projection": proj,
            "producer": {"name": "example-generator", "version": "0.1"},
            "basis": [("node", "input:v1"), ("node", "rank:r")],
            "dependencies": [("node", "input:v1"), ("node", "rank:r")],
            "assumptions": ["the revision changes values the model was trained on"],
            "proposed_tests": TESTS, "at": "2026-10-25T12:00:00Z",
            "scores": [{"value": 0.8, "method": "priority", "method_version": "0", "candidate_set": "set-1",
                        "direction": "higher_first"}]}
    return HypothesisCandidate.create(**{**args, **kw})


def codes(record):
    return {p["code"] for p in validate_candidate(record)}


def test_a_new_candidate_is_active_and_unverified(proj):
    c = make(proj)
    d = c.to_dict()
    assert (d["kind"], d["epistemic_status"], d["revision"], d["previous"]) == \
        ("hypothesis_candidate", "unverified", 1, None)
    assert c.state == "active" and c.id.startswith("hc:sha256:") and c.projection == proj.identity
    assert d["scores"][0] == {"value": 0.8, "method": "priority", "method_version": "0", "candidate_set": "set-1",
                              "direction": "higher_first", "interpretation": "ordering", "calibration": None}
    assert d["proposed_tests"][0]["id"] == "test-1"
    assert make(proj).to_dict() == d                     # the same content makes the same record
    assert validate_candidate(d) == []


def test_serialization_preserves_everything(proj):
    c = make(proj).attach_assessment(stance="opposing", evidence=[("external", "doi:10.0000/synthetic")],
                                     conditions="recomputed on input v2 under the same model settings",
                                     assessor={"name": "reviewer-agent", "version": "1"}, at="2026-10-26")
    c = c.withdraw(at="2026-10-27", reason="the scope changed to one prediction")
    back = HypothesisCandidate.from_json(c.to_json())
    assert back == c and back.to_dict() == c.to_dict()
    d = back.to_dict()
    assert d["epistemic_status"] == "unverified"
    assert [e["state"] for e in d["lifecycle"]] == ["active", "withdrawn"]
    assert d["assessments"][0]["stance"] == "opposing" and d["assumptions"]
    assert d["proposed_tests"] == c.to_dict()["proposed_tests"]
    assert json.loads(c.to_json(indent=2)) == d


def edit(record, **changes):
    out = json.loads(json.dumps(record))
    out.update(changes)
    out["record_digest"] = digest({k: v for k, v in out.items() if k != "record_digest"})
    return out


@pytest.mark.parametrize("changes,code", [
    ({"epistemic_status": "supported"}, "epistemic_status_fixed"),
    ({"kind": "assertion"}, "kind_fixed"),
    ({"proposed_tests": []}, "too_few"),
    ({"proposed_tests": [{**TESTS[0], "id": "t", "outcomes": [{"observation": "x", "bearing": "supports"},
                                                              {"observation": "y", "bearing": "supports"}]}]},
     "not_discriminating"),
    ({"proposed_tests": [{**TESTS[0], "id": "t", "outcomes": [{"observation": "x", "bearing": "supports"}]}]},
     "too_few"),
    ({"proposed_tests": [{**TESTS[0], "id": "t", "outcomes": [{"observation": "x", "bearing": "proves"},
                                                              {"observation": "y", "bearing": "opposes"}]}]},
     "invalid_bearing"),
    ({"basis": []}, "too_few"),
    ({"basis": [{"type": "claim", "ref": "x"}]}, "invalid_ref"),
    ({"basis": [{"type": "result", "ref": "r1"}]}, "invalid_ref"),
    ({"producer": {"name": "x"}}, "missing_field"),
    ({"projection": "p1"}, "invalid_projection"),
    ({"statement": "  "}, "invalid_text"),
    ({"scores": [{"value": 0.8, "method": "m", "method_version": "0", "candidate_set": "s",
                  "direction": "higher_first", "interpretation": "probability", "calibration": None}]},
     "uncalibrated_probability"),
    ({"scores": [{"value": 1.5, "method": "m", "method_version": "0", "candidate_set": "s",
                  "direction": "higher_first", "interpretation": "probability", "calibration": "cal:1"}]},
     "invalid_score"),
    ({"scores": [{"value": True, "method": "m", "method_version": "0", "candidate_set": "s",
                  "direction": "higher_first", "interpretation": "ordering", "calibration": None}]}, "invalid_score"),
    ({"scores": [{"value": 1, "method": "m", "method_version": "0", "candidate_set": "s",
                  "direction": "up", "interpretation": "ordering", "calibration": None}]}, "invalid_score"),
    ({"lifecycle": [{"state": "withdrawn", "at": "2026-10-25", "reason": "r", "superseded_by": None}]},
     "invalid_transition"),
    ({"lifecycle": [{"state": "active", "at": "2026-10-25", "reason": None, "superseded_by": None},
                    {"state": "active", "at": "2026-10-26", "reason": "again", "superseded_by": None}]},
     "invalid_transition"),
    ({"lifecycle": [{"state": "active", "at": "2026-10-25", "reason": None, "superseded_by": None},
                    {"state": "superseded", "at": "2026-10-26", "reason": "r", "superseded_by": None}]},
     "invalid_text"),
    ({"lifecycle": [{"state": "active", "at": "2026-10-25", "reason": None, "superseded_by": None},
                    {"state": "withdrawn", "at": "2026-10-26", "reason": "r", "superseded_by": "hc:x"}]},
     "invalid_transition"),
    ({"lifecycle": [{"state": "active", "at": "yesterday", "reason": None, "superseded_by": None}]}, "invalid_time"),
    ({"lifecycle": [{"state": "active", "at": "2026-10-25", "reason": None, "superseded_by": None},
                    {"state": "withdrawn", "at": "2026-10-26", "reason": None, "superseded_by": None}]},
     "invalid_text"),
    ({"status": "verified"}, "unknown_field"),
    ({"revision": 2}, "invalid_revision"),
    ({"assessments": [{"id": "a1", "stance": "proven", "evidence": [{"type": "external", "ref": "x"}],
                       "conditions": "c", "assessor": {"name": "n", "version": None}, "at": "2026-10-25",
                       "note": None}]}, "invalid_stance"),
    ({"assessments": [{"id": "a1", "stance": "opposing", "evidence": [], "conditions": "c",
                       "assessor": {"name": "n", "version": None}, "at": "2026-10-25", "note": None}]}, "too_few"),
    ({"extensions": ["x"]}, "invalid_extensions"),
])
def test_invalid_records_are_refused(proj, changes, code):
    bad = edit(make(proj).to_dict(), **changes)
    assert code in codes(bad)
    with pytest.raises(CandidateError) as err:
        HypothesisCandidate.from_dict(bad)
    assert code in {p["code"] for p in err.value.problems}


def test_tampering_is_detected(proj):
    d = make(proj).to_dict()
    assert codes({**d, "statement": "Revising input:v1 changes the ranking."}) == {"digest_mismatch"}
    resealed = edit(d, statement="Revising input:v1 changes the ranking.")
    assert codes(resealed) == {"digest_mismatch"}          # the generated id is the digest of the core fields
    assert codes({**d, "scores": [{**d["scores"][0], "value": float("nan")}]}) == {"invalid_score"}


def test_the_epistemic_status_cannot_change(proj):
    c = make(proj)
    for name in dir(HypothesisCandidate):
        assert not any(w in name.lower() for w in ("promote", "verify", "confirm", "accept"))
    with pytest.raises(AttributeError):
        c.epistemic_status = "verified"
    with pytest.raises(TypeError):
        c["lifecycle"][0]["state"] = "withdrawn"
    supported = c.attach_assessment(stance="supporting", evidence=[("external", "dataset:x")], conditions="c",
                                    assessor="lab", at="2026-10-26")
    assert supported.epistemic_status == "unverified" and supported.state == "active"


def test_lifecycle_transitions_make_new_revisions(proj):
    c1 = make(proj)
    c2 = c1.withdraw(at="2026-10-26", reason="scope changed")
    assert (c1.state, c2.state, c2.revision, c2["previous"]) == ("active", "withdrawn", 2, c1.record_digest)
    with pytest.raises(CandidateError):
        c2.withdraw(at="2026-10-27", reason="again")
    with pytest.raises(CandidateError):
        c2.supersede(by="hc:other", at="2026-10-27", reason="r")
    successor = make(proj, statement="Revising input:v1 may change the rank of pred:p1.")
    c3 = c1.supersede(by=successor, at="2026-10-26", reason="narrower formulation")
    assert c3.state == "superseded" and c3.lifecycle[-1]["superseded_by"] == successor.id
    with pytest.raises(CandidateError):
        c1.supersede(by=c1, at="2026-10-26", reason="itself")
    c4 = c2.attach_assessment(stance="inconclusive", evidence=[("external", "run:1")], conditions="partial rerun",
                              assessor={"name": "lab"}, at="2026-10-28")
    assert c4.state == "withdrawn" and c4.revision == 3          # evidence may still arrive after a withdrawal


def test_scores(proj):
    c = make(proj, scores=[])
    c2 = c.add_score(value=0.3, method="m", method_version="1", candidate_set="s", direction="lower_first")
    assert c2.scores[0]["interpretation"] == "ordering"
    with pytest.raises(CandidateError) as err:
        c.add_score(value=0.3, method="m", method_version="1", candidate_set="s", direction="higher_first",
                    interpretation="probability")
    assert err.value.code == "uncalibrated_probability"
    c3 = c.add_score(value=0.3, method="m", method_version="1", candidate_set="s", direction="higher_first",
                     interpretation="probability", calibration="calibration:report-7")
    assert c3.scores[0]["calibration"] == "calibration:report-7"
    with pytest.raises(CandidateError):
        make(proj, candidate_id="hc:sha256:" + "0" * 64)
    assert make(proj, candidate_id="lab:H-12").id == "lab:H-12"


def test_histories_are_append_only(proj):
    c1 = make(proj)
    c2 = c1.attach_assessment(stance="opposing", evidence=[("external", "doi:x")], conditions="c", assessor="a",
                              at="2026-10-26")
    c3 = c2.withdraw(at="2026-10-27", reason="r")
    assert verify_revisions([c1, c2, c3]) == []
    assert verify_revisions([c2, c3], from_start=False) == []
    assert [x["code"] for x in verify_revisions([c2, c3])] == ["broken_history"]     # a partial history says so
    moved = edit(c3.to_dict(), extensions={"note": "rewritten in transport"})
    assert "core_changed" in {x["code"] for x in verify_revisions([c1, c2, moved])}
    dropped = edit(c3.to_dict(), assessments=[])           # a transport that loses the opposing evidence
    assert validate_candidate(dropped) == []
    assert {p["code"] for p in verify_revisions([c1, c2, dropped])} == {"history_rewritten"}
    assert "broken_history" in {p["code"] for p in verify_revisions([c1, c3])}
    renamed = edit(c2.to_dict(), assumptions=["another assumption"])
    assert {p["code"] for p in verify_revisions([c1, renamed])} == {"digest_mismatch"}   # a generated id is the core's
    own1 = make(proj, candidate_id="lab:H-1")
    own2 = edit(own1.withdraw(at="2026-10-26", reason="r").to_dict(), assumptions=["another assumption"])
    assert {p["code"] for p in verify_revisions([own1, own2])} == {"core_changed"}


def test_staleness_of_the_snapshot(proj):
    c = make(proj, dependencies=[("node", "pred:p1")], basis=[("node", "rank:r"), ("edge", "dep4"),
                                                               ("external", "doi:10.0000/x")])
    s = staleness(c, projection=proj)
    assert (s["question"], s["stale"], s["reasons"], s["undecided"]) == ("snapshot", False, [], [])
    assert s["outside"] == [{"field": "basis", "type": "external", "ref": "doi:10.0000/x"}]
    newer = Projection.build(nodes=NODES, edges=EDGES, bindings={"source": "synthetic, revised"})
    s = staleness(c, projection=newer)
    assert s["stale"] is True and s["reasons"][0]["code"] == "projection_changed"
    with pytest.raises(ValueError):
        staleness(c)


def test_staleness_follows_dependency_impact(proj):
    DEP_R = [("DEPENDS_ON", "reverse")]
    c = make(proj, dependencies=[("node", "pred:p1")], basis=[("node", "rank:r")])
    impact = execute(proj, proj.request("dependency_impact", changed=["input:v1"], scope=DEP_R))
    s = staleness(c, impact=impact)
    assert s["stale"] is True and [r["code"] for r in s["reasons"]] == ["input_affected", "input_affected"]
    assert [h["edge"] for h in s["reasons"][0]["witness"]] == ["dep1", "dep2"]
    assert c.state == "active" and "does not make it false" in s["note"]
    s = staleness(make(proj, dependencies=[("node", "input:v1")]), impact=impact)
    assert s["reasons"][0]["code"] == "input_revised"


def test_staleness_never_clears_what_it_cannot_decide(proj):
    """A node is cleared only by a completed impact without max_depth, on a projection that holds it; edge and
    result references are never cleared by node impact."""
    DEP_R = [("DEPENDS_ON", "reverse")]
    model_change = execute(proj, proj.request("dependency_impact", changed=["model:m"], scope=DEP_R))
    untouched = make(proj, dependencies=[("node", "input:v1")], basis=[("node", "input:v1")])
    assert staleness(untouched, projection=proj, impact=model_change)["stale"] is False
    s = staleness(untouched, impact=model_change)                  # no projection: membership unknown
    assert s["stale"] is None and "unknown" in s["undecided"][0]["why"]
    elsewhere = Projection.build(nodes=["other"], edges=[])
    other = make(proj, basis=[("node", "input:v1")], dependencies=[])
    assert staleness(other, projection=elsewhere, impact=model_change)["stale"] is None   # not the impact's projection
    with_edge = make(proj, basis=[("edge", "dep1")], dependencies=[])
    s = staleness(with_edge, projection=proj, impact=model_change)
    assert s["stale"] is None and s["undecided"][0]["type"] == "edge"
    shallow = execute(proj, proj.request("dependency_impact", changed=["input:v1"], scope=DEP_R, max_depth=1))
    far = make(proj, dependencies=[("node", "rank:r")], basis=[("node", "rank:r")])
    s = staleness(far, projection=proj, impact=shallow)
    assert s["stale"] is None and "max_depth 1" in s["undecided"][0]["why"]
    cut = execute(proj, proj.request("dependency_impact", changed=["input:v1"], scope=DEP_R,
                                     limits={"max_expansions": 1}))
    s = staleness(far, projection=proj, impact=cut)
    assert s["stale"] is None and "interrupted" in s["undecided"][0]["why"]
    missing = HypothesisCandidate.create(statement="s", scope="synthetic", projection=proj,
                                         producer={"name": "t", "version": "0"}, basis=[("node", "not-in-graph")],
                                         proposed_tests=TESTS, at="2026-10-25")
    s = staleness(missing, projection=proj, impact=model_change)
    assert s["stale"] is None and "does not hold" in s["undecided"][0]["why"]


def test_ordering_stays_within_one_method_and_candidate_set(proj):
    def scored(statement, value, **kw):
        return make(proj, statement=statement, scores=[{"value": value, "method": "m", "method_version": "1",
                                                        "candidate_set": "set-1", "direction": "higher_first", **kw}])
    a, b, c = scored("A", 0.2), scored("B", 0.9), scored("C", 0.2)
    other = make(proj, statement="D", scores=[{"value": 5, "method": "m", "method_version": "2",
                                               "candidate_set": "set-1", "direction": "higher_first"}])
    gone = scored("E", 1.0).withdraw(at="2026-10-26", reason="r")
    out = order_candidates([a, b, c, other, gone], method="m", method_version="1", candidate_set="set-1")
    assert [x["candidate"] for x in out["order"]] == [b.id] + sorted([a.id, c.id])
    assert out["unscored"] == [other.id] and out["not_in_states"] == [gone.id]
    assert out["interpretation"] == "ordering" and "a probability only with" in out["note"]
    with pytest.raises(ValueError):
        order_candidates([a, scored("F", 0.1, direction="lower_first")], method="m", method_version="1",
                         candidate_set="set-1")


def test_witness_references(proj):
    res = execute(proj, proj.request("reachability", sources=["rank:r"], scope=[("DEPENDS_ON", "forward")]))
    refs = witness_refs(res, "input:v1")
    assert refs[0] == {"type": "result", "ref": res.result_id}
    assert [r["ref"] for r in refs[1:]] == ["rank:r", "dep4", "pred:p1", "dep2", "model:m", "dep1", "input:v1"]
    c = make(proj, basis=refs)
    assert c.basis[0]["type"] == "result"


def test_candidate_edge_cases(proj):
    with pytest.raises(CandidateError) as err:                      # identical observations cannot discriminate
        make(proj, proposed_tests=[{"description": "d", "outcomes": [
            {"observation": "rank rises", "bearing": "supports"},
            {"observation": "Rank  rises", "bearing": "opposes"}]}])
    assert err.value.code == "not_discriminating"
    for bad_time in ("2026-99-99", "2026-02-30", "2026-10-25T25:00Z", "2026-10-25T10:00+25:00"):
        with pytest.raises(CandidateError) as err:
            make(proj, at=bad_time)
        assert err.value.code == "invalid_time"
    with pytest.raises(CandidateError) as err:                      # a withdrawal is not dated before the creation
        make(proj).withdraw(at="1999-01-01", reason="r")
    assert err.value.code == "invalid_time"
    late = make(proj, at="2026-10-25T23:30:00-05:00").withdraw(at="2026-10-26T04:45:00Z", reason="r")
    assert late.state == "withdrawn"
    assert make(proj, at="2026-10-25T10:00:00.123456789Z").revision == 1
    with pytest.raises(CandidateError) as err:            # text that is not valid Unicode is a problem, not a crash
        make(proj, statement="bad \ud800 text")
    assert err.value.code == "invalid_text"
    c = make(proj)
    for name in ("_data", "kind"):
        with pytest.raises(AttributeError):
            delattr(c, name)
