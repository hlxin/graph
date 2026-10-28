# Validation

How GRAPH's answers are checked, and where. Every check runs with `python -m pytest` unless it says otherwise.

| What must hold | How it is checked | Where |
| --- | --- | --- |
| Exact results agree with independent computations | Every operation runs on random small graphs (parallel edges, self-loops, cycles, non-ASCII IDs), with and without random limits, against oracles that share no code with the engine: plain breadth-first and least-cost searches, a brute-force pattern enumerator, threshold sweeps, and NetworkX where it has the same algorithm (reachability, shortest paths, flows, matchings, dominators, components, spanning forests, isomorphism and others) | `tests/oracle.py`, `tests/test_oracle.py` and each operation's test file |
| Every backend gives the same answers | A language-neutral conformance suite of 217 hand-written cases, whose expectations are worked out by hand from the contract and the graphs, never copied from the engine | `src/graph_runtime/conformance/cases.json`, `tests/test_conformance.py`, the `graph_conformance` tool |
| Witnesses survive parallel edges, cycles, reverse traversal and repeated nodes | Named scenarios for each, random graphs with parallel edges and self-loops, and `verify_result`, which re-checks every witness hop against the records | `tests/test_operations.py`, `tests/test_oracle.py` |
| A certificate never passes where it is wrong | Answers made without one edge are checked against the records with it: where the edge changes the answer, the check refuses it. Forged certificates (a dropped state, a lowered potential, a closure missing a node, a reordered kernel) are refused with the hop or entry that breaks them | each operation's test file, `tests/test_support_certificate.py`, `tests/test_k_paths_certificate.py` |
| A revalidated `holds` means a new search would agree | Random edits (edges added, removed and moved, nodes removed and retyped): every `holds` matches a fresh run and checks out against the new records, every `fails` is refuted, and the diff-based and full checks agree | `tests/test_revalidate.py`, `tests/test_revalidate_checked.py` |
| Limits never certify a negative | Each limit with exact-fit and one-short cases; random limits on every operation: an interrupted answer lists only what the full answer holds and never says `found: false`. The JSON Schema and the conformance suite refuse a result that claims otherwise | `tests/test_limits.py`, `tests/test_oracle.py`, `tests/test_schemas.py` |
| Large integer costs keep their order | Costs of `2**64 + 1` against `2**64 + 2`, which a float sum cannot tell apart; floats added as exact rationals; each refusal (`missing_cost`, `negative_cost` where unsupported, `non_finite_cost`, `invalid_cost`) | `tests/test_operations.py`, `tests/test_requests.py` |
| A projection is reused only while its bindings match | The identity covers the bindings; a mismatch is `stale_snapshot` (`projection_mismatch`, `bindings_changed`) | `tests/test_results.py` |
| Hypothesis candidates keep their status and history | JSON round trips, record digests, and `verify_revisions` detecting a dropped opposing assessment or a rewritten core field; schema validation at every revision | `tests/test_hypotheses.py`, `tests/test_schemas.py` |
| Source records are never changed | Deep-copy comparisons after building, running every operation, and creating and revising candidates | `tests/test_projection.py`, `tests/test_results.py` |
| Answers do not change unnoticed | The `result_id` of every result of a fixed random workload is recorded; any change in an answer, a witness choice, a certificate or a counter shows up. A deliberate change regenerates the file and says why in its commit | `tests/test_golden.py`, `tests/golden_results.json` |
| Work stays within its bounds | Adversarial shapes (long chains, deep hierarchies, many relations or seeds, huge loops) are run against budgets, deadlines and cancellation, and against time bounds that catch quadratic work | each operation's test file |

## Consumer parity

The SCALE and STATES adapters are checked against each application's own engine, on its own records, in that
application's environment (they import it, and read its checkout without writing to it):

- SCALE: competency-question chains and plans of any tree shape row for row, records resting on retractions (all or
  in part), and evidence search hit for hit ([SCALE_INTEGRATION.md](SCALE_INTEGRATION.md)); and every generated one-
  and two-step plan on the methanol-synthesis graphs, row for row ([PERFORMANCE.md](PERFORMANCE.md)).
- STATES: context extraction, image for image, with relations, ports and limits ([STATES_INTEGRATION.md](STATES_INTEGRATION.md)).

```bash
PYTHONDONTWRITEBYTECODE=1 ../scale/.venv/bin/python -m pytest integrations/scale -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 ../STATES/.venv/bin/python -m pytest integrations/states -p no:cacheprovider
```

## What this does not show

A passing suite is not proof that a production service or a scientific model is correct. The parity tests show that
the adapters and the runtime reproduce what each application's own code computes, on that application's records.
