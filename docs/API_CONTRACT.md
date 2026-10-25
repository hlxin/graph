# GRAPH contract `graph-runtime/v0`

This is the contract the runtime implements (`src/graph_runtime`). It was written on 25 October 2026 from the
developer note ([GRAPH_Developer_Note_for_Claude.md](GRAPH_Developer_Note_for_Claude.md)). The handoff package the
note mentions (its own `API_CONTRACT.md`, kickoff prompt and research archive) was not in this checkout. The JSON
Schema of every record is `src/graph_runtime/schemas/graph-runtime-v0.schema.json` (`graph_runtime.json_schema`).

Every operation binds an immutable **projection** and a normalized **request**, and returns a **result**. The
runtime is exact-only and runs in process, on the CPU, with the standard library.

## 1. Projections

A projection is an immutable, read-only graph view built from the consumer's records:

```python
Projection.build(nodes=..., edges=..., relations=None, node_types=None, bindings=None)
```

| Input | Form | Rule |
| --- | --- | --- |
| `nodes` | an ID, or `{"id", "types": [...]}` | IDs are unique, non-empty strings; types are strings |
| `edges` | `{"id", "source", "target", "relation"}`; other keys are not read | IDs unique; both ends are nodes; one relation per edge |
| `relations`, `node_types` | the declared vocabularies | may hold relations or types no record uses; default: those the records use |
| `bindings` | `{name: text}` | what the projection describes: suggested `source`, `schema`, `policy` |

- **Original IDs are kept.** Nodes and edges are remapped to dense integers for execution, and every answer names
  them by their original IDs. Parallel edges (same ends and relation) stay distinct records.
- **Identity.** `projection.identity` is `sha256:<hex>`, computed over newline-terminated canonical JSON lines. The
  first line is a header: kind, contract, bindings, both vocabularies and the counts. Then comes one `[id, [types]]`
  line per node and one `[id, source, target, relation]` line per edge, each in ascending ID order. Canonical JSON
  means sorted keys, no whitespace and UTF-8 text. The same records in any order give the same identity; any
  change in a record, a vocabulary or a binding gives another. The identity can be reproduced from the records
  alone (`tests/test_projection.py`).
- **Immutable.** No attribute can be set, and no internal container is mutable. The records passed in are only
  read. `to_dict()` writes a JSON snapshot; `Projection.from_dict()` rebuilds it and checks its identity.
- **Source records only.** A record marked as a hypothesis candidate (`kind: hypothesis_candidate`, an
  `epistemic_status`, or a `HypothesisCandidate`) is refused (`ProjectionError`, code `candidate_record`). A
  proposed relation is never a traversable edge.
- **Staleness.** `projection.stale_bindings(current)` names the bindings that no longer match. A projection may be
  reused only while its source, schema/context bindings and policy scope are unchanged.

Errors: `ProjectionError` with code `duplicate_node_id`, `duplicate_edge_id`, `dangling_edge`,
`undeclared_relation`, `undeclared_node_type`, `invalid_record`, `invalid_bindings`, `candidate_record`, or (when
reading a snapshot) `invalid_snapshot`, `unsupported_contract` or `identity_mismatch`.

## 2. Requests

A request names its operation, the identity of the projection it was made for, its explicit scope and its limits.
`normalize_request(mapping)` (or `projection.request(operation, **params)`) does four things:

- checks the request's shape
- fills every default explicitly
- sorts and de-duplicates its sets
- gives it an identity, `request_id`: the digest of the normalized form

Two requests that ask the same thing share one `request_id`.

Common fields: `contract` (`graph-runtime/v0`), `operation`, `mode` (`exact`, the only mode), `projection` (the
identity, required) and `limits`.

**Scope.** A list of `{"relation", "direction"}`. `forward` follows an edge from its source to its target;
`reverse` follows it from its target to its source. A pair `[relation, direction]` is accepted as input. There is
no wildcard: a scope lists what it follows.

| Operation | Parameters | Limits |
| --- | --- | --- |
| `reachability` | `sources` (node IDs), `scope`, `max_depth` (null: unbounded) | `max_expansions`, `max_results` |
| `typed_path` | `sources`, `pattern` (positions `{"scope", "node_types"}`; `node_types` null: any), `targets` (null: any node) | `max_expansions` |
| `shortest_path` | `sources`, `targets`, `scope`, `costs`: `{"by_relation": {relation: cost}}` or `{"by_edge": {edge: cost}}` | `max_expansions` |
| `dependency_impact` | `changed` (node IDs), `scope` (the direction in which a change propagates), `max_depth` | `max_expansions`, `max_results` |

Defaults: `max_expansions` 1,000,000 and `max_results` 100,000. Every limit is a non-negative integer, and the
normalized request always states it.

**Costs.** A cost is an integer of any size or a finite float, never negative. When every cost is an integer,
paths are added as integers, so large costs keep their exact order. When the request gives any float, every cost
is added as an exact rational. In that rational mode, `cost` is the nearest float and `cost_exact` is the exact
value, `p/q` or `p`. Every edge in scope must have a cost.

## 3. Execution

```python
execute(projection, request, *, timeout=None, deadline=None, cancel=None, current_bindings=None,
        clock=time.monotonic, check_interval=64) -> Result
```

`execute` never raises for a problem with the request; the result states it. The checks run in this order:

1. Normalization (`failed` or `unsupported`).
2. The request's projection identity against this projection (`stale_snapshot`, `projection_mismatch`).
3. `current_bindings` against the projection's bindings (`stale_snapshot`, `bindings_changed`).
4. What the request names against what the projection holds (`failed`): unknown nodes, relations, node types and
   edges, and missing costs.
5. The search.

**Limits.**

- **Expansion.** One expansion examines every in-scope hop of one search state. A state is a node, or a node at a
  position of the pattern. `max_expansions` bounds the states expanded.
- **Output.** `max_results` bounds the nodes a reachability or impact answer lists.
- **Deadline and cancellation.** A `timeout` (seconds) or `deadline` (a `clock()` time), and a `cancel` (a
  `CancelToken`, a `threading.Event` or a callable), are checked before the first expansion and then every
  `check_interval` expansions.

When any of these stops the search, the result is `interrupted`. A search that needs no further expansion completes,
whatever its limits: a full answer that fits the limit is complete.

## 4. Operations

**Order.**

- Nodes and edges are ordered by ascending original ID (Unicode code point order).
- A node's hops are its forward-scope edges (it is their source) in ascending edge ID, then its reverse-scope edges
  (it is their target) in ascending edge ID.
- Breadth-first searches run first in, first out, from the sources in ascending ID.
- The shortest-path search settles nodes by (cost, node ID).

This order makes answers reproducible and decides which of several equally good witnesses is returned. The choice
is fixed within a contract version, and a consumer must not read meaning into it.

**Reachability.** The answer lists every node reachable from a source within the scope and `max_depth`. Sources are
listed at depth 0. Each other node is listed with its least hop count and `via`, the hop it was first reached by
(`edge`, `relation`, `direction`, `from`). The `via` links form a tree; `result.path_to(node)` rebuilds the full
witness. Answer: `{"reached": [{"node", "depth", "via"}], "count"}`.

**Dependency impact.** Directed reachability on a consumer-defined dependency projection, from the `changed`
nodes. A node is listed only when at least one hop reaches it. A changed node is therefore listed only when another
change, or a cycle, reaches it; its witness then starts at that other change. Answer:
`{"changed": [...], "affected": [{"node", "depth", "via"}], "count"}`. What an affected record requires is the
consumer's to decide.

**Typed path.** Finds one walk matching a finite relation sequence, from a source to a target (or to any node). The
search state is a node plus its position in the sequence. Hop `i` follows `pattern[i].scope` and lands on a node of
`pattern[i].node_types`. A witness may visit a node, or use an edge, more than once. Answer:
`{"found", "witness": {"source", "target", "length", "hops": [{"position", "edge", "relation", "direction", "from",
"to"}]}}`.

**Shortest path.** Finds one minimum-cost witness from any source to any target, using Dijkstra's algorithm with
exact arithmetic. A source that is also a target is the answer at cost 0. Each hop carries the cost the request
gave its edge. Answer: `{"found", "optimal", "cost", ["cost_exact"], "witness"}`.

## 5. Results

| Field | Meaning |
| --- | --- |
| `status` | `completed`, `interrupted`, `unsupported`, `failed` or `stale_snapshot` |
| `complete` | true exactly when the status is `completed` |
| `operation`, `mode`, `projection`, `request`, `request_id` | what was asked, normalized, and of which projection |
| `answer` | the operation's answer; null when the request was not run |
| `interruption` | `{"reason": "expansion_budget" \| "output_limit" \| "deadline" \| "cancelled", "expansions", "limit"}` |
| `error` | `{"code", "message", "detail"}` for `unsupported`, `failed` and `stale_snapshot` |
| `stats` | `expansions`, `edges_examined` and `discovered` (states reached): deterministic counters |
| `limitations` | what the answer does not claim, in words |
| `runtime`, `contract`, `kind` | `scale-graph-runtime` and its version, `graph-runtime/v0`, `graph_runtime.result` |
| `result_id` | the digest of everything but `timing` |
| `timing` | wall-clock seconds for `bind_s`, `search_s`, `witness_s`, `result_s` and `total_s`; not part of `result_id` |

- **Completed.** The answer is complete for this request on this projection. A complete negative (nothing reached,
  `found: false`) is scoped to the requested operation on the declared projection. It is not an absence claim about
  the literature or the physical world.
- **Interrupted.** What the answer lists is valid, each item with its witness, and listed depths are least depths.
  Nothing is certified absent: `found` is `null` (unknown), or `true` when a witness was already found.
  `result.reachable(x)` and `result.affected(x)` give `False` only for a complete answer, and `None` when the
  answer cannot say.
- **Interrupted shortest path.** A tentative witness has `optimal: false`. `lower_bound` (with `lower_bound_exact`
  in rational mode) brackets the minimum: if a target is reachable at all, the minimum cost lies in
  `[lower_bound, cost]`.
- **Reproducible.** The same runtime, projection and request give the same `result_id`, unless a deadline or a
  cancellation interrupted the search. `Result.from_dict` reads a result's JSON and checks its `result_id`.

**Error codes.**

| Status | Codes |
| --- | --- |
| `unsupported` | `unsupported_operation`, `unsupported_mode`, `unsupported_contract`, `negative_cost` |
| `failed` | `invalid_request`, `invalid_cost`, `non_finite_cost`, `unknown_node`, `unknown_relation`, `unknown_node_type`, `unknown_edge`, `missing_cost`, `internal_error` |
| `stale_snapshot` | `projection_mismatch`, `bindings_changed` |

The result contract reports how an execution ended. It carries no scientific answer status: SCALE's `answered`,
`no_matching_record`, `missing_context` and `schema_gap` stay with SCALE.

## 6. Checking results

- `verify_result(result, nodes=..., edges=...)` re-checks a result against the input records, independently of the
  engine. It checks:
  - the contract's invariants
  - that every witness hop names an input edge with that relation, in the requested scope and direction
  - that hops chain from a requested source to what they witness, and match typed-path positions and node types
  - that shortest-path costs are the exact sum of the hop costs

  It does not check completeness or minimality; the oracle tests do.
- `graph_runtime.conformance.run_conformance(execute)` runs 34 language-neutral cases (`conformance/cases.json`),
  with expectations written by hand from their graphs, against any backend's `execute`. A completed answer must
  match exactly, witness choice aside unless the case names the one optimal witness. An interrupted answer may list
  only what the full answer holds.

## 7. Not in v0

These are later extensions:

- approximate modes and ranking (personalized PageRank, learned graph reasoning)
- GPU execution and alternative backends (to be introduced after profiling, behind the conformance suite)
- generic AND/hypergraph execution
- property filters on values
- traversal of candidate relations: a combined view needs its own contract, one that keeps candidate identity in
  every edge and witness

Reaction-specific feasibility, kinetics and every other scientific interpretation remain with the applications. A
path through a species-connectivity graph does not certify that the reactants, quantities, sites or kinetic
conditions are satisfied.
