# GRAPH

**Graph Runtime for Analysis, Provenance, Hypotheses**: reusable graph algorithms for scientific software.

GRAPH is a small Python library that gives scientific applications reliable graph execution, under one contract:

- explicit inputs: an immutable projection of the application's records and a normalized request
- supported operations only, exact only
- bounded work: budgets, output limits, deadlines and cancellation, and an interrupted search never certifies a
  negative
- reproducible results: the same projection and request give the same `result_id`
- witnesses that name the original records, and certificates that prove an answer against them

The applications that use it (SCALE, STATES, RATES, Cathanor) keep their scientific definitions, domain rules,
authoritative records and workflows. GRAPH imports none of their vocabularies; each application reaches it through an
adapter it owns ([docs/DESIGN.md](docs/DESIGN.md)). The runtime is the Python standard library only.

Status: **in development** (`0.1.0.dev0`, contract `graph-runtime/v0.2`), not yet released on PyPI. MIT licensed.

## Install

```bash
pip install git+https://github.com/hlxin/graph       # until the first release on PyPI as graph-runtime-sci
pip install 'graph-runtime-sci[retrieval] @ git+https://github.com/hlxin/graph'   # with the optional text search (numpy)
```

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

[examples/synthetic_revision](examples/synthetic_revision/README.md) walks through a revision, its dependency impact
and a hypothesis candidate about it.

## Projections

An immutable, read-only graph view of the consumer's records. It keeps the original node and edge IDs (remapped to
integers inside) and keeps parallel edges as distinct records. Optional type and relation hierarchies let a type
match its subtypes' nodes and a relation cover its specializations. Its identity digest is bound to the consumer's
source, schema and policy, so a stale projection is detected.

A **periodic projection** holds one cell of an infinite periodic graph (a crystal, a slab), with image shifts on its
edges. Reachability and impact search its images (`atom@i,j,k`) without lifting a box of them.

## Operations

Each operation returns structural witnesses, and on request a certificate ([docs/API_CONTRACT.md](docs/API_CONTRACT.md)).

**Paths and reachability**

- **Reachability**, and **between pairs**: many source-target questions from a 2-hop index kept on the projection,
  each reachable pair with a simple path.
- **Typed path**: a finite relation sequence, with optional node types per position.
- **Regular path**: walks whose hops match a regular expression of steps, such as
  `DERIVED_FROM*/SUPPORTED_BY[Measurement]/IN`: one with the fewest hops, or every node reached.
- **Shortest path**: explicit, finite costs with exact integer and rational arithmetic. Dijkstra's algorithm when no
  cost is negative; otherwise Bellman-Ford-Moore with subtree disassembly, reporting a negative cycle on the way (such
  as an exergonic loop in a reaction network) with walks that prove it.
- **k shortest paths**: the first k simple paths in a strict order, by Yen's algorithm: alternative reaction pathways.
- **Bottleneck path**: the path whose weakest edge is strongest, or whose highest edge is lowest, with the fewest hops:
  a pathway's fastest rate-limiting step, or its lowest highest barrier.
- **Dependency impact** and **dominators**: what depends on a change, and the nodes every route from the sources must
  pass through (unavoidable intermediates, single points of failure).

**Patterns and rules**

- **Pattern**: variables and steps, optional steps included. Trees are matched without dead ends and counted exactly
  without listing them; cycles (corroboration diamonds, triangles) are listed by a worst-case optimal join and counted
  by variable elimination along a tree decomposition; injective patterns (distinct nodes and edges) give substructure
  search for motifs, sites and rings.
- **Datalog**: positive, recursive rules over the records (`node`, `type`, `edge`), evaluated semi-naively to the least
  model: lineage, provenance closures, any fixpoint of joins, every fact with its derivation.
- **Support**: what loses all or some of its support when nodes fail, under AND/OR rules, as a least fixpoint.
- **Derivation**: the best derivation of each target in an AND/OR graph, where a reaction needs all its reactants and
  a species any reaction that makes it: the cheapest route from given feedstocks, the one whose highest barrier is
  lowest, or whose slowest step is fastest.

**Structure**

- **Components**: weak, or strong with each flagged initial, terminal and cyclic: fragments, feedback loops, circular
  derivations.
- **Biconnectivity**: articulation points with the pieces each leaves, bridges and blocks.
- **Spanning forest**: the minimum or maximum spanning forest, unique by edge ID among ties.
- **Cycle basis**: a minimum cycle basis by de Pina's algorithm: a molecule's smallest set of smallest rings.
- **Canonical forms** and **isomorphism**: one name for every copy of a structure, by individualization and
  refinement, for deduplicating molecules and motifs.
- **Periodicity**: each component's translation lattice in a periodic projection: rank 0 (molecule) to 3
  (framework), a canonical basis with witnesses, and its degree of interpenetration.
- **Critical path**: the canonical topological order, makespan, critical path and every node's slack, or a cycle.
- **d-separation**: whether a causal graph makes two sets independent given a third.

**Flows, matchings and networks**

- **Max flow** and **min-cost flow**: a maximum flow and a minimum cut that prove each other, and a given throughput
  at least cost.
- **Matching**: as many distinct pairings as possible, with the vertex cover that proves no more exist; with costs,
  the assignment problem.
- **Incidence**: the exact rank and kernels of the matrix of edge coefficients between two node sets: a reaction
  network's independent reactions and conservation laws, a Petri net's invariants.
- **Elementary flux modes**: a reaction network's minimal steady-state pathways, by exact double description, each
  proven elementary.
- **Personalized PageRank**: a relevance ranking around chosen nodes in which every score is an interval proven to
  hold it, the top of the ranking certified where intervals separate.

## Guarantees

| Part | What it provides |
| --- | --- |
| Limits | Expansion budget and output limit (part of the request), deadline and cancellation (execution controls). A limit that prevents completion makes the result `interrupted`; a full answer that fits can complete. An interrupted result never certifies a negative |
| Results | One contract: `completed`, `interrupted`, `unsupported`, `failed` or `stale_snapshot`, with the normalized request, the projection identity, the answer, statistics, limitations and a reproducible `result_id` |
| Checks | `verify_result` re-checks every witness against the input records; JSON Schemas describe every record; a language-neutral conformance suite any backend must pass |
| Certificates | On request, an answer proves itself against the records without a second search: closures and closed states for negatives, potentials for optima and lower bounds, cuts, covers, minors and kernels, orders and forests, least fixpoints' ranks. `verify_result` checks them |
| Revalidation | Whether a cached result still holds on an edited projection (`holds`, `fails` with the edit that refutes it, or `unknown`), decided from its answer and certificate; with one diff of the projections, in time proportional to the edit |
| Hypotheses | Candidate records that are always `unverified`, with discriminating proposed tests, calibrated or ordering-only scores, append-only lifecycle and assessment history, and staleness through dependency impact. They are never traversed |
| Retrieval (optional, numpy) | `graph_runtime.retrieval`: exact keyword (BM25), hashed-vector, hybrid and rank-fusion search over text items that name records, so a search can choose where a traversal starts ([docs/RETRIEVAL.md](docs/RETRIEVAL.md)) |

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

The SCALE and STATES integration tests run in each application's environment, from checkouts beside this one, and
only read them ([docs/SCALE_INTEGRATION.md](docs/SCALE_INTEGRATION.md), [docs/STATES_INTEGRATION.md](docs/STATES_INTEGRATION.md)):

```bash
PYTHONDONTWRITEBYTECODE=1 ../scale/.venv/bin/python -m pytest integrations/scale -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 ../STATES/.venv/bin/python -m pytest integrations/states -p no:cacheprovider
```

The benchmarks are in `benchmarks/` ([docs/PERFORMANCE.md](docs/PERFORMANCE.md)).

## Layout

| Path | What it holds |
| --- | --- |
| `src/graph_runtime/` | The runtime (standard library only): `projection`, `request`, `engine`, `result`, `verify`, `revalidate`, `hypotheses`, an algorithm module per family of operations, `schemas/`, `conformance/`, the tool surface (`tools`, `mcp`, `cli`); and `retrieval`, which alone needs numpy |
| `tests/` | Unit, scenario, oracle-parity (independent oracles and NetworkX), limit, schema, golden and conformance tests |
| `integrations/scale/`, `integrations/states/` | The SCALE and STATES adapters (application-owned, staged here) and their parity tests against each application's own code |
| `benchmarks/` | Whole-request measurements on synthetic graphs and on SCALE's and STATES's records |
| `examples/` | The synthetic revision example |
| `docs/` | The design, the contract, validation, performance, retrieval, hypotheses and the two integrations |

## Documentation

- [docs/DESIGN.md](docs/DESIGN.md): the boundary between GRAPH and the applications, and the principles.
- [docs/API_CONTRACT.md](docs/API_CONTRACT.md): the contract as implemented.
- [docs/VALIDATION.md](docs/VALIDATION.md): how the answers are checked.
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md): the benchmarks and their results.
- [docs/HYPOTHESES_AND_PROVENANCE.md](docs/HYPOTHESES_AND_PROVENANCE.md): hypothesis candidates.
- [docs/RETRIEVAL.md](docs/RETRIEVAL.md): search, then traverse.
- [docs/SCALE_INTEGRATION.md](docs/SCALE_INTEGRATION.md): the SCALE adapter and its parity results.
- [docs/STATES_INTEGRATION.md](docs/STATES_INTEGRATION.md): the STATES adapter and its parity results.

## License

MIT. See [LICENSE](LICENSE).
