# Hypothesis candidates and provenance

The H in GRAPH is a concrete responsibility. It makes candidate explanations and proposed relationships portable
and inspectable, without ever turning them into established assertions. This document describes the candidate
contract as implemented (`src/graph_runtime/hypotheses.py`). It was written on 25 October 2026 from section 3 of
the [developer note](GRAPH_Developer_Note_for_Claude.md).

## The record

| Field | Rule |
| --- | --- |
| `kind` | always `hypothesis_candidate` |
| `epistemic_status` | always `unverified`; no operation changes it |
| `id` | `hc:sha256:<digest of the core fields>`, or an ID the consumer chooses (the `hc:sha256:` prefix is reserved) |
| `revision`, `previous` | 1 and null for a new candidate; each later revision names the previous revision's `record_digest` |
| `statement`, `scope` | non-empty text: what is proposed, and what it is about |
| `projection` | the identity of the projection the candidate was formed on |
| `producer` | `{"name", "version"}` of what produced it |
| `basis` | at least one reference `{"type": "node" \| "edge" \| "result" \| "external", "ref"}`; a result is named by its `result_id` |
| `dependencies` | references whose revision makes the candidate stale |
| `assumptions` | text, each one stated |
| `proposed_tests` | at least one `{"id", "description", "outcomes": [{"observation", "bearing"}]}` whose outcomes discriminate: one `supports` the candidate and one `opposes` it (`inconclusive` may be added), and no two describe the same observation |
| `proposed_relations` | `{"source", "target", "relation"}`, never traversable |
| `scores` | `{"value", "method", "method_version", "candidate_set", "direction": "higher_first" \| "lower_first", "interpretation": "ordering" \| "probability", "calibration"}` |
| `lifecycle` | `active` first; then at most one of `withdrawn` (with a reason) or `superseded` (with a reason and `superseded_by`), dated no earlier than the creation; every `at` is an ISO 8601 date or date-time of a real day |
| `assessments` | `{"id", "stance": "supporting" \| "opposing" \| "inconclusive", "evidence": [references], "conditions", "assessor": {"name", "version"}, "at", "note"}` |
| `extensions` | consumer data, not interpreted |
| `record_digest` | the digest of every other field |

The core fields never change after creation: statement, scope, projection, producer, basis, dependencies,
assumptions, proposed tests and proposed relations. A reformulation is a new candidate, which may supersede the
old one.

## Rules

- **Never an assertion.** A candidate is unverified at every revision. Supporting assessments do not change that,
  and GRAPH has no "promote hypothesis to fact" operation. A later measurement or established assertion is a
  separate scientific record with its own provenance.
- **Lifecycle is not truth.** `active`, `withdrawn` and `superseded` record what happened to the candidate. A
  withdrawal can reflect a change of scope or formulation; it does not establish that the candidate was false.
- **Immutable records, append-only history.** `withdraw`, `supersede`, `attach_assessment` and `add_score` each
  return a new revision. The new revision appends to the lifecycle, assessments or scores, keeps everything the
  previous revision held, and names its digest; the original record is unchanged. `verify_revisions(history)`
  detects a broken chain, a history that does not start at revision 1 (unless `from_start=False` says it is
  partial), a changed fixed field (the core fields, the kind, contract, epistemic status and extensions), and any
  dropped or rewritten item. A transport cannot lose opposing evidence or a withdrawal unnoticed.
- **Scores order, they do not measure.** An `ordering` score orders candidates within one method, method version and
  candidate set. `order_candidates` never ranks across sets or methods, lists unscored candidates apart, and leaves
  out inactive ones. A `probability` interpretation requires a calibration reference and a value in [0, 1]; whether
  the calibration holds is for the consuming application to assess.
- **Assessment is external.** A consumer attaches supporting, opposing or inconclusive evidence under declared
  conditions. GRAPH stores the assessment, keeps it in every later revision, and never weighs it.
- **Never traversed.** Projections refuse candidate records. A proposed relation cannot silently become evidence
  during traversal. A future combined view needs its own contract, one that keeps candidate identity in every edge
  and witness.

## Staleness

`staleness(candidate, projection=..., impact=...)` says whether a candidate must be re-examined. It asks one of two
questions:

- **The snapshot** (`projection` alone, the current projection). A different identity from the one the candidate
  was formed on makes it stale (`projection_changed`). The same identity means nothing it references inside the
  projection has changed. Missing node or edge references, and result references (which a projection does not
  contain), remain undecided even when the identity matches.
- **The dependencies** (`impact`, a `dependency_impact` result of the revised nodes; `projection`, when its identity
  is the impact's, so that GRAPH can check it holds a node). A referenced node that was revised (`input_revised`),
  or that the impact reaches (`input_affected`, with the witness), makes the candidate stale.

`stale` is three-valued, as GRAPH's answers are:

| `stale` | Meaning |
| --- | --- |
| `true` | a reason was found; re-examine the candidate |
| `false` | every node, edge and result reference was decided unaffected, for the question asked |
| `null` | something could not be decided; `undecided` says what and why |

A node is cleared only by a completed impact without `max_depth`, on a projection that holds it. An interrupted or
depth-bounded impact, an unknown or different projection, and an edge or result reference (node impact does not
follow them) all leave a reference undecided. External references are outside GRAPH's view, and are listed in
`outside`. Staleness is not falsity, and it changes nothing in the candidate.

## Transport

`to_dict()` and `to_json()` give the record as JSON. `from_dict()` and `from_json()` validate it, including its
digests, and refuse it with every problem listed (`CandidateError.problems`). The JSON Schema definition
`hypothesis_candidate` (`graph_runtime.json_schema("hypothesis_candidate")`) checks the same rules a schema can
express. Serialization preserves:

- the unverified status
- the lifecycle history
- the assumptions
- opposing evidence
- the proposed tests

Consumer extension object keys must be strings; transport never coerces keys and loses an entry. Finite integer
scores are kept exactly, including values beyond the float range. Invalid proposed-test bearings and malformed
JSON are reported as candidate problems. Lifecycle date-times retain all fractional digits: comparable date-times
(both with offsets or both without) are ordered by their full instant/local time. Mixed date/date-time or
offset/offset-free forms compare days, using the UTC day for an offset-bearing date-time. Calendar validity,
chronology and normalized observation uniqueness require runtime checks beyond the schema's shape checks.

`witness_refs` includes the source node of a zero-hop witness as well as its result identity.

## The synthetic example

[examples/synthetic_revision](../examples/synthetic_revision/README.md) records a candidate: revising an input might
change the order of two model predictions in a ranking.

- Its basis is the dependency-impact result and the ranking's witness.
- Its `0.8` priority is an arbitrary ordering value, not an 80 % probability.
- Its proposed recomputation is a plan, not a completed validation.

After a later change to the model, the candidate is reported stale, with the witnesses that say why. The example
exercises transport and provenance; it asserts no scientific result.
