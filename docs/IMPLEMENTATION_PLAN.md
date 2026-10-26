# Implementation plan and status

The developer note's work sequence ([GRAPH_Developer_Note_for_Claude.md](GRAPH_Developer_Note_for_Claude.md), section
5) has six work items. This file records where each stands, and the evidence for each acceptance requirement of
section 6. Status as of 25 October 2026, contract `graph-runtime/v0.2`, runtime `0.1.0.dev0`.

## Work items

| # | Work item | Status | What exists |
| --- | --- | --- | --- |
| 1 | Record the baseline and agree on the small contract | Done; **the contract awaits review** | [API_CONTRACT.md](API_CONTRACT.md) and the JSON Schema; the SCALE baseline in [SCALE_INTEGRATION.md](SCALE_INTEGRATION.md) |
| 2 | The immutable view and four exact operations with structural witnesses | Done, and extended from SCALE (below) | `projection.py`, `request.py`, `engine.py`, `result.py`, `verify.py`; unit, scenario, oracle and limit tests; 62 conformance cases. Contract v0.2 adds type and relation hierarchies and a fifth operation, `pattern` |
| 3 | Candidate-hypothesis validation and transport, without source mutation | Done | `hypotheses.py`, [HYPOTHESES_AND_PROVENANCE.md](HYPOTHESES_AND_PROVENANCE.md), the synthetic example |
| 4 | The first real consumer adapter and scientific parity fixtures | First cut | SCALE's adapter, staged in `integrations/scale/`, with two read-only workflows and parity against SCALE's own engine; it still has to go to SCALE as a pull request |
| 5 | Measure the whole request; evaluate one alternative backend only if needed | Measured and optimized | every result's `timing` (bind, search, witness, result) and `benchmarks/bench.py` (build, serialization, reading back, verifying, workload identity, versions, raw results, parity); the hot spots found are fixed (see Measurements); no alternative backend is needed at the sizes measured |
| 6 | Prepare the extraction path; exercise a real second consumer | Second consumer exercised | STATES's adapter, staged in `integrations/states/`: context extraction as bounded reachability over the lifted periodic substrate, with parity against STATES's own `extract_context` ([STATES_INTEGRATION.md](STATES_INTEGRATION.md)); the private repository `hlxin/graph` holds the runtime; no release yet |

The note's criterion for a separately versioned package is a real second workflow that validates the same
interface. STATES now provides one. Its context extraction uses only what v0.1 already had, unchanged in v0.2, with
no new operation and no STATES vocabulary in the runtime:

- a projection of its lifted substrate graph
- reachability with `max_depth` and `max_results`
- witnesses
- stale bindings

On 70 records and 760 contexts it matches STATES's own code. What remains for a release is the other half of the
criterion: a reason for independent releases. Both adapters are still staged here, and nothing is published. The
runtime keeps its boundary so that it can stay small: standard library only (the optional retrieval module aside),
and no application vocabulary.

## From SCALE

SCALE is to use GRAPH, and so are other applications. What SCALE's own engines do that is not catalysis is moved
into GRAPH in generic form, and each move is checked against SCALE's engine on SCALE's records:

| SCALE | GRAPH | Evidence |
| --- | --- | --- |
| Label ancestors and edge-type specializations (`pgraph_query`) | Type and relation hierarchies in projections (`type_parents`, `relation_parents`), contract v0.2 | 5 conformance cases; an independent expansion of the hierarchies on 250 random graphs; SCALE's chains and plans |
| Plans of edge steps: chains, trees, optional steps (`pgraph_query`) | The `pattern` operation: semi-join reduction with no dead ends, rows in canonical order, exact totals without listing rows | 8 conformance cases; a brute-force enumerator on 400 random patterns; 39 SCALE question–document pairs, row for row (203 rows) |
| Evidence search: BM25, hashed int8 vectors, hybrid and rank fusion (`passages`) | `graph_runtime.retrieval` (optional, numpy), with tokenizer settings, index identities and a `records` filter | 50 tests against the plain definitions; 1,980 SCALE searches (130,173 hits) hit for hit; SCALE's text stages; [RETRIEVAL.md](RETRIEVAL.md) |
| What rests on a retraction, all or in part (`lifecycle`) | Dependency impact (done); the "all or some" support analysis is next | 18,951 records on injected retractions |
| The tool surface (`api`, `mcp_server`) | Next: a command line and an MCP server for GRAPH's operations | |

## Acceptance evidence

| Requirement (note, section 6) | Evidence | Where |
| --- | --- | --- |
| Exact results agree with independent small-graph checks and the consumer's reference behaviour | Every operation is checked on random graphs against an independent oracle (about 4,700 executions, with and without random limits, and 400 random patterns against a brute-force enumerator), against NetworkX (reachability and integer-cost shortest paths), and against 62 hand-written conformance cases. SCALE parity covers 14 competency questions, 1,654 generated chains on 31 documents, 39 plans of any shape row for row, 18,951 records resting on injected retractions in 134 documents, and 1,980 evidence searches hit for hit. STATES parity covers 760 contexts (38,033 atom images, 243,869 relations and ports, 679 limit checks) on 70 records | `tests/test_oracle.py`, `tests/test_conformance.py`, `integrations/scale/`, `integrations/states/` |
| Witnesses survive parallel edges, cycles, reverse traversal and repeated nodes in typed patterns | Named scenarios for each (`e4`/`e5` twins, the `a-b-c-d-a` cycle, reverse hops, a witness `e1, e1, e1` that revisits nodes). Random graphs include parallel edges and self-loops, and `verify_result` re-checks every witness against the records | `tests/test_operations.py`, `tests/test_oracle.py` |
| Large integer costs keep their order; missing, negative and non-finite costs are refused | Costs of `2**64 + 1` versus `2**64 + 2`, which a float sum cannot tell apart; floats added as exact rationals; each refusal code tested (`missing_cost`, `negative_cost` as unsupported, `non_finite_cost`, `invalid_cost`) | `tests/test_operations.py`, `tests/test_requests.py` |
| Output limits, cancellation, deadlines and exhausted budgets cannot certify a negative | Each limit, with exact-fit and one-short cases. Random limits on every operation: an interrupted answer only lists what the full answer holds, and never says `found: false`. The JSON Schema and the conformance suite refuse a result that claims otherwise | `tests/test_limits.py`, `tests/test_oracle.py`, `tests/test_schemas.py`, `tests/test_conformance.py` |
| A projection is reused only while its source, schema/context and policy are unchanged | The identity covers the bindings; `stale_snapshot` (`projection_mismatch`, `bindings_changed`). The SCALE adapter binds the document digest, the pinned schema's spec digest and the access level | `tests/test_results.py`, `integrations/scale/test_scale_parity.py` |
| Candidate serialization keeps the unverified status, lifecycle history, assumptions, opposing evidence and proposed tests | JSON round trips, record digests, and `verify_revisions` detecting a dropped opposing assessment or a rewritten core field. Schema validation at every revision | `tests/test_hypotheses.py`, `tests/test_schemas.py` |
| Source records are unchanged by execution and candidate creation | Deep-copy comparisons after building, running every operation and creating and revising candidates. Caller objects are copied, never kept. The SCALE checkout's working tree is clean after the integration tests | `tests/test_projection.py`, `tests/test_results.py`, `integrations/scale/` |
| Performance reports include input preparation and witness work, workload identity, versions, raw results and correctness parity | `benchmarks/bench.py` records projection build, bind, search, witness, result and JSON time per request; the workload's generator, parameters, seed and projection identity; versions; raw results; and parity with NetworkX | `benchmarks/` |

An independent review read the runtime cold on 25 October 2026 and fuzzed it against its own oracles (about 50,000
cases, with limits, cancellation and deadlines). It found no wrong answer in the four operations and no identity
collision. It did find defects at the edges, each now fixed with a regression test (`tests/test_review_regressions.py`
and `tests/test_hypotheses.py`):

- `staleness` could report "not stale" for references it cannot decide; it is now three-valued.
- Malformed requests could make `execute` raise.
- `Result.from_dict` trusted `complete` and `found`.
- Float costs beyond the floats crashed.
- There were smaller validation gaps: duplicate observations, impossible or out-of-order dates, deletable
  attributes, partial histories, and `verify_result` raising on malformed results.

A passing suite is not proof that a production service or a scientific model is correct. The SCALE parity tests
show that the adapter and the runtime reproduce what SCALE's engine and lifecycle compute, on SCALE's documents.

## Measurements

These come from `benchmarks/bench.py` on one Apple-silicon laptop (Python 3.13.5, 25 October 2026). Use them to
compare runs on that machine only. The workload is a uniform random multigraph with 4 relations, and every answer
agreed with NetworkX. Times are in seconds.

The first measurements (`v0`) showed that making the result and serializing it cost as much as the search. Binding a
per-edge cost table cost more than the search it served:

| Size | Build | Reachability: search, witness, result, JSON | Shortest path: bind, search |
| --- | --- | --- | --- |
| 1,000 nodes, 5,000 edges | 0.017 | 0.0013, 0.0006, 0.0027, 0.0026 | 0.006, 0.0015 |
| 10,000 nodes, 50,000 edges | 0.19 | 0.012, 0.005, 0.027, 0.032 | 0.071, 0.020 |
| 100,000 nodes, 500,000 edges | 2.8 | 0.40, 0.09, 0.42, 0.41 | 1.0, 0.17 |

The `v0.1` revision made reading a result back much costlier: it checks every field against the schema. A
100,000-node answer took about 10 seconds to read or verify, 120 times as long as making it. The current runtime fixes
both:

- **Encoding.** A result encodes each top-level field once, which gives both its `result_id` and its JSON; it reuses
  the request's own encoding and freezes its views only when they are read.
- **Projection identity.** Its lines are written with JSON's own string encoder, and plain ASCII text is checked
  without encoding it.
- **Cost tables.** Normalizing one no longer sorts it, or builds a message for every entry.
- **Schema checks.** The checks compile the bundled schema into closures once, with predicate modes that stop at the
  first problem.
- **Verification.** `verify_result` checks each witness hop once.

Every result id, identity and digest is unchanged: the example's committed outputs still match byte for byte.

| Size | Build | Reachability: search, witness, result, JSON, read back, verify | Shortest path: bind, search, read back |
| --- | --- | --- | --- |
| 1,000 nodes, 5,000 edges | 0.007 | 0.0008, 0.0004, 0.0007, 0.0000, 0.0074, 0.0066 | 0.0016, 0.0011, 0.0056 |
| 10,000 nodes, 50,000 edges | 0.078 | 0.0085, 0.0038, 0.0075, 0.0002, 0.066, 0.074 | 0.020, 0.017, 0.060 |
| 100,000 nodes, 500,000 edges | 1.19 | 0.20, 0.079, 0.079, 0.0014, 0.84, 0.93 | 0.31, 0.10, 0.78 |

The search now dominates making an answer. Reading an answer back is what costs most, because it checks every
field, and it is still under a second at 100,000 nodes. No alternative backend is needed for these sizes.

### Real consumer requests

`benchmarks/consumers.py` measures real requests with both engines: the application's own code and GRAPH through
the staged adapter, on the application's own records. It takes the best of three runs, and checks that both give the
same answer: all 73 requests agreed. Mean times per request:

| Workflow | Application's own code | GRAPH: building the projection | GRAPH: the query |
| --- | --- | --- | --- |
| SCALE competency-question chains (29, on 25 documents) | 0.51 ms | 0.41 ms | 0.48 ms |
| SCALE records resting on retractions (2) | 0.04 ms | 0.34 ms (both together) | |
| STATES contexts, 1 to 3 shells (42, on 14 records) | 15.3 ms | 52 ms (lifting the periodic substrate) | 0.12 ms |

For SCALE, GRAPH is on par once the adapter computes SCALE's schema digest once per schema object. Before that,
every call recomputed it, about 7 ms on average and up to 21 ms. For STATES, the query is fast and the lift is the cost.
GRAPH needs an explicit, finite projection, so the adapter lifts a whole box of periodic images: (2·shells + 1)
images along every periodic axis. STATES's own search walks only the images it reaches. The lift ranges from 2 ms
(450 nodes, against 2.6 ms natively) to 724 ms (58,320 nodes and 300,617 edges for a context of 140 images, against
26.5 ms). A lift reused for many contexts, such as every shell count, every root set, or other operations on the same
record, answers each in about 0.12 ms.

**A proposed extension for review, not made.** A *periodic projection* would hold the finite records of one cell
plus each edge's image shift, and expand images only as a traversal reaches them. This is the generic shape of any
periodic or unrolled graph, not a STATES vocabulary. It would keep the inputs explicit and finite, make a one-off
context cost what STATES's own search costs, and keep GRAPH's witnesses, limits and identity. It changes the
contract, so it waits for a decision.

## Next

1. Review the contract (work item 1) and settle anything it should change before a consumer depends on it.
2. Take the rest of SCALE's generic machinery: the "all or some" support analysis, and a command line and MCP server.
3. Certificates: answers that carry a proof of a negative or of optimality, checked without rerunning the search,
   and cached answers revalidated on an edited projection instead of recomputed.
4. Propose the SCALE adapter to SCALE as a pull request, under SCALE's process.
5. Propose the STATES adapter to STATES, under its own process.
6. Decide on the periodic projection proposed under Real consumer requests.
7. A third workflow, RATES (its site graphs), when one needs an operation GRAPH has.
