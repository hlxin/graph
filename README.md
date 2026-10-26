# GRAPH

**Graph Runtime for Analysis, Provenance, Hypotheses**: reusable graph algorithms for scientific software.

GRAPH is a small library that gives scientific applications reliable graph execution and keeps one computational
contract across them:

- explicit inputs (an immutable projection and a normalized request)
- supported operations only, exact only
- bounded work
- reproducible results
- witnesses that can be checked against the input records

The applications (SCALE, STATES, RATES, Cathanor) keep their scientific definitions, domain rules, authoritative
records and workflows. GRAPH imports none of their vocabularies; each application reaches it through an adapter it
owns.

Status: **in development** (`0.1.0.dev0`, contract `graph-runtime/v0.2`). Nothing is published. The distribution
name `scale-graph-runtime` and the import name `graph_runtime` are proposals, not yet reserved or checked for
availability (the bare PyPI name `graph` belongs to another project).

## What v0 does

| Part | What it provides |
| --- | --- |
| Projection | An immutable, read-only graph view of the consumer's records: original node and edge IDs kept, parallel edges kept as distinct records, integer remapping inside, optional type and relation hierarchies (a type matches its subtypes' nodes, a relation covers its specializations), and an identity digest bound to the consumer's source, schema and policy |
| Operations | Exact **reachability**, **typed path** (a finite relation sequence, with optional node types per position), **shortest path** (explicit, finite, non-negative costs, with exact integer and rational arithmetic), **dependency impact**, **pattern** (tree patterns of variables and steps, optional steps included: rows without dead ends, counted exactly without listing them) and **support** (what loses all or some of its support when nodes fail, under AND/OR rules, as a least fixpoint), each with structural witnesses |
| Limits | Expansion budget and output limit (part of the request), deadline and cancellation (execution controls). A limit that prevents completion makes the result `interrupted`; a full answer that fits can complete. An interrupted result never certifies a negative |
| Results | One contract: `completed`, `interrupted`, `unsupported`, `failed` or `stale_snapshot`, with the normalized request, the projection identity, the answer, statistics, limitations and a reproducible `result_id` |
| Hypotheses | Candidate records that are always `unverified`, with discriminating proposed tests, calibrated or ordering-only scores, append-only lifecycle and assessment history, and staleness through dependency impact. They are never traversed |
| Checks | `verify_result` re-checks every witness against the input records, and the closure of every complete reachability and impact answer; JSON Schemas describe every record; a language-neutral conformance suite any backend must pass |
| Certificates | On request, a typed or shortest path answer proves itself against the records without a second search: closed states or a closure for a negative, potentials for an optimum or an interrupted search's lower bound |
| Revalidation | Whether a cached result still holds on an edited projection, decided from its answer and certificate (`holds`, `fails` with the edit that refutes it, or `unknown`); with one diff of the projections, in time proportional to the edit (0.1 ms per cached result on a 500,000-edge graph, against about 100 ms to rerun it) |
| Retrieval (optional, numpy) | `graph_runtime.retrieval`: exact keyword (BM25), hashed-vector, hybrid and rank-fusion search over text items that name records, so a search can choose where a traversal starts and a traversal can restrict a search; indexes with identities, saved with digests ([docs/RETRIEVAL.md](docs/RETRIEVAL.md)) |

## Quick start

```python
from graph_runtime import Projection, execute

p = Projection.build(
    nodes=[{"id": "test:1", "types": ["Test"]}, {"id": "sample:1", "types": ["Sample"]}, "paper:1"],
    edges=[{"id": "link:1", "source": "test:1", "target": "sample:1", "relation": "USES"},
           {"id": "link:2", "source": "sample:1", "target": "paper:1", "relation": "REPORTED_IN"}],
    bindings={"source": "my-document@sha256:…", "schema": "my-schema 1.2", "policy": "public"},
)
result = execute(p, p.request("reachability", sources=["test:1"],
                              scope=[("USES", "forward"), ("REPORTED_IN", "forward")]))
result.status              # 'completed'
result.reachable("paper:1")   # True; False only in a complete answer; None when unknown
result.path_to("paper:1")     # [{'edge': 'link:1', ...}, {'edge': 'link:2', ...}]: original IDs
result.to_json()              # the contract's JSON, with the normalized request and result_id
```

[examples/synthetic_revision](examples/synthetic_revision/README.md) walks through a revision, its dependency
impact and a hypothesis candidate about it.

## Development

```bash
python3 -m pytest                      # the runtime's tests (pytest; networkx, jsonschema and numpy for the oracles, schemas and retrieval)
uv run --extra dev pytest              # the same, in an isolated environment
python examples/synthetic_revision/run.py --check
```

The SCALE integration tests run in SCALE's environment and only read SCALE
([docs/SCALE_INTEGRATION.md](docs/SCALE_INTEGRATION.md)):

```bash
PYTHONDONTWRITEBYTECODE=1 ../scale/.venv/bin/python -m pytest integrations/scale -p no:cacheprovider
```

The STATES integration tests run in STATES's environment in the same way, and only read STATES
([docs/STATES_INTEGRATION.md](docs/STATES_INTEGRATION.md)):

```bash
PYTHONDONTWRITEBYTECODE=1 ../STATES/.venv/bin/python -m pytest integrations/states -p no:cacheprovider
```

`benchmarks/bench.py` measures every phase of a request on synthetic graphs. `benchmarks/consumers.py` measures real
SCALE and STATES requests with both engines, in each application's environment.

## Layout

| Path | What it holds |
| --- | --- |
| `src/graph_runtime/` | The runtime (standard library only): `projection`, `request`, `engine`, `result`, `verify`, `hypotheses`, `schemas/`, `conformance/`; and `retrieval`, which alone needs numpy |
| `tests/` | Unit, scenario, oracle-parity (independent oracle and NetworkX), limit, schema and conformance tests |
| `integrations/scale/` | SCALE's adapter (application-owned, staged here) and its parity tests against SCALE's own engine |
| `integrations/states/` | STATES's adapter (application-owned, staged here) and its parity tests against STATES's own context extraction |
| `examples/` | The synthetic revision example |
| `docs/` | The developer note, the contract, the hypothesis contract, the implementation plan and the SCALE integration |

## Documentation

- [docs/GRAPH_Developer_Note_for_Claude.md](docs/GRAPH_Developer_Note_for_Claude.md): the decision, the boundary and
  the requirements.
- [docs/API_CONTRACT.md](docs/API_CONTRACT.md): the contract as implemented.
- [docs/HYPOTHESES_AND_PROVENANCE.md](docs/HYPOTHESES_AND_PROVENANCE.md): hypothesis candidates.
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): the six work items, their status and the evidence for
  each acceptance requirement.
- [docs/RETRIEVAL.md](docs/RETRIEVAL.md): search, then traverse.
- [docs/SCALE_INTEGRATION.md](docs/SCALE_INTEGRATION.md): the SCALE baseline, the adapter and its parity results.
- [docs/STATES_INTEGRATION.md](docs/STATES_INTEGRATION.md): the STATES baseline, the adapter and its parity results.
