# Implementation plan and status

The developer note's work sequence ([GRAPH_Developer_Note_for_Claude.md](GRAPH_Developer_Note_for_Claude.md), section
5) has six work items. This file records where each stands, and the evidence for each acceptance requirement of
section 6. Status as of 25 October 2026, contract `graph-runtime/v0`, runtime `0.1.0.dev0`.

## Work items

| # | Work item | Status | What exists |
| --- | --- | --- | --- |
| 1 | Record the baseline and agree on the small contract | Done; **the contract awaits review** | [API_CONTRACT.md](API_CONTRACT.md) and the JSON Schema; the SCALE baseline in [SCALE_INTEGRATION.md](SCALE_INTEGRATION.md) |
| 2 | The immutable view and four exact operations with structural witnesses | Done | `projection.py`, `request.py`, `engine.py`, `result.py`, `verify.py`; unit, scenario, oracle and limit tests; 34 conformance cases |
| 3 | Candidate-hypothesis validation and transport, without source mutation | Done | `hypotheses.py`, [HYPOTHESES_AND_PROVENANCE.md](HYPOTHESES_AND_PROVENANCE.md), the synthetic example |
| 4 | The first real consumer adapter and scientific parity fixtures | First cut | SCALE's adapter, staged in `integrations/scale/`, with two read-only workflows and parity against SCALE's own engine; it still has to go to SCALE as a pull request |
| 5 | Measure the whole request; evaluate one alternative backend only if needed | Started | every result's `timing` (bind, search, witness, result) and `benchmarks/bench.py` (build, serialization, workload identity, versions, raw results, parity); no alternative backend yet, and none is needed so far |
| 6 | Prepare the extraction path; exercise a real second consumer | Not started | the private repository `hlxin/graph` holds the runtime; no second consumer (STATES, RATES or Cathanor) has an adapter yet |

The note's criterion for a separately versioned package is a real second workflow that validates the same
interface. That criterion is not met yet: SCALE is the only consumer so far, and the synthetic example does not
count. The separate repository exists by decision, and the runtime keeps its boundary so that it can stay small:
standard library only, and no application vocabulary.

## Acceptance evidence

| Requirement (note, section 6) | Evidence | Where |
| --- | --- | --- |
| Exact results agree with independent small-graph checks and the consumer's reference behaviour | Every operation is checked on random graphs against an independent oracle (about 4,700 executions, with and without random limits), against NetworkX (reachability and integer-cost shortest paths), and against 34 hand-written conformance cases. SCALE parity covers 14 competency questions, 1,654 generated chains on 31 documents, and 18,951 records resting on injected retractions in 134 documents | `tests/test_oracle.py`, `tests/test_conformance.py`, `integrations/scale/` |
| Witnesses survive parallel edges, cycles, reverse traversal and repeated nodes in typed patterns | Named scenarios for each (`e4`/`e5` twins, the `a-b-c-d-a` cycle, reverse hops, a witness `e1, e1, e1` that revisits nodes). Random graphs include parallel edges and self-loops, and `verify_result` re-checks every witness against the records | `tests/test_operations.py`, `tests/test_oracle.py` |
| Large integer costs keep their order; missing, negative and non-finite costs are refused | Costs of `2**64 + 1` versus `2**64 + 2`, which a float sum cannot tell apart; floats added as exact rationals; each refusal code tested (`missing_cost`, `negative_cost` as unsupported, `non_finite_cost`, `invalid_cost`) | `tests/test_operations.py`, `tests/test_requests.py` |
| Output limits, cancellation, deadlines and exhausted budgets cannot certify a negative | Each limit, with exact-fit and one-short cases. Random limits on every operation: an interrupted answer only lists what the full answer holds, and never says `found: false`. The JSON Schema and the conformance suite refuse a result that claims otherwise | `tests/test_limits.py`, `tests/test_oracle.py`, `tests/test_schemas.py`, `tests/test_conformance.py` |
| A projection is reused only while its source, schema/context and policy are unchanged | The identity covers the bindings; `stale_snapshot` (`projection_mismatch`, `bindings_changed`). The SCALE adapter binds the document digest, the pinned schema's spec digest and the access level | `tests/test_results.py`, `integrations/scale/test_scale_parity.py` |
| Candidate serialization keeps the unverified status, lifecycle history, assumptions, opposing evidence and proposed tests | JSON round trips, record digests, and `verify_revisions` detecting a dropped opposing assessment or a rewritten core field. Schema validation at every revision | `tests/test_hypotheses.py`, `tests/test_schemas.py` |
| Source records are unchanged by execution and candidate creation | Deep-copy comparisons after building, running every operation and creating and revising candidates. Caller objects are copied, never kept. The SCALE checkout's working tree is clean after the integration tests | `tests/test_projection.py`, `tests/test_results.py`, `integrations/scale/` |
| Performance reports include input preparation and witness work, workload identity, versions, raw results and correctness parity | `benchmarks/bench.py` records projection build, bind, search, witness, result and JSON time per request; the workload's generator, parameters, seed and projection identity; versions; raw results; and parity with NetworkX | `benchmarks/` |

A passing suite is not proof that a production service or a scientific model is correct. The SCALE parity tests
show that the adapter and the runtime reproduce what SCALE's engine and lifecycle compute, on SCALE's documents.

## First measurements

These come from `benchmarks/bench.py` on one Apple-silicon laptop (Python 3.13.5, 25 October 2026). Use them to
compare runs on that machine only. The workload is a uniform random multigraph with 4 relations, and every answer
agreed with NetworkX.

| Size | Build | Reachability: search, witness, result, JSON (s) | Shortest path: bind, search (s) |
| --- | --- | --- | --- |
| 1,000 nodes, 5,000 edges | 0.017 s | 0.0013, 0.0006, 0.0027, 0.0026 | 0.006, 0.0015 |
| 10,000 nodes, 50,000 edges | 0.19 s | 0.012, 0.005, 0.027, 0.032 | 0.071, 0.020 |
| 100,000 nodes, 500,000 edges | 2.8 s | 0.40, 0.09, 0.42, 0.41 | 1.0, 0.17 |

Making the result (freezing and digesting it) and serializing it cost as much as the search itself. So does binding
a per-edge cost table. These are the first places to optimize, before any alternative backend is considered.

## Next

1. Review the contract (work item 1) and settle anything it should change before a consumer depends on it.
2. Propose the SCALE adapter to SCALE as a pull request, under SCALE's process.
3. Profile and reduce result-making and per-edge cost binding (work item 5): lighter freezing, streaming digests.
4. Build a second consumer's adapter, from STATES or RATES, on a real workflow (work item 6).
