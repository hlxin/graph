"""Hypothesis candidates: candidate explanations and proposed relationships made portable and inspectable, without
ever becoming established assertions.

A candidate record (``kind: hypothesis_candidate``, ``epistemic_status: unverified``, both fixed) holds its
statement and scope, the identity of the projection it was formed on, its producer and version, basis references,
dependencies, assumptions, at least one proposed test whose outcomes discriminate (one supports it, one opposes
it), optional proposed relations and ranking scores, its lifecycle history and the assessments consumers attach.

Records are immutable. A lifecycle change, an assessment or a score makes a new revision, which keeps everything the
previous revision held, appends to it, and names the previous revision's digest; :func:`verify_revisions` checks a
history for that, so opposing evidence or a withdrawal cannot drop out of a candidate in transport. The statement,
scope, projection, producer, basis, dependencies, assumptions, tests and proposed relations never change: a
reformulation is a new candidate, which may supersede the old.

GRAPH never assesses a candidate and never promotes one: no operation changes its epistemic status, a lifecycle
state says nothing about scientific truth, and a later measurement or established assertion is a separate
scientific record with its own provenance. A score with ``interpretation: ordering`` orders candidates within one
method and candidate set and is not a probability; ``interpretation: probability`` requires a calibration
reference, which the consuming application must assess. Projections refuse candidate records
(:mod:`graph_runtime.projection`), so a proposed relation is never traversed.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ._canonical import canonical_json, digest, freeze, is_digest, thaw
from .errors import CandidateError
from .projection import CONTRACT, Projection
from .result import COMPLETED, Result

CANDIDATE_KIND = "hypothesis_candidate"
EPISTEMIC_STATUS = "unverified"
LIFECYCLE_STATES = ("active", "withdrawn", "superseded")
REF_TYPES = ("node", "edge", "result", "external")
BEARINGS = ("supports", "opposes", "inconclusive")
STANCES = ("supporting", "opposing", "inconclusive")
SCORE_DIRECTIONS = ("higher_first", "lower_first")
SCORE_INTERPRETATIONS = ("ordering", "probability")
CORE_FIELDS = ("statement", "scope", "projection", "producer", "basis", "dependencies", "assumptions",
               "proposed_tests", "proposed_relations")
APPEND_ONLY = ("scores", "lifecycle", "assessments")
FIELDS = ("kind", "contract", "id", "revision", "previous", "epistemic_status", *CORE_FIELDS, *APPEND_ONLY,
          "extensions", "record_digest")
FIXED_FIELDS = ("kind", "contract", "id", "epistemic_status", *CORE_FIELDS, "extensions")   # the same in every revision
GENERATED_ID = "hc:sha256:"
STALE_NOTE = ("stale true: re-examine the candidate (that does not make it false, and nothing in it changes); "
              "false: every reference GRAPH tracks was decided unaffected, for the question asked (the snapshot, or "
              "the revisions in the impact); null: some could not be decided (undecided says why). External "
              "references are outside GRAPH's view (outside).")
ORDER_NOTE = ("An ordering score orders candidates within one method, version and candidate set only; it is a "
              "probability only with interpretation probability and a calibration reference, which the consuming "
              "application must assess.")
_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})?)?$")


# ---------------------------------------------------------------------------------------------- validation
def validate_candidate(record: Any) -> list[dict]:
    """Every problem of a candidate record, as ``{"path", "code", "message"}``; empty when it is valid."""
    return _validate(record, sealed=True)


def _validate(record: Any, *, sealed: bool) -> list[dict]:
    """``sealed``: the record carries its ``record_digest``, which must match; an unsealed record (one being made)
    has none yet."""
    out: list[dict] = []

    def bad(path: str, code: str, message: str) -> None:
        out.append({"path": path, "code": code, "message": message})

    if not isinstance(record, Mapping):
        bad("", "invalid_candidate", "a candidate record is a mapping")
        return out
    for f in sorted(str(k) for k in record if k not in FIELDS or (k == "record_digest" and not sealed)):
        bad(f, "unknown_field", f"{f!r} is not a field of a candidate record (consumer data goes in extensions)")
    for f in FIELDS:
        if f not in record and (sealed or f != "record_digest"):
            bad(f, "missing_field", f"a candidate record states {f}")
    if out:
        return out
    if record["kind"] != CANDIDATE_KIND:
        bad("kind", "kind_fixed", f"kind is always {CANDIDATE_KIND!r}")
    if record["contract"] != CONTRACT:
        bad("contract", "unsupported_contract", f"this runtime reads {CONTRACT!r}")
    if record["epistemic_status"] != EPISTEMIC_STATUS:
        bad("epistemic_status", "epistemic_status_fixed", "a candidate is always unverified; an established assertion "
            "or a measurement is a separate scientific record with its own provenance")
    _text(record["id"], "id", bad)
    rev, prev = record["revision"], record["previous"]
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
        bad("revision", "invalid_revision", "revision is a positive integer")
    elif (rev == 1) != (prev is None):
        bad("previous", "invalid_revision", "revision 1 has no previous record; every later revision names the "
            "previous revision's record_digest")
    if prev is not None and not is_digest(prev):
        bad("previous", "invalid_revision", "previous is a record digest (sha256:<64 hex>)")
    _text(record["statement"], "statement", bad)
    _text(record["scope"], "scope", bad)
    if not is_digest(record["projection"]):
        bad("projection", "invalid_projection", "projection is the identity of the projection the candidate was formed "
            "on (sha256:<64 hex>)")
    _producer(record["producer"], "producer", bad)
    _refs(record["basis"], "basis", bad, at_least_one=True)
    _refs(record["dependencies"], "dependencies", bad)
    if _list(record["assumptions"], "assumptions", bad):
        for i, a in enumerate(record["assumptions"]):
            _text(a, f"assumptions[{i}]", bad)
    _tests(record["proposed_tests"], bad)
    if _list(record["proposed_relations"], "proposed_relations", bad):
        for i, r in enumerate(record["proposed_relations"]):
            where = f"proposed_relations[{i}]"
            if _keys(r, where, ("source", "target", "relation"), (), bad):
                for k in ("source", "target", "relation"):
                    _text(r[k], f"{where}.{k}", bad)
    if _list(record["scores"], "scores", bad):
        for i, s in enumerate(record["scores"]):
            _score(s, f"scores[{i}]", bad)
    _lifecycle(record, bad)
    _assessments(record["assessments"], bad)
    ext = record["extensions"]
    if ext is not None and not isinstance(ext, Mapping):
        bad("extensions", "invalid_extensions", "extensions is a mapping of consumer data, or null")
    if out:
        return out
    try:
        body = thaw({k: v for k, v in record.items() if k != "record_digest"})
        want = digest(body)
        core_id = _core_id({k: body[k] for k in CORE_FIELDS})
    except (TypeError, ValueError) as exc:
        bad("", "not_json", f"a candidate record is plain JSON, without NaN or infinities: {exc}")
        return out
    if isinstance(record["id"], str) and record["id"].startswith(GENERATED_ID) and record["id"] != core_id:
        bad("id", "digest_mismatch", f"an id starting {GENERATED_ID!r} is the digest of the candidate's core fields")
    if sealed and record["record_digest"] != want:
        bad("record_digest", "digest_mismatch", "record_digest does not match the record's content")
    return out


def _text(value: Any, path: str, bad, *, optional: bool = False) -> bool:
    if optional and value is None:
        return True
    if not isinstance(value, str) or not value.strip():
        bad(path, "invalid_text", f"{path} is non-empty text" + (" or null" if optional else ""))
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        bad(path, "invalid_text", f"{path} is valid Unicode text (it holds an unpaired surrogate)")
        return False
    return True


def _list(value: Any, path: str, bad, *, at_least: int = 0) -> bool:
    if not isinstance(value, (list, tuple)):
        bad(path, "invalid_list", f"{path} is a list")
        return False
    if len(value) < at_least:
        bad(path, "too_few", f"{path} holds at least {at_least} item{'s' if at_least != 1 else ''}")
        return False
    return True


def _keys(value: Any, path: str, required: Sequence[str], optional: Sequence[str], bad) -> bool:
    if not isinstance(value, Mapping):
        bad(path, "invalid_item", f"{path} is a mapping")
        return False
    ok = True
    for k in sorted(str(k) for k in value if k not in required and k not in optional):
        bad(f"{path}.{k}", "unknown_field", f"{path} does not take {k!r}")
        ok = False
    for k in required:
        if k not in value:
            bad(f"{path}.{k}", "missing_field", f"{path} states {k}")
            ok = False
    return ok


def _moment(value: Any) -> tuple[date, datetime | None] | None:
    """(the date, the date-time or None) of an ISO 8601 date or date-time that names a real day and time; None when
    ``value`` is not one."""
    m = _TIME.match(value) if isinstance(value, str) else None
    if not m:
        return None
    try:
        day = date.fromisoformat(m[1])
        if m[2] is None:
            return day, None
        tz = None
        if m[6] == "Z":
            tz = timezone.utc
        elif m[6]:
            sign = 1 if m[6][0] == "+" else -1
            tz = timezone(sign * timedelta(hours=int(m[6][1:3]), minutes=int(m[6][4:6])))
        frac = int((m[5] or "0")[:6].ljust(6, "0"))
        return day, datetime(day.year, day.month, day.day, int(m[2]), int(m[3]), int(m[4] or 0), frac, tzinfo=tz)
    except ValueError:
        return None


def _before(later: tuple, earlier: tuple) -> bool:
    """Whether a moment is earlier than another: by the instant when both are date-times with offsets, else by the
    day (in UTC for a date-time with an offset)."""
    (ld, lt), (ed, et) = later, earlier
    if lt is not None and et is not None and lt.tzinfo is not None and et.tzinfo is not None:
        return lt < et

    def day(d: date, t: datetime | None) -> date:
        return t.astimezone(timezone.utc).date() if t is not None and t.tzinfo is not None else d
    return day(ld, lt) < day(ed, et)


def _time(value: Any, path: str, bad) -> None:
    if _moment(value) is None:
        bad(path, "invalid_time", f"{path} is an ISO 8601 date or date-time of a real day (2026-10-25, "
            "2026-10-25T14:00:00Z)")


def _producer(value: Any, path: str, bad) -> None:
    if _keys(value, path, ("name", "version"), (), bad):
        _text(value["name"], f"{path}.name", bad)
        _text(value["version"], f"{path}.version", bad)


def _refs(value: Any, path: str, bad, *, at_least_one: bool = False) -> None:
    if not _list(value, path, bad, at_least=1 if at_least_one else 0):
        return
    for i, r in enumerate(value):
        where = f"{path}[{i}]"
        if not _keys(r, where, ("type", "ref"), (), bad):
            continue
        if r["type"] not in REF_TYPES:
            bad(f"{where}.type", "invalid_ref", f"a reference's type is one of {list(REF_TYPES)}")
        if _text(r["ref"], f"{where}.ref", bad) and r["type"] == "result" and not is_digest(r["ref"]):
            bad(f"{where}.ref", "invalid_ref", "a result reference is a result_id (sha256:<64 hex>)")


def _tests(value: Any, bad) -> None:
    if not _list(value, "proposed_tests", bad, at_least=1):
        return
    seen = set()
    for i, t in enumerate(value):
        where = f"proposed_tests[{i}]"
        if not _keys(t, where, ("id", "description", "outcomes"), (), bad):
            continue
        if _text(t["id"], f"{where}.id", bad):
            if t["id"] in seen:
                bad(f"{where}.id", "duplicate_id", f"two proposed tests have the id {t['id']!r}")
            seen.add(t["id"])
        _text(t["description"], f"{where}.description", bad)
        if not _list(t["outcomes"], f"{where}.outcomes", bad, at_least=2):
            continue
        bearings, observed = set(), set()
        for j, o in enumerate(t["outcomes"]):
            ow = f"{where}.outcomes[{j}]"
            if not _keys(o, ow, ("observation", "bearing"), (), bad):
                continue
            if _text(o["observation"], f"{ow}.observation", bad):
                key = " ".join(o["observation"].split()).casefold()
                if key in observed:
                    bad(f"{ow}.observation", "not_discriminating", "two outcomes of a proposed test describe the same "
                        "observation, which cannot both support and oppose the candidate")
                observed.add(key)
            if o["bearing"] not in BEARINGS:
                bad(f"{ow}.bearing", "invalid_bearing", f"an outcome's bearing is one of {list(BEARINGS)}")
            bearings.add(o["bearing"])
        if not {"supports", "opposes"} <= bearings:
            bad(f"{where}.outcomes", "not_discriminating", "a proposed test has an outcome that supports the candidate "
                "and one that opposes it")


def _score(s: Any, where: str, bad) -> None:
    if not _keys(s, where, ("value", "method", "method_version", "candidate_set", "direction", "interpretation",
                             "calibration"), (), bad):
        return
    v = s["value"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        bad(f"{where}.value", "invalid_score", "a score's value is a finite number")
        v = None
    for k in ("method", "method_version", "candidate_set"):
        _text(s[k], f"{where}.{k}", bad)
    if s["direction"] not in SCORE_DIRECTIONS:
        bad(f"{where}.direction", "invalid_score", f"direction is one of {list(SCORE_DIRECTIONS)}")
    if s["interpretation"] not in SCORE_INTERPRETATIONS:
        bad(f"{where}.interpretation", "invalid_score", f"interpretation is one of {list(SCORE_INTERPRETATIONS)}")
    _text(s["calibration"], f"{where}.calibration", bad, optional=True)
    if s["interpretation"] == "probability":
        if not isinstance(s["calibration"], str) or not s["calibration"].strip():
            bad(f"{where}.calibration", "uncalibrated_probability", "a score read as a probability names its calibration "
                "reference; an uncalibrated score is an ordering value")
        if v is not None and not 0 <= v <= 1:
            bad(f"{where}.value", "invalid_score", "a probability lies between 0 and 1")


def _lifecycle(record: Mapping, bad) -> None:
    events = record["lifecycle"]
    if not _list(events, "lifecycle", bad, at_least=1):
        return
    for i, e in enumerate(events):
        where = f"lifecycle[{i}]"
        if not _keys(e, where, ("state", "at", "reason", "superseded_by"), (), bad):
            continue
        state = e["state"]
        if state not in LIFECYCLE_STATES:
            bad(f"{where}.state", "invalid_state", f"a lifecycle state is one of {list(LIFECYCLE_STATES)}")
        elif (i == 0) != (state == "active"):
            bad(f"{where}.state", "invalid_transition", "a candidate starts active and is never active again: "
                "withdrawn and superseded are final")
        _time(e["at"], f"{where}.at", bad)
        _text(e["reason"], f"{where}.reason", bad, optional=state == "active")
        if state == "superseded":
            if _text(e["superseded_by"], f"{where}.superseded_by", bad) and e["superseded_by"] == record["id"]:
                bad(f"{where}.superseded_by", "invalid_transition", "a candidate does not supersede itself")
        elif e["superseded_by"] is not None:
            bad(f"{where}.superseded_by", "invalid_transition", "only a superseded candidate names its successor")
    if len(events) > 2:
        bad("lifecycle", "invalid_transition", "withdrawn and superseded are final: a lifecycle has at most two events")
    for i in range(1, len(events)):
        a = _moment(events[i - 1].get("at")) if isinstance(events[i - 1], Mapping) else None
        b = _moment(events[i].get("at")) if isinstance(events[i], Mapping) else None
        if a is not None and b is not None and _before(b, a):
            bad(f"lifecycle[{i}].at", "invalid_time", "a lifecycle event is not dated before the event it follows")


def _assessments(value: Any, bad) -> None:
    if not _list(value, "assessments", bad):
        return
    seen = set()
    for i, a in enumerate(value):
        where = f"assessments[{i}]"
        if not _keys(a, where, ("id", "stance", "evidence", "conditions", "assessor", "at", "note"), (), bad):
            continue
        if _text(a["id"], f"{where}.id", bad):
            if a["id"] in seen:
                bad(f"{where}.id", "duplicate_id", f"two assessments have the id {a['id']!r}")
            seen.add(a["id"])
        if a["stance"] not in STANCES:
            bad(f"{where}.stance", "invalid_stance", f"a stance is one of {list(STANCES)}")
        _refs(a["evidence"], f"{where}.evidence", bad, at_least_one=True)
        _text(a["conditions"], f"{where}.conditions", bad)
        if _keys(a["assessor"], f"{where}.assessor", ("name", "version"), (), bad):
            _text(a["assessor"]["name"], f"{where}.assessor.name", bad)
            _text(a["assessor"]["version"], f"{where}.assessor.version", bad, optional=True)
        _time(a["at"], f"{where}.at", bad)
        _text(a["note"], f"{where}.note", bad, optional=True)


def _core_id(core: Mapping) -> str:
    return "hc:" + digest({"kind": CANDIDATE_KIND, **core})


# ---------------------------------------------------------------------------------------------- records
def _plain(value: Any, what: str) -> Any:
    """A JSON copy that shares nothing with the caller's objects (tuples become lists). NaN and infinities pass
    here, so that validation can say where they are."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise CandidateError([{"path": what, "code": "not_json", "message": f"{what} is plain JSON: {exc}"}]) from None


def _ref(r: Any) -> Any:
    if isinstance(r, (list, tuple)) and len(r) == 2:
        return {"type": r[0], "ref": r[1]}
    return r


class HypothesisCandidate:
    """An immutable candidate record. Make one with :meth:`create`, read one with :meth:`from_dict`; every change
    (:meth:`withdraw`, :meth:`supersede`, :meth:`attach_assessment`, :meth:`add_score`) returns a new revision and
    leaves this one as it was."""

    __slots__ = ("_data",)
    kind = CANDIDATE_KIND           # read by Projection.build, which refuses candidates

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("make a candidate with HypothesisCandidate.create(...) or HypothesisCandidate.from_dict(...)")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a HypothesisCandidate is immutable; its changes are new revisions")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("a HypothesisCandidate is immutable; its changes are new revisions")

    @classmethod
    def create(cls, *, statement: str, scope: str, projection: Projection | str, producer: Mapping[str, str],
               basis: Iterable[Any], proposed_tests: Iterable[Mapping], at: str, dependencies: Iterable[Any] = (),
               assumptions: Iterable[str] = (), proposed_relations: Iterable[Mapping] = (),
               scores: Iterable[Mapping] = (), reason: str | None = None, candidate_id: str | None = None,
               extensions: Mapping | None = None) -> "HypothesisCandidate":
        """A new, active candidate (revision 1). ``basis`` and ``dependencies``: references ``{"type", "ref"}`` (or
        ``(type, ref)`` pairs) to nodes and edges of the projection, results (by ``result_id``) or external records.
        ``proposed_tests``: ``{"description", "outcomes": [{"observation", "bearing"}], "id"?}``. ``scores``: see
        :meth:`add_score`. Its ``id`` is the digest of its core fields unless ``candidate_id`` names one."""
        if isinstance(candidate_id, str) and candidate_id.startswith(GENERATED_ID):
            raise CandidateError([{"path": "id", "code": "reserved_id", "message": f"ids starting {GENERATED_ID!r} are "
                                   "generated from the core fields; leave candidate_id out or choose another"}])
        tests = []
        for i, t in enumerate(_plain(list(proposed_tests), "proposed_tests")):
            tests.append({"id": t.get("id") or f"test-{i + 1}", **{k: v for k, v in t.items() if k != "id"}}
                         if isinstance(t, dict) else t)
        core = {"statement": statement, "scope": scope,
                "projection": projection.identity if isinstance(projection, Projection) else projection,
                "producer": _plain(producer, "producer"),
                "basis": _plain([_ref(r) for r in basis], "basis"),
                "dependencies": _plain([_ref(r) for r in dependencies], "dependencies"),
                "assumptions": _plain(list(assumptions), "assumptions"), "proposed_tests": tests,
                "proposed_relations": _plain(list(proposed_relations), "proposed_relations")}
        record = {"kind": CANDIDATE_KIND, "contract": CONTRACT,
                  "id": candidate_id if candidate_id is not None else "hc:pending",
                  "revision": 1, "previous": None, "epistemic_status": EPISTEMIC_STATUS, **core,
                  "scores": [_score_record(s) for s in _plain(list(scores), "scores")],
                  "lifecycle": [{"state": "active", "at": at, "reason": reason, "superseded_by": None}],
                  "assessments": [], "extensions": _plain(extensions, "extensions")}
        if candidate_id is None:            # the generated id is the digest of core fields that validate first
            problems = _validate(_plain(record, "candidate"), sealed=False)
            if problems:
                raise CandidateError(problems)
            record["id"] = _core_id(_plain(core, "candidate"))
        return cls._seal(record)

    @classmethod
    def _seal(cls, record: dict) -> "HypothesisCandidate":
        record = _plain({k: v for k, v in record.items() if k != "record_digest"}, "candidate")
        problems = _validate(record, sealed=False)
        if problems:
            raise CandidateError(problems)
        record["record_digest"] = digest(record)
        c = object.__new__(cls)
        object.__setattr__(c, "_data", freeze(record))
        return c

    @classmethod
    def from_dict(cls, data: Mapping) -> "HypothesisCandidate":
        """Read a candidate record; raises :class:`CandidateError` with every problem when it is not valid (its
        ``record_digest`` must match its content)."""
        record = _plain(data, "candidate")
        problems = validate_candidate(record)
        if problems:
            raise CandidateError(problems)
        c = object.__new__(cls)
        object.__setattr__(c, "_data", freeze(record))
        return c

    @classmethod
    def from_json(cls, text: str) -> "HypothesisCandidate":
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict:
        return thaw(self._data)

    def to_json(self, *, indent: int | None = None) -> str:
        if indent is None:
            return canonical_json(self.to_dict())
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)

    # ------------------------------------------------------------------------------------------------ fields
    def __getitem__(self, field: str) -> Any:
        return self._data[field]

    @property
    def id(self) -> str:
        return self._data["id"]

    @property
    def revision(self) -> int:
        return self._data["revision"]

    @property
    def record_digest(self) -> str:
        return self._data["record_digest"]

    @property
    def projection(self) -> str:
        return self._data["projection"]

    @property
    def epistemic_status(self) -> str:
        return self._data["epistemic_status"]

    @property
    def state(self) -> str:
        """The current lifecycle state: active, withdrawn or superseded (never a statement about truth)."""
        return self._data["lifecycle"][-1]["state"]

    @property
    def basis(self) -> tuple:
        return self._data["basis"]

    @property
    def dependencies(self) -> tuple:
        return self._data["dependencies"]

    @property
    def scores(self) -> tuple:
        return self._data["scores"]

    @property
    def lifecycle(self) -> tuple:
        return self._data["lifecycle"]

    @property
    def assessments(self) -> tuple:
        return self._data["assessments"]

    def __repr__(self) -> str:
        return f"<HypothesisCandidate {self.id[:22]}… rev {self.revision} {self.state}>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, HypothesisCandidate) and other.record_digest == self.record_digest

    def __hash__(self) -> int:
        return hash(self.record_digest)

    # ------------------------------------------------------------------------------------------------ revisions
    def _revise(self, field: str, item: dict) -> "HypothesisCandidate":
        record = thaw(self._data)
        record["previous"] = record.pop("record_digest")
        record["revision"] += 1
        record[field].append(item)
        return self._seal(record)

    def _transition(self, state: str) -> None:
        if self.state != "active":
            raise CandidateError([{"path": "lifecycle", "code": "invalid_transition",
                                   "message": f"a {self.state} candidate cannot become {state}: withdrawn and superseded "
                                              "are final (a reformulation is a new candidate)"}])

    def withdraw(self, *, at: str, reason: str) -> "HypothesisCandidate":
        """A revision in which the candidate is withdrawn (a change of scope or formulation, say: not a finding that
        it is false)."""
        self._transition("withdrawn")
        return self._revise("lifecycle", {"state": "withdrawn", "at": at, "reason": reason, "superseded_by": None})

    def supersede(self, *, by: "HypothesisCandidate | str", at: str, reason: str) -> "HypothesisCandidate":
        """A revision in which the candidate is superseded by ``by`` (a candidate or its id)."""
        self._transition("superseded")
        successor = by.id if isinstance(by, HypothesisCandidate) else by
        return self._revise("lifecycle", {"state": "superseded", "at": at, "reason": reason,
                                          "superseded_by": successor})

    def attach_assessment(self, *, stance: str, evidence: Iterable[Any], conditions: str, assessor: Mapping,
                          at: str, note: str | None = None, assessment_id: str | None = None) -> "HypothesisCandidate":
        """A revision with a consumer's assessment attached: supporting, opposing or inconclusive evidence under the
        stated conditions. GRAPH stores it and never weighs it; the epistemic status stays unverified."""
        who = {"name": assessor, "version": None} if isinstance(assessor, str) else \
            {"name": None, "version": None, **_plain(dict(assessor), "assessor")}
        item = {"stance": stance, "evidence": _plain([_ref(r) for r in evidence], "evidence"),
                "conditions": conditions, "assessor": who, "at": at, "note": note}
        item = {"id": assessment_id or "as:" + digest({"candidate": self.id, **item}), **item}
        return self._revise("assessments", item)

    def add_score(self, *, value: float, method: str, method_version: str, candidate_set: str, direction: str,
                  interpretation: str = "ordering", calibration: str | None = None) -> "HypothesisCandidate":
        """A revision with a ranking score: its method and version, the identity of the candidate set it ranks, its
        direction (``higher_first`` or ``lower_first``) and interpretation (``ordering``, or ``probability`` with a
        calibration reference)."""
        return self._revise("scores", _score_record({"value": value, "method": method, "method_version": method_version,
                                                     "candidate_set": candidate_set, "direction": direction,
                                                     "interpretation": interpretation, "calibration": calibration}))


def _score_record(s: Any) -> Any:
    if isinstance(s, dict):
        return {"interpretation": "ordering", "calibration": None, **s}
    return s


# ---------------------------------------------------------------------------------------------- histories, staleness
def verify_revisions(revisions: Sequence["HypothesisCandidate | Mapping"], *, from_start: bool = True) -> list[dict]:
    """Problems in a candidate's revision history (consecutive revisions, oldest first): each revision is valid,
    names the previous one's digest, keeps every fixed field (the core fields, the kind, contract, epistemic status
    and extensions), and keeps every score, lifecycle event and assessment the previous revision held, in order,
    adding only after them. ``from_start``: the history begins at revision 1 (a partial history can only vouch for
    the revisions it holds)."""
    out: list[dict] = []
    records = [r.to_dict() if isinstance(r, HypothesisCandidate) else thaw(r) for r in revisions]
    if not records:
        return [{"path": "revisions", "code": "broken_history", "message": "a history holds at least one revision"}]
    for i, r in enumerate(records):
        for p in validate_candidate(r):
            out.append({**p, "path": f"revisions[{i}].{p['path']}".rstrip(".")})
    if out:
        return out
    if from_start and records[0]["revision"] != 1:
        out.append({"path": "revisions[0].revision", "code": "broken_history",
                    "message": f"the history starts at revision {records[0]['revision']}, not 1"})
    for i in range(1, len(records)):
        a, b = records[i - 1], records[i]
        where = f"revisions[{i}]"
        if b["id"] != a["id"]:
            out.append({"path": f"{where}.id", "code": "broken_history", "message": "a history holds one candidate"})
        if b["revision"] != a["revision"] + 1 or b["previous"] != a["record_digest"]:
            out.append({"path": f"{where}.previous", "code": "broken_history",
                        "message": f"revision {b['revision']} does not follow revision {a['revision']}"})
        for f in FIXED_FIELDS:
            if f != "id" and b[f] != a[f]:
                out.append({"path": f"{where}.{f}", "code": "core_changed",
                            "message": f"{f} never changes in a revision (a reformulation is a new candidate)"})
        for f in APPEND_ONLY:
            if b[f][:len(a[f])] != a[f]:
                out.append({"path": f"{where}.{f}", "code": "history_rewritten",
                            "message": f"{f} keeps every earlier item, in order, and only adds after them"})
    return out


def staleness(candidate: HypothesisCandidate, *, projection: Projection | None = None,
              impact: Result | None = None) -> dict:
    """Whether a candidate must be re-examined, as one of two questions.

    * The snapshot (``projection`` alone, the current projection): a different identity from the one the candidate
      was formed on makes it stale; the same identity means nothing it references inside the projection changed.
    * The dependencies (``impact``, a dependency_impact result of the revised nodes; ``projection``, when its identity
      is the impact's, to check that it holds a node): a node the candidate references that was revised, or that
      the impact reaches, makes it stale, with the witness. A node is cleared only by a completed impact without
      max_depth, on a projection that holds it. An edge or a result reference is not something node impact decides.

    ``stale`` is True when a reason was found, False only when every node, edge and result reference was decided,
    None otherwise (``undecided`` says which and why). External references are outside GRAPH's view (``outside``).
    Staleness is not falsity and changes nothing in the candidate."""
    if projection is None and impact is None:
        raise ValueError("staleness asks about the current projection, a dependency impact, or both")
    refs = [(field, dict(r)) for field in ("dependencies", "basis") for r in candidate[field]]
    reasons: list[dict] = []
    undecided: list[dict] = []
    outside = [{"field": f, "type": r["type"], "ref": r["ref"]} for f, r in refs if r["type"] == "external"]
    if impact is None:
        question = "snapshot"
        relative_to: dict = {"projection": projection.identity}
        if projection.identity != candidate.projection:
            reasons.append({"code": "projection_changed", "message": f"formed on projection {candidate.projection}; "
                            f"the current projection is {projection.identity}"})
    else:
        if impact.operation != "dependency_impact" or impact.answer is None:
            raise ValueError("impact is a completed or interrupted dependency_impact result")
        question = "dependencies"
        changed = set(impact.answer["changed"])
        relative_to = {"impact": impact.result_id, "projection": impact.projection, "changed": sorted(changed)}
        bound = impact.request["max_depth"]
        holder = projection if projection is not None and projection.identity == impact.projection else None
        for field, r in refs:
            kind, ref = r["type"], r["ref"]
            item = {"field": field, "type": kind, "ref": ref}
            if kind == "external":
                continue
            if kind != "node":
                undecided.append({**item, "why": f"dependency impact follows nodes; it does not decide an {kind} "
                                                 "reference" if kind == "edge" else "dependency impact follows nodes; "
                                                 f"it does not decide a {kind} reference"})
            elif ref in changed:
                reasons.append({"code": "input_revised", **item, "message": f"{field} names {ref}, which was revised"})
            elif impact.affected(ref):
                reasons.append({"code": "input_affected", **item, "witness": impact.path_to(ref),
                                "message": f"{field} names {ref}, which depends on a revised node"})
            elif impact.status != COMPLETED:
                undecided.append({**item, "why": "the impact was interrupted before it could clear this node"})
            elif bound is not None:
                undecided.append({**item, "why": f"the impact stopped at max_depth {bound}"})
            elif holder is None:
                undecided.append({**item, "why": "without the projection the impact ran on, whether it holds this "
                                                 "node is unknown"})
            elif not holder.has_node(ref):
                undecided.append({**item, "why": "the projection the impact ran on does not hold this node"})
    return {"candidate": candidate.id, "revision": candidate.revision, "question": question,
            "stale": True if reasons else (None if undecided else False), "reasons": reasons, "undecided": undecided,
            "outside": outside, "relative_to": relative_to, "note": STALE_NOTE}


def order_candidates(candidates: Iterable[HypothesisCandidate], *, method: str, method_version: str,
                     candidate_set: str, states: Sequence[str] = ("active",)) -> dict:
    """Order candidates by their score for one method, method version and candidate set (the latest such score of
    each), in the score's direction; ties by candidate id. Candidates without such a score, and those whose state is
    not in ``states``, are listed apart, never ranked. Scores of one method and set must agree on direction and
    interpretation."""
    scored, unscored, left_out = [], [], []
    direction = interpretation = None
    for c in candidates:
        if c.state not in states:
            left_out.append(c.id)
            continue
        mine = [s for s in c.scores if s["method"] == method and s["method_version"] == method_version
                and s["candidate_set"] == candidate_set]
        if not mine:
            unscored.append(c.id)
            continue
        s = mine[-1]
        if direction is None:
            direction, interpretation = s["direction"], s["interpretation"]
        elif (s["direction"], s["interpretation"]) != (direction, interpretation):
            raise ValueError(f"scores of {method} {method_version} on {candidate_set} disagree on direction or "
                             "interpretation")
        scored.append((s["value"], c.id))
    sign = -1 if direction == "higher_first" else 1
    scored.sort(key=lambda vc: (sign * vc[0], vc[1]))
    return {"method": method, "method_version": method_version, "candidate_set": candidate_set,
            "direction": direction, "interpretation": interpretation,
            "order": [{"candidate": cid, "value": v} for v, cid in scored],
            "unscored": sorted(unscored), "not_in_states": sorted(left_out), "note": ORDER_NOTE}


def witness_refs(result: Result, node: str | None = None) -> list[dict]:
    """Basis references for a candidate drawn from a result: the result itself, then the edges and nodes of its
    witness (of ``node`` for a reachability or impact result)."""
    if result.operation in ("reachability", "dependency_impact"):
        if node is None:
            raise ValueError("name the node whose witness to cite")
        hops = result.path_to(node)
    else:
        hops = result.witness or []
    refs = [{"type": "result", "ref": result.result_id}]
    seen = set()
    for h in hops:
        for t, ref in (("node", h["from"]), ("edge", h["edge"]), ("node", h["to"])):
            if (t, ref) not in seen:
                seen.add((t, ref))
                refs.append({"type": t, "ref": ref})
    return refs
