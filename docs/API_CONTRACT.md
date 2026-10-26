# GRAPH contract `graph-runtime/v0.2`

This is the contract the runtime implements (`src/graph_runtime`). It was written on 25 October 2026 from the
developer note ([GRAPH_Developer_Note_for_Claude.md](GRAPH_Developer_Note_for_Claude.md)). The handoff package the
note mentions (its own `API_CONTRACT.md`, kickoff prompt and research archive) was not in this checkout. The JSON
Schema of every record is `src/graph_runtime/schemas/graph-runtime-v0.schema.json` (`graph_runtime.json_schema`).

Every operation binds an immutable **projection** and a normalized **request**, and returns a **result**. The
runtime is exact-only and runs in process, on the CPU, with the standard library.

Revision `v0.1` tightened transport validation, ordered node-set answers by ascending ID, counted source search
states consistently, and required costs only for edges actually in scope. Revision `v0.2`, in development, adds:

- type and relation hierarchies (section 1)
- the `pattern` operation: tree patterns, enumerated without dead ends and counted without listing (section 4)
- the `support` operation: what loses all or some of its support when nodes fail, under AND/OR rules (section 4)
- certificates: complete answers that prove their negatives, optima and lower bounds against the records without a
  second search, and closure checks of complete reachability and impact answers (sections 4 and 6)
- revalidation: whether a cached result still holds on an edited projection, decided from its answer and
  certificate (section 7)
- the `bottleneck_path` operation: the path whose weakest edge is strongest, or whose highest edge is lowest, with
  the fewest hops among those, certified by a threshold closure (section 4)

Each revision supersedes the one before; readers reject other contract tags. The schema keeps its original filename.
Revisions follow `CLAUDE.md`'s contract-version rule.

## 1. Projections

A projection is an immutable, read-only graph view built from the consumer's records:

```python
Projection.build(nodes=..., edges=..., relations=None, node_types=None, bindings=None,
                 type_parents=None, relation_parents=None)
```

| Input | Form | Rule |
| --- | --- | --- |
| `nodes` | an iterable of IDs or `{"id", "types": [...]}` records | IDs are unique, non-empty strings; types are strings; a bare string or mapping is not a record collection |
| `edges` | an iterable of `{"id", "source", "target", "relation"}`; other keys are not read | IDs unique; both ends are nodes; one relation per edge |
| `relations`, `node_types` | the declared vocabularies | may hold relations or types no record uses; default: those the records use |
| `bindings` | `{name: text}` | what the projection describes: suggested `source`, `schema`, `policy` |
| `type_parents`, `relation_parents` | `{name: [parent, ...]}` | hierarchies: acyclic, several parents allowed; names join the vocabularies (and must be in declared ones) |

- **Hierarchies.** A node has every ancestor of the types it declares, so a constraint on a type matches the nodes
  of its subtypes. A scope entry on a relation also follows every relation below it: a scope on `CONTROL_FOR` follows
  `DARK_CONTROL_FOR` edges, never the reverse. `projection.covered(relation)` lists what an entry follows. Witnesses
  still name each edge's own relation, `node(id).types` the types a node declares, and `by_relation` costs the
  relation each edge has: no cost is inherited.
- **Original IDs are kept.** Nodes and edges are remapped to dense integers for execution, and every answer names
  them by their original IDs. Parallel edges (same ends and relation) stay distinct records.
- **Identity.** `projection.identity` is `sha256:<hex>`, computed over newline-terminated canonical JSON lines. The
  first line is a header: kind, contract, bindings, both vocabularies, both hierarchies and the counts. Then
  comes one `[id, [types]]` line per node and one `[id, source, target, relation]` line per edge, each in ascending ID order. Canonical JSON
  means sorted keys, no whitespace and UTF-8 text. The same records in any order give the same identity; any
  change in a record, a vocabulary or a binding gives another. The identity can be reproduced from the records
  alone (`tests/test_projection.py`).
- **Immutable.** No attribute can be set, and no internal container is mutable. The records passed in are only
  read. `to_dict()` writes a JSON snapshot; `Projection.from_dict()` requires every schema field (including identity,
  bindings and vocabularies), rejects extra fields and malformed containers, rebuilds it and checks its identity.
- **Source records only.** A record marked as a hypothesis candidate (`kind: hypothesis_candidate`,
  `epistemic_status: unverified`, or a `HypothesisCandidate`) is refused (`ProjectionError`, code
  `candidate_record`). A proposed relation is never a traversable edge.
- **Staleness.** `projection.stale_bindings(current)` names the bindings that no longer match. A projection may be
  reused only while its source, schema/context bindings and policy scope are unchanged.

Errors: `ProjectionError` with code `duplicate_node_id`, `duplicate_edge_id`, `dangling_edge`,
`undeclared_relation`, `undeclared_node_type`, `invalid_record`, `invalid_bindings`, `invalid_hierarchy`,
`hierarchy_cycle`, `candidate_record`, or (when
reading a snapshot) `invalid_snapshot`, `unsupported_contract` or `identity_mismatch`.

## 2. Requests

A request names its operation, the identity of the projection it was made for, its explicit scope and its limits.
`normalize_request(mapping)` (or `projection.request(operation, **params)`) does four things:

- checks the request's shape
- fills every default explicitly
- sorts and de-duplicates its sets
- gives it an identity, `request_id`: the digest of the normalized form

Two requests that ask the same thing share one `request_id`.

Node-ID sets, scopes, typed patterns and non-null node-type lists must be non-empty. A projection itself may be
empty; requesting a node it does not contain fails as `unknown_node`.

Common fields: `contract` (`graph-runtime/v0.2`), `operation`, `mode` (`exact`, the only mode), `projection` (the
identity, required) and `limits`.

**Scope.** A list of `{"relation", "direction"}`. `forward` follows an edge from its source to its target;
`reverse` follows it from its target to its source. A pair `[relation, direction]` is accepted as input. There is
no wildcard: a scope lists what it follows.

| Operation | Parameters | Limits |
| --- | --- | --- |
| `reachability` | `sources` (node IDs), `scope`, `max_depth` (null: unbounded) | `max_expansions`, `max_results` |
| `typed_path` | `sources`, `pattern` (positions `{"scope", "node_types"}`; `node_types` null: any), `targets` (null: any node), `certificate` (default false) | `max_expansions` |
| `shortest_path` | `sources`, `targets`, `scope`, `costs`: `{"by_relation": {relation: cost}}` or `{"by_edge": {edge: cost}}`, `certificate` (default false) | `max_expansions` |
| `dependency_impact` | `changed` (node IDs), `scope` (the direction in which a change propagates), `max_depth` | `max_expansions`, `max_results` |
| `pattern` | `match` (`{"var", "node_types", "nodes"}`, at least one constraint), `steps` (each `{"from", "var", "scope", "node_types", "nodes", "edges", "edge_var", "optional"}`), `answer` (`rows`, `count` or `exists`; default `rows`) | `max_expansions`, `max_results` |
| `support` | `failed` (node IDs), `scope` (from a node to what supports it), `all_of` (node types that need every support; null: every node needs one) | `max_expansions`, `max_results` |
| `bottleneck_path` | `sources`, `targets`, `scope`, `weights` (as costs, but any finite number), `objective` (`max_min`, the default, or `min_max`), `certificate` (default false) | `max_expansions` |

Defaults: `max_expansions` 1,000,000 and `max_results` 100,000. Every limit is a non-negative integer, and the
normalized request always states it.

**Costs.** A cost is an integer of any size or a finite float, never negative. When every cost is an integer,
paths are added as integers, so large costs keep their exact order. When the request gives any float, every cost
is added as an exact rational. In that rational mode, `cost` is the nearest float (null when the exact value is
beyond the floats) and `cost_exact` is the exact value, `p/q` or `p`. Every edge in scope must have a cost. A
declared relation with no in-scope edges needs no cost, for either cost-table form. A
request whose text is not valid Unicode, or which holds a number too long to write as JSON, fails as
`invalid_request`.

## 3. Execution

```python
execute(projection, request, *, timeout=None, deadline=None, cancel=None, current_bindings=None,
        clock=time.monotonic, check_interval=64) -> Result
```

Once its own arguments are valid, `execute` never raises: a problem with the request, an answer that cannot be
written as JSON, or a runtime failure is a result that states it. The checks run in this order:

1. Normalization (`failed` or `unsupported`).
2. The request's projection identity against this projection (`stale_snapshot`, `projection_mismatch`).
3. `current_bindings` against the projection's bindings (`stale_snapshot`, `bindings_changed`).
4. What the request names against what the projection holds (`failed`): unknown nodes, relations, node types and
   edges, and missing costs.
5. The search.

`timeout` and `deadline` are finite numbers of seconds (not booleans); a negative value is allowed and may already
be expired. `clock` is callable and `check_interval` is a positive integer (not a boolean). Invalid execution
arguments raise `TypeError` or `ValueError`; a failing clock or cancellation callback produces `failed/internal_error`.

**Limits.**

- **Expansion.** One expansion starts examining the in-scope hops of one search state. A state is a node, or a node
  at a position of the pattern. A witness or output limit can end that scan early. `max_expansions` bounds the states
  expanded; checking a node with no in-scope hops still counts as an expansion.
- **Output.** `max_results` bounds the nodes a reachability or impact answer lists.
- **Deadline and cancellation.** A `timeout` (seconds) or `deadline` (a `clock()` time), and a `cancel` (a
  `CancelToken`, a `threading.Event` or a callable), are checked before the first expansion and then every
  `check_interval` expansions.

When any of these stops the search, the result is `interrupted`. A search that needs no further expansion completes,
whatever its limits: a full answer that fits the limit is complete.

## 4. Operations

**Order.**

- Nodes and edges are ordered by ascending original ID (Unicode code point order).
- Reachability and impact entries are serialized in ascending node ID order. Discovery and output-buffer selection
  still follow the breadth-first order below; sorting the retained entries does not change their witnesses.
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
"to"}]}, "certificate"}`.

With `certificate: true`, a complete negative carries its proof: `{"kind": "closed_states", "states": [{"node",
"position"}]}`, every state (node, position before the last) the sources reach, by position then node ID. Every
source is a state at position 0. Every hop the pattern allows from a state leads to a listed state at the next
position, or, from the last position, to a node that is not a target. So no walk matching the pattern reaches a
target. A found answer needs no certificate (its witness proves it), and an interrupted one has none.

**Shortest path.** Finds one minimum-cost witness from any source to any target, using Dijkstra's algorithm with
exact arithmetic. A source that is also a target is the answer at cost 0. Each hop carries the cost the request
gave its edge. Answer: `{"found", "optimal", "cost", ["cost_exact"], "witness", "certificate"}`.

With `certificate: true`, the answer carries its proof:

- **Potentials** prove an optimum, and an interrupted search's lower bound: `{"kind": "potentials", "bound",
  "potentials": [{"node", "value"}]}`. The values are the settled costs below the bound, by node ID; every other
  node has the bound. Every source has 0, every target at least the bound, and no hop in scope from a listed node
  leads to a value more than its cost above. So no path from a source to a target costs less than the bound. The
  bound is the witness's cost, or the lower bound. Numbers are integers, or `p/q` strings in rational mode.
- **A closure** proves a complete negative: `{"kind": "closure", "reached": [...]}`, everything the sources reach,
  closed under the scope's hops, with no target among it.

**Pattern.** Matches a tree pattern, SCALE's query plans in generic form:

- **Variables.** `match` binds a variable to the nodes it admits: its types, through the hierarchy, and/or explicit
  node IDs. Each step binds a new variable to a node one hop away from an earlier variable. The hop follows the
  step's scope (with the relation hierarchy) to a node the step admits, by the same constraints, through an edge in
  the step's explicit edge IDs when it gives them. Explicit ID sets are how a consumer passes the result of its own
  filters, such as SCALE's value and unit semantics. `edge_var` names a step's edge.
- **Optional steps.** An optional step that finds no hop binds null, and the row stays. A step from a variable an
  optional step binds must itself be optional, so an unmatched optional part never removes a row.
- **Rows.** A row binds every variable (`nodes`), every edge variable (`edges`), and one link per step (`links`;
  null for an unbound optional step). Variables may bind the same node. Parallel edges give distinct rows. A
  self-loop in a scope of both directions gives one row, not two.
- **Order.** Rows come in canonical order: matched nodes by ascending ID, then each step's hops in the order every
  operation uses.

Evaluation never meets a dead end; this is Yannakakis' semi-join reduction, over the region the match reaches.

1. Discovery goes downward. It is the only pass that expands states: each candidate's hops are scanned once per step.
2. Pruning goes upward: a required step keeps only the parents with a hop into its child's remaining candidates.
3. Pruning goes downward again: each step keeps only the hops between its remaining parents and children.

Every remaining value then completes to a match, so the rows are enumerated from the kept hops in time proportional
to the input scanned plus the rows returned. Their number, `total`, is computed exactly by multiplying and summing
along the kept hops, without listing them.

- `answer: count` and `answer: exists` return `total` and `exists` and list no rows.
- `answer: rows` lists up to `max_results` rows. More than that is an `output_limit` interruption, but its `total` is
  still the exact count.
- `coverage` gives each variable's candidates and the number it kept, and each step's kept hops.
- An expansion budget, a deadline or a cancellation that stops discovery leaves `exists` and `total` null: unknown,
  never false.

Answer: `{"exists", "total", "rows": [{"nodes", "edges", "links"}], "count", "coverage"}`.

**Bottleneck path.** Finds the path from any source to any target whose bottleneck is best. Under `max_min`, a
path's value is its weakest edge's weight and higher is better: in a reaction network weighted by rates, this is
the pathway whose rate-limiting step is fastest. Under `min_max`, a path's value is its highest edge's weight and
lower is better: weighted by barriers, this is the pathway whose highest barrier is lowest. Weights are compared and
never added, so any finite numbers are exact, negative ones included.

1. Nodes are settled best value first, ties by node ID, as Dijkstra's algorithm settles costs. The first target
   settled has the best value.
2. A breadth-first search over the hops at least as good as that value then finds, among the paths that reach it,
   one with the fewest hops, in canonical order.

A source that is also a target is the answer, a path with no hop and no value. Each hop carries its weight. Answer:
`{"found", "optimal", "value", "witness", "certificate"}`, and `bound` when interrupted: no path to a target is
strictly better than the bound. An interruption while the second step chooses among the best paths gives the first
step's path, with its value as the bound.

With `certificate: true`, the answer carries its proof. A **threshold** (`{"kind": "threshold", "threshold",
"reached"}`) lists what the sources reach by hops strictly better than the threshold, closed under those hops, with
no target. It proves that no path is strictly better than the threshold: the value, or the bound. A **closure**
proves a complete negative, as for shortest paths.

**Support.** What stops standing when the `failed` nodes fail, and what stands on less than before. It generalizes
SCALE's "rests on a retraction, all or in part". A hop in `scope` leads from a node to one of its supports, for
example from a claim to its evidence items, or from an item to the source it cites.

- **Rules.** A node with no support stands: it is a ground, such as a source or an axiom. A node of an `all_of` type
  (through the hierarchy) stands when every support stands. Any other node stands when one of its supports does.
  Parallel hops to one support count once. A failed node never stands.
- **Least fixpoint.** What stands is the least fixpoint of these rules, so support in a circle grounds nothing. A
  node supported only by a cycle that has lost its way to a ground falls with it.
- **Before and after.** The analysis computes what stands with nothing failed, and with the failed nodes held down.
  `lost` lists the nodes that stood before and do not now. `weakened` lists the nodes that still stand but lost a
  support that stood before. Nodes that never stood (`ungrounded`) are counted, never listed as lost.
- **Entries.** Each entry gives the node's `supports` (distinct support nodes), how many still stand
  (`standing`), and `failing`: the first hop, in canonical order, to each support that fell. `causes` are the
  failed nodes it rests on through fallen supports. A failed node's own cause is itself, so a failure behind
  another failure is not named twice.
- **Cost.** Only the region the failures reach is read: their dependents, and everything those depend on. The rest
  of the projection cannot change the answer.
- **Limits.** An expansion budget, a deadline or a cancellation that stops the analysis leaves `lost`, `weakened`
  and `counts` null: unknown, never empty. `max_results` bounds the nodes listed, lost ones first. More than that is
  an `output_limit` interruption, and the counts stay exact.

Answer: `{"failed", "lost": [{"node", "supports", "standing", "failing": [hop], "causes"}], "weakened": [...],
"counts": {"lost", "weakened", "ungrounded"}}`.

## 5. Results

| Field | Meaning |
| --- | --- |
| `status` | `completed`, `interrupted`, `unsupported`, `failed` or `stale_snapshot` |
| `complete` | true exactly when the status is `completed` |
| `operation`, `mode`, `projection`, `request`, `request_id` | what was asked, normalized, and of which projection |
| `answer` | the operation's answer; null when the request was not run |
| `interruption` | `{"reason": "expansion_budget" \| "output_limit" \| "deadline" \| "cancelled", "expansions", "limit"}` |
| `error` | `{"code", "message", ["detail"]}` for `unsupported`, `failed` and `stale_snapshot` |
| `stats` | `expansions`, `edges_examined` and `discovered`: deterministic counters; discovered counts the source states plus newly retained search states, including changed nodes even when absent from the impact answer |
| `limitations` | what the answer does not claim, in words |
| `runtime`, `contract`, `kind` | `scale-graph-runtime` and its version, `graph-runtime/v0.2`, `graph_runtime.result` |
| `result_id` | the digest of everything but `timing` |
| `timing` | wall-clock seconds for `bind_s`, `search_s`, `witness_s`, `result_s` and `total_s`; errors may report only result and total time; not part of `result_id` |

- **Completed.** The answer is complete for this request on this projection. A complete negative (nothing reached,
  `found: false`) is scoped to the requested operation on the declared projection. It is not an absence claim about
  the literature or the physical world.
- **Interrupted.** What the answer lists is valid, each item with its witness, and listed depths are least depths.
  Nothing is certified absent: `found` is `null` (unknown), or `true` when a witness was already found.
  `result.reachable(x)`, `result.affected(x)`, `result.found` and `result.exists` (patterns) give `False` only for a
  complete answer, and `None` when the
  answer cannot say.
- **Interrupted shortest path.** A tentative witness has `optimal: false`. `lower_bound` (with `lower_bound_exact`
  in rational mode) brackets the minimum: if a target is reachable at all, the minimum cost lies in
  `[lower_bound, cost]`. In rational mode use the exact fields for this comparison; the displayed floats may round.
- **Reproducible.** The same runtime, projection and request give the same `result_id`, unless a deadline or a
  cancellation interrupted the search.
- **Read back.** `Result.from_dict` reads a result's JSON, another backend's included. It checks the bundled schema's
  shape rules and the contract's cross-field invariants (`graph_runtime.result.contract_problems`): result and
  request digests, request normalization, operation/mode/projection binding, status/answer/error consistency,
  limits, counters, canonical node order, counts, witness chains and exact cost sums and bounds. Only
  `stale_snapshot/projection_mismatch` may bind a request to a different projection. These checks do not prove
  completeness or minimality. `reachable`, `affected` and `found`
  give a negative only for a `completed` result.

The schema describes JSON shapes. Digests, sorted sets, cross-field equality, cost arithmetic and membership in
input records require runtime checks. JSON must contain finite numbers, valid Unicode and string object keys;
Python integer fields reject booleans and floats even where JSON Schema considers `1.0` an integer.

**Error codes.**

| Status | Codes |
| --- | --- |
| `unsupported` | `unsupported_operation`, `unsupported_mode`, `unsupported_contract`, `negative_cost` |
| `failed` | `invalid_request`, `invalid_cost`, `non_finite_cost`, `unknown_node`, `unknown_relation`, `unknown_node_type`, `unknown_edge`, `missing_cost`, `unrepresentable_result`, `internal_error` |
| `stale_snapshot` | `projection_mismatch`, `bindings_changed` |

The result contract reports how an execution ended. It carries no scientific answer status: SCALE's `answered`,
`no_matching_record`, `missing_context` and `schema_gap` stay with SCALE.

## 6. Checking results

- `verify_result(result, nodes=..., edges=..., type_parents=None, relation_parents=None)` re-checks a result
  against the input records, independently of the engine. It checks:
  - the contract's invariants
  - that every witness hop names an input edge with that relation, in the requested scope and direction
  - that hops chain from a requested source to what they witness, and match typed-path positions and node types
  - that shortest-path costs are the exact sum of the hop costs
  - that a complete reachability or impact answer is closed: every hop in scope from a node it expanded (a changed
    node at depth 0) leads to a listed node at most one hop deeper. With the witnesses, this proves the answer
    complete and its depths least
  - that every certificate proves its answer, as section 4 describes
  - for support, everything: it recomputes what stands before and after from the records, by re-evaluating each
    node's rule until nothing changes rather than by the engine's counting, and compares every listed node, count,
    failing hop and cause

  A typed or shortest path negative without a certificate, and a pattern's rows and total, are left to the oracle
  tests.
- `graph_runtime.conformance.run_conformance(execute)` runs 70 language-neutral cases (`conformance/cases.json`),
  with expectations written by hand from their graphs, against any backend's `execute`. A completed answer must
  match exactly and bind the case's projection and normalized request. Named `witness_edges` expectations check
  the canonical witness choice, including ties. An interrupted answer may list only what the full answer holds.
  Cases without deadline or cancellation controls are repeated to check `result_id` reproducibility.

## 7. Revalidation

`graph_runtime.revalidate` decides whether a cached result still holds on another projection, such as one built
after the records were edited, without searching again.

- `revalidate(result, projection)` checks the result's answer and certificate against the new projection, in time
  proportional to the answer and the hops from what it lists.
- `diff(old, new)` lists what changed, by original IDs: nodes added, removed and retyped, and edges added and removed
  (an edge whose ends or relation changed counts as both). It reads each projection once.
- `revalidate(result, projection, diff=d)` checks only what the edit touches, in time proportional to the edit. It
  looks up the answer's canonically ordered lists by binary search. It trusts that the result was correct on its own
  projection. When the vocabulary (relations, node types, hierarchies) changed, the full check runs instead.
- `revalidate_all(results, old, new)` revalidates every result with one diff.

The verdict is one of three:

| Verdict | Meaning | Reasons |
| --- | --- | --- |
| `holds` | the answer is still a correct answer to the same request on the new projection. A new search might choose other witnesses among equally good ones | none |
| `fails` | it is not | `node_gone`, `witness_gone`, `type_changed`, `reaches_unlisted` (a hop from an expanded node to a node the answer does not list), `reaches_shallower`, `unanswerable` (the request names a node, relation, type or edge the new projection lacks, or an edge in scope has no cost) |
| `unknown` | neither can be shown without a new search | `not_complete` (an interrupted or unanswered result), `not_certified` (pattern, support), `no_certificate` (a negative or optimum without one), `certificate_broken` (an edit broke the proof without refuting the answer) |

Each reason names the edge or node concerned. The record is `{"kind": "graph_runtime.revalidation", "contract",
"operation", "result_id", "request_id", "from_projection", "to_projection", "verdict", "reasons", "reason_count",
"revalidation_id"}`. `reasons` holds at most 100 entries, in canonical order. `revalidation_id` digests the rest.
The full and diff checks give the same record. `checked` (`answer` or `diff`) says how the verdict was reached and is
not part of the record.

A revalidated result keeps its own `result_id`: the record, not a new result, says that it holds on the new
projection. A new search could reach the same answer with other witnesses and statistics.

## 8. Not in v0

These are later extensions:

- approximate modes and ranking (personalized PageRank, learned graph reasoning)
- GPU execution and alternative backends (to be introduced after profiling, behind the conformance suite)
- generic AND/hypergraph execution beyond `support`'s two rules (hyperedges, weighted or counted support)
- property filters on values
- traversal of candidate relations: a combined view needs its own contract, one that keeps candidate identity in
  every edge and witness

Text retrieval is outside this contract. The optional `graph_runtime.retrieval` module ([RETRIEVAL.md](RETRIEVAL.md))
ranks text items that name records. A consumer passes the nodes its hits name to a request as ordinary node IDs, and
can bind the index's `index_id` into the projection, so that results name the index and go stale with it. Its hits
are not results of this contract and carry no `result_id`.

Reaction-specific feasibility, kinetics and every other scientific interpretation remain with the applications. A
path through a species-connectivity graph does not certify that the reactants, quantities, sites or kinetic
conditions are satisfied.
