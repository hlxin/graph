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
| Projection | An immutable, read-only graph view of the consumer's records: original node and edge IDs kept, parallel edges kept as distinct records, integer remapping inside, optional type and relation hierarchies (a type matches its subtypes' nodes, a relation covers its specializations), and an identity digest bound to the consumer's source, schema and policy. A **periodic projection** holds one cell of an infinite periodic graph (a crystal, a slab), its edges with image shifts; reachability and impact search its images (`atom@i,j,k`) without lifting a box of them, and **periodicity** gives each component's translation lattice: rank 0 (molecule) to 3 (framework), a canonical basis with witnesses, and its degree of interpenetration |
| Operations | Exact **elementary flux modes** (a reaction network's minimal steady-state pathways, by exact double description, each proven elementary), **canonical forms** and **isomorphism** (one name for every copy of a structure, by individualization and refinement: deduplicating molecules and motifs; isomorphisms proven by their bijections), **Datalog** (positive, recursive rules over the records, `node`, `type` and `edge`, evaluated semi-naively to the least model: lineage, provenance closures, any fixpoint of joins; every fact with its derivation), **reachability** (and **between pairs**: many source-target questions from a 2-hop index kept on the projection, each reachable pair with a simple path), **typed path** (a finite relation sequence, with optional node types per position), **k shortest paths** (the first k simple paths in a strict order, by Yen's algorithm: alternative reaction pathways), **shortest path** (explicit, finite costs with exact integer and rational arithmetic: Dijkstra's algorithm when none is negative, else Bellman-Ford-Moore with subtree disassembly between the sources and the targets, reporting a negative cycle on the way, such as an exergonic loop in a reaction network, with three walks that prove it), **dependency impact**, **pattern** (patterns of variables and steps, optional steps included: trees without dead ends, counted exactly without listing them, and cycles, such as corroboration diamonds or triangles, listed by a worst-case optimal join and counted without listing by variable elimination along a tree decomposition; or injective, distinct nodes and edges, for substructure search: motifs, sites, rings), **support** (what loses all or some of its support when nodes fail, under AND/OR rules, as a least fixpoint), **bottleneck path** (the path whose weakest edge is strongest, or whose highest edge is lowest: a reaction pathway's fastest rate-limiting step, or its lowest highest barrier) **regular path** (walks whose hops match a regular expression of steps, such as `DERIVED_FROM*/SUPPORTED_BY[Measurement]/IN`: one with the fewest hops, or every node reached) **personalized PageRank** (a relevance ranking around chosen nodes, as graph-based retrieval uses, in which every node's score is an interval proven to hold it, the top of the ranking certified where intervals separate) **derivation** (the best derivation of each target in an AND/OR graph, where a reaction needs all its reactants and a species any reaction that makes it: the cheapest route from given feedstocks, the one whose highest barrier is lowest, or whose slowest step is fastest), **components** (weak, or strong with each flagged initial, terminal and cyclic: fragments, feedback loops, circular derivations, the classes a walk can never leave) **biconnectivity** (articulation points with the pieces each leaves, bridges and blocks: where a single failure splits a structure), **max flow** (a maximum flow and a minimum cut that prove each other: throughput, bottlenecks, and how many edge- or node-disjoint lines join two sets) **min-cost flow** (a given throughput, or the most, at least cost, proven by residual potentials and a cut) **matching** (as many distinct pairings as possible, with the vertex cover that proves no more exist; with costs, the cheapest of the largest: the assignment problem, proven by potentials) **dominators** (the nodes every route from the sources must pass through: unavoidable intermediates, single points of failure) **incidence** (the exact rank and kernels of the matrix of edge coefficients between two node sets: a reaction network's independent reactions and conservation laws, a Petri net's invariants) **critical path** (the canonical topological order, makespan, critical path and every node's slack, or a cycle: schedules, workflows, build systems), **d-separation** (whether a causal graph makes two sets independent given a third, with an active trail or a Bayes-ball certificate), **spanning forest** (the minimum or maximum spanning forest, unique by edge ID among ties: a network's backbone, single-linkage clusters) and **cycle basis** (a minimum cycle basis by de Pina's algorithm: a molecule's smallest set of smallest rings, a network's independent loops), each with structural witnesses |
| Limits | Expansion budget and output limit (part of the request), deadline and cancellation (execution controls). A limit that prevents completion makes the result `interrupted`; a full answer that fits can complete. An interrupted result never certifies a negative |
| Results | One contract: `completed`, `interrupted`, `unsupported`, `failed` or `stale_snapshot`, with the normalized request, the projection identity, the answer, statistics, limitations and a reproducible `result_id` |
| Hypotheses | Candidate records that are always `unverified`, with discriminating proposed tests, calibrated or ordering-only scores, append-only lifecycle and assessment history, and staleness through dependency impact. They are never traversed |
| Checks | `verify_result` re-checks every witness against the input records, and the closure of every complete reachability and impact answer; JSON Schemas describe every record; a language-neutral conformance suite any backend must pass |
| Certificates | On request, a typed, shortest, bottleneck or regular path answer proves itself against the records without a second search: closed states or a closure for a negative, potentials for an optimum or an interrupted search's lower bound (feasible potentials of any sign, with the dead ends, when a cost is negative), a threshold for a best bottleneck, closed product states for a regular path's fewest hops, negative or reached set, values no rule improves on for a derivation's best values and underivable targets, estimates whose exact residuals prove a ranking's intervals, spanning trees and a closed, ordered partition for components, a depth-first forest for articulation points, bridges and blocks, a minimum cut's source side for a maximum flow, a vertex cover for a maximum matching, a low-high order for a dominator tree, a nonsingular minor and a kernel basis for a rank, a topological order with earliest times for a critical path, Bayes-ball states for a d-separation, both least fixpoints' ranks for a support analysis; a spanning forest proves itself by the cycle property; de Pina's witnesses prove a cycle basis minimum; a source's closure proves it reaches none of the targets outside it; a Datalog answer's whole least model, closed under every rule, proves its rows are all; other counts or color refinement histograms prove two graphs not isomorphic; a nonsingular minor of its support proves a flux mode elementary; potentials and the walks leaving the listed paths' prefixes prove a k shortest paths ranking |
| Revalidation | Whether a cached result still holds on an edited projection, decided from its answer and certificate (`holds`, `fails` with the edit that refutes it, or `unknown`); components, biconnectivity, flows, matchings, dominators, incidence, critical paths, d-separation, k shortest paths and support by checking their certificates against the new records; with one diff of the projections, in time proportional to the edit (0.1 ms per cached result on a 500,000-edge graph, against about 100 ms to rerun it) |
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

## Tools for agents and other programs

The same tools are reachable as Python (`graph_runtime.tools.GraphToolkit().call(name, arguments)`), on the command
line and over the Model Context Protocol. Each tool is plain JSON Schema, takes JSON and returns JSON, reports a
problem as data, and writes nothing. A session keeps what it built and what it answered: a request run again on the
same projection is answered from the session (results are reproducible), and an agent can read a summary first and
fetch the rest of a result only when it needs it, so large answers do not fill its context.

| Tool | What it does |
| --- | --- |
| `graph_status` | versions, operations, whether text search is available, what the session holds |
| `graph_describe` | the contract's JSON Schema for requests, results, projections and candidates |
| `graph_projection` | build a projection from records (inline or a file) and keep it under its identity |
| `graph_execute` | run a request on a projection the session holds, or one given inline; `view: summary` returns its status, counts and nearest items instead of the whole answer |
| `graph_result` | a result the session holds, by `result_id`: whole, summarized, a slice of one of its lists, or one node's witness |
| `graph_batch` | several requests on one projection in one call, each summarized (or whole) |
| `graph_verify` | check a result against a projection's records |
| `graph_revalidate` | which cached results still hold after the records changed |
| `graph_search` | exact text search over items that name records, with the nodes they name (needs numpy) |
| `graph_conformance` | the conformance suite against this runtime |

```bash
graph-runtime call graph_execute @args.json          # or python -m graph_runtime; '-' reads stdin
graph-runtime mcp                                    # MCP over stdio; its session keeps projections
claude mcp add graph -- python -m graph_runtime mcp  # register it with an MCP host such as Claude Code
```

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
| `src/graph_runtime/` | The runtime (standard library only): `projection`, `request`, `engine`, `result`, `verify`, `revalidate`, `hypotheses`, `schemas/`, `conformance/`, the tool surface (`tools`, `mcp`, `cli`); and `retrieval`, which alone needs numpy |
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
