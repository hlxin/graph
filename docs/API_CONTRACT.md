# GRAPH contract `graph-runtime/v0.2`

This is the contract the runtime implements (`src/graph_runtime`); [DESIGN.md](DESIGN.md) gives the boundary it
serves. The JSON Schema of every record is `src/graph_runtime/schemas/graph-runtime-v0.schema.json` (`graph_runtime.json_schema`).

Every operation binds an immutable **projection** and a normalized **request**, and returns a **result**. The
runtime is exact-only and runs in process, on the CPU, with the standard library.

Revision `v0.1` tightened transport validation, ordered node-set answers by ascending ID, counted source search
states consistently, and required costs only for edges actually in scope. Revision `v0.2`, in development, adds:

- type and relation hierarchies (section 1)
- the `pattern` operation: tree patterns, enumerated without dead ends and counted without listing (section 4)
- the `support` operation: what loses all or some of its support when nodes fail, under AND/OR rules, certified on
  request by both least fixpoints' ranks (section 4)
- certificates: complete answers that prove their negatives, optima and lower bounds against the records without a
  second search, and closure checks of complete reachability and impact answers (sections 4 and 6)
- revalidation: whether a cached result still holds on an edited projection, decided from its answer and
  certificate (section 7)
- the `bottleneck_path` operation: the path whose weakest edge is strongest, or whose highest edge is lowest, with
  the fewest hops among those, certified by a threshold closure and the layers below the path (section 4)
- the `regular_path` operation: walks whose hops match a regular expression of steps (regular path queries), one
  with the fewest hops or every node reached, certified by the closed product states of the expression's automaton
  (section 4)
- the `derivation` operation: the best derivation of each target in an AND/OR graph (a node of an `all_of` type needs
  every input, any other node one), for a sum of costs, the least highest cost or the greatest lowest, with a
  derivation as witness, certified by values no rule improves on (section 4)
- cycles in patterns: a step may close a cycle between bound variables, evaluated by a generic (worst-case optimal)
  join (section 4)
- the `personalized_pagerank` operation: a relevance ranking around chosen nodes in which every number is proven:
  each listed node carries an interval that contains its personalized PageRank, the top of the ranking is certified
  where intervals separate, and the certificate (the estimates) is checked by recomputing the residuals exactly
  (section 4); its push counts in units the sources divide
- periodic projections: one cell of an infinite periodic graph, with image shifts on its edges, searched by
  reachability and dependency impact without lifting it (section 1), and the `periodicity` operation: each
  component's translation lattice (rank, Hermite normal form basis, interpenetration), witnessed (section 4)
- the `components` operation: weak or strong components, strong ones flagged initial, terminal and cyclic, certified
  by spanning trees and an ordered, closed partition (section 4)
- the `biconnectivity` operation: articulation points (with the pieces each leaves), bridges and blocks, certified by
  the depth-first forest from which a checker recomputes every low point (section 4)
- the `max_flow` operation: a maximum flow and a minimum cut that prove each other (edge- and node-disjoint paths
  with unit capacities), certified by the cut's source side (section 4)
- the `matching` operation: a maximum matching of a left set to partners outside it, certified by König's vertex
  cover (section 4)
- the `dominators` operation: the nodes every walk from the sources to a node passes through (its dominator tree, or
  each target's chain), certified by a low-high order of the tree, checked arc by arc (section 4)
- the `critical_path` operation: the canonical topological order of an acyclic graph, its makespan and a critical
  path, every node's earliest and latest time and slack, or a cycle; certified by the order with earliest times
  (section 4)
- the `d_separation` operation: whether a causal graph makes two node sets independent given a third, with an active
  trail as witness or a Bayes-ball certificate (section 4)
- the `incidence` operation: the exact rank of the matrix of edge coefficients between row and column nodes, and
  canonical bases of its kernels (a reaction network's conservation laws and steady combinations), certified by a
  nonsingular minor and an independent kernel basis (section 4)
- negative costs for `shortest_path` (requests that gave one were `unsupported` before): Bellman-Ford-Moore with
  subtree disassembly over the nodes between the sources and the targets, a negative cycle on the way reported with
  three walks that prove it, and an optimum proven by feasible potentials; answers to non-negative costs are unchanged
  (section 4)
- injective patterns (`injective: true`): distinct variables bind distinct nodes and distinct steps link distinct
  edges (subgraph monomorphism: substructure search), enumerated over the semi-join-reduced candidates; every pattern
  request now states `injective`, so pattern requests made before have other `request_id`s and `result_id`s, with the
  same answers (section 4)
- the `min_cost_flow` operation: a given amount (or as much as possible) from the sources to the targets at least
  cost, by successive shortest paths with potentials, proven by the residual potentials and, for a maximum flow, a cut
  (section 4)
- the `k_shortest_paths` operation: the first k simple paths from a source to a target in the order of (cost, hops,
  start node and edge IDs), by Yen's algorithm, each a checked witness, and with `certificate: true` the ranking
  proven by potentials and the walks that leave the listed paths' prefixes, so that such answers are revalidated;
  every k shortest paths request now states `certificate`, so requests made before have other `request_id`s and
  `result_id`s, with the same paths (section 4)
- the `spanning_forest` operation: a minimum (or maximum) spanning forest by Kruskal's algorithm in the order of
  (weight, edge ID), unique in that order, which proves itself against the records by the cycle property (section 4)
- the `cycle_basis` operation: a minimum cycle basis (ring perception: the smallest set of smallest rings, for every
  link 1) by de Pina's algorithm, independent by construction and proven minimum by its witnesses, whose lightest
  odd cycles a checker recomputes; an interrupted search lists a proven prefix (section 4)
- costs for `matching`: among the largest matchings, one of least total cost (the assignment problem), by successive
  shortest paths with potentials, proven by König's cover and the residual network's potentials; every matching
  request now states `costs`, so matching requests made before have other `request_id`s and `result_id`s, with the
  same answers (section 4)
- counting patterns with cycles without listing their rows: variable elimination along a tree decomposition (FAQ's
  sum of products), a generic join inside each bag; `answer: rows` lists rows only up to `max_results` and counts the
  rest. Totals and rows are unchanged; the expansions spent, and so the `result_id`s of cyclic pattern requests, are
  not (section 4)
- the `reachability_pairs` operation: whether each of many sources reaches its target, from a 2-hop index of the
  projection and the scope (pruned landmark labelling) built once and kept on the projection, every request charged
  its full cost so that a kept index changes nothing but the time; a simple path for each reachable pair, and on
  request the closure of each unreachable pair's source (section 4)
- the `datalog` operation: a positive, range-restricted Datalog program over the projection's records (`node`, `type`
  and `edge` as input predicates, with the hierarchies), evaluated semi-naively to its least model; every fact with a
  derivation of strictly increasing ranks, and on request the whole model as a certificate a checker proves closed
  under every rule (section 4)
- the `canonical_form` and `isomorphism` operations: a node set's induced subgraph put in canonical order by
  individualization and refinement with automorphism pruning, its encoding checked as a relabeling; and whether two
  node sets' graphs are isomorphic, proven by node and edge bijections, or not, proven by other counts or color
  refinement histograms when those separate them (section 4)
- the `flux_modes` operation: the elementary flux modes of a reaction network's stoichiometric matrix (as
  `incidence` reads it), by exact double description, each mode proven elementary by a nonsingular minor of its
  support (section 4)

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

**Periodic projections.** A crystal, a slab or a polymer is an infinite graph that repeats: the derived graph of a
finite quotient graph whose edges carry integer image shifts (a voltage graph; the quotient graph of a crystal net).
`PeriodicProjection.build(nodes=..., edges=..., dimensions=None, periodic=None, ...)` holds one cell: the nodes and
edges as a projection takes them, each edge with a `shift`, a list of `dimensions` integers (by default the shifts'
length), and `periodic`, the axes it repeats along (by default all; along any other, shifts and images are 0). It
stands for the infinite graph whose nodes are the images `<node>@<k1>,...,<kd>` of the cell's nodes, for every integer
vector k, written in plain decimal, and whose edges are the images of its edges: edge e from u to v with shift s joins
`u@k` to `v@(k + s)`, and that image is `<e>@<k>`, named by its source's image.

- **Requests and answers** name images by these IDs, and every rule of the contract applies to the infinite graph,
  canonical order included (by image IDs). A finite lift that holds every image a search reaches gives the same
  answer, witnesses and counters.
- **Searches read only the images they reach.** No box of images is built first.
- **Operations.** Reachability, dependency impact and periodicity (section 4) run on a periodic projection; the
  other operations are `unsupported` (`unsupported_operation`), and periodicity runs on nothing else. A search with no
  depth bound that keeps finding images ends at a limit, interrupted: the infinite graph never yields a complete
  unbounded answer, unless the component is finite.
- **Identity** is computed as a projection's, over a header that adds `kind` `graph_runtime.periodic_projection`,
  `dimensions` and `periodic`, and edge lines `[id, source, target, relation, [shift]]`. `to_dict()` and `from_dict()`
  write and read snapshots (the `periodic_projection` schema).
- **Checking.** `verify_result` checks a periodic result against the cell's records (edges with `shift`, or
  `dimensions` given), image by image. Revalidation does not take periodic projections.

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
| `shortest_path` | `sources`, `targets`, `scope`, `costs`: `{"by_relation": {relation: cost}}` or `{"by_edge": {edge: cost}}`, of any sign, `certificate` (default false) | `max_expansions` |
| `dependency_impact` | `changed` (node IDs), `scope` (the direction in which a change propagates), `max_depth` | `max_expansions`, `max_results` |
| `pattern` | `match` (`{"var", "node_types", "nodes"}`, at least one constraint), `steps` (each `{"from", "var", "scope", "node_types", "nodes", "edges", "edge_var", "optional"}`, or, to close a cycle, `{"from", "to", "scope", "edges", "edge_var"}`), `answer` (`rows`, `count` or `exists`; default `rows`), `injective` (default false) | `max_expansions`, `max_results` |
| `support` | `failed` (node IDs), `scope` (from a node to what supports it), `all_of` (node types that need every support; null: every node needs one), `certificate` (default false) | `max_expansions`, `max_results` |
| `bottleneck_path` | `sources`, `targets`, `scope`, `weights` (as costs, but any finite number), `objective` (`max_min`, the default, or `min_max`), `certificate` (default false) | `max_expansions` |
| `regular_path` | `sources`, `path` (a path expression: text, a list or a tree, normalized to the tree), `targets` (null: any node), `answer` (`witness`, the default, or `reached`), `certificate` (default false) | `max_expansions`, `max_results` |
| `personalized_pagerank` | `sources`, `scope` (the hops a walk follows), `alpha` (the probability of stopping at each step, strictly between 0 and 1; a number or `p/q`, normalized to `p/q`; default `3/20`), `tolerance` (the residual to reach, the same way; default `1/10000`), `top` (nodes listed; default 20), `certificate` (default false) | `max_expansions` |
| `periodicity` | `nodes` (images of a periodic projection's nodes), `scope` (every relation in both directions: it is followed both ways) | `max_expansions` |
| `components` | `scope`, `connectivity` (`weak`, the default: hops followed either way; `strong`: a walk each way), `nodes` (only the components holding these; null: every component), `min_size` (the fewest nodes a listed component has; default 1), `certificate` (default false) | `max_expansions`, `max_results` (components listed) |
| `biconnectivity` | `scope` (each edge in it, in either direction, a link between its ends), `nodes` (only the components holding these; null: all), `blocks` (list the blocks too; default false), `certificate` (default false) | `max_expansions` |
| `max_flow` | `sources`, `targets` (disjoint from the sources), `scope`, `capacities` (as costs, non-negative; null: every edge 1), `node_capacity` (what each node other than the sources and targets passes; null: no limit), `certificate` (default false) | `max_expansions` |
| `matching` | `left` (`{"node_types", "nodes"}`, at least one: the nodes to pair), `scope` (from a left node to its candidate partners), `costs` (as weights: any finite numbers; null: none), `certificate` (default false) | `max_expansions` |
| `dominators` | `sources`, `scope`, `targets` (null: every node reached, with its immediate dominator; else each target's dominators), `certificate` (default false) | `max_expansions` |
| `critical_path` | `scope` (a hop from u to v: u comes first), `sources` (only what they reach; null: every node), `costs` (as for shortest paths; null: every hop takes 1), `answer` (`path`, the default: the makespan and a critical path; `schedule`: every node's times too), `certificate` (default false) | `max_expansions` |
| `d_separation` | `x`, `y`, `given` (default none; the three disjoint), `scope` (a hop from u to v: u is a parent, a direct cause, of v), `certificate` (default false) | `max_expansions` |
| `incidence` | `rows`, `columns` (`{"node_types", "nodes"}` each, at least one constraint, disjoint), `scope`, `coefficients` (as weights: `{"by_relation": ...}` or `{"by_edge": ...}`, any finite numbers), `kernels` (a subset of `left` and `right`; default none), `certificate` (default false) | `max_expansions` |
| `min_cost_flow` | `sources`, `targets` (disjoint from the sources), `scope`, `capacities` (as for `max_flow`; null: every edge 1), `costs` (as for shortest paths, non-negative), `value` (how much to send, as a capacity; null: as much as possible), `certificate` (default false) | `max_expansions` |
| `k_shortest_paths` | `sources`, `targets`, `scope`, `costs` (as for shortest paths, non-negative; null: every hop 1), `k` (1 to 1,000,000; default 10), `certificate` | `max_expansions` |
| `flux_modes` | `rows` (species), `columns` (reactions), `scope`, `coefficients` (all as for `incidence`), `reversible` (reactions, column nodes, that may run backwards; default none), `certificate` (default false) | `max_expansions`, `max_results` |
| `canonical_form` | `nodes` (the induced subgraph's), `scope` (the edges that count, by relation, with their own direction), `directed` (default true), `node_colors` (`types`, the default: each node's types with their ancestors; or `none`) | `max_expansions` |
| `isomorphism` | `left`, `right` (two node sets, which may overlap), `scope`, `directed`, `node_colors` (as for `canonical_form`), `certificate` (default false) | `max_expansions` |
| `datalog` | `program` (rules `{"id", "head", "body"}`, atoms `{"predicate", "args"}`; a term `"?name"` is a variable, any other text, or `{"const": text}`, a constant), `query` (an atom of a derived predicate), `answer` (`rows`, `count` or `exists`; default `rows`), `derivations` (default true), `certificate` (default false) | `max_expansions`, `max_results` |
| `reachability_pairs` | `pairs` (`[{"source", "target"}]`, a pair `[source, target]` read as one; sorted and de-duplicated), `scope`, `witnesses` (default true), `certificate` (default false) | `max_expansions` |
| `cycle_basis` | `scope` (each edge in it a link between its ends, either way), `weights` (as costs: non-negative; null: every link 1), `certificate` (default false) | `max_expansions` |
| `spanning_forest` | `scope` (each edge in it a link between its ends, either way), `weights` (as weights: any finite numbers; null: every link 1), `objective` (`min`, the default, or `max`) | `max_expansions` |
| `derivation` | `targets`, `scope` (from a node to what it needs), `all_of` (node types that need every input; null: every node needs one), `grounds` (nodes given; null: every node with no input that needs one), `costs` (as for shortest paths when `objective` is `min_sum`, any finite numbers otherwise; null: every hop costs 1), `objective` (`min_sum`, the default, `min_max` or `max_min`), `certificate` (default false) | `max_expansions` |

Defaults: `max_expansions` 1,000,000 and `max_results` 100,000. Every limit is a non-negative integer, and the
normalized request always states it.

**Costs.** A cost is an integer of any size or a finite float, never negative except a shortest path's (a negative
one elsewhere is `unsupported`, `negative_cost`). When every cost is an integer,
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

**Negative costs.** When the request gives any negative cost (in its table, whether or not the edge is in scope), the
answer is a least-cost walk, and the search is Bellman-Ford-Moore's, first in first out from the sources in
ascending ID, with Tarjan's subtree disassembly (when a node's cost falls, its subtree of the search tree leaves the
tree and the queue; when that subtree holds the node whose hop lowered it, a negative cycle is found). It runs over
the live nodes: those the sources reach that reach a target, found by a breadth-first search forwards and then one
backwards, each node keeping the hop toward a target it was found by. A walk from a source to a target stays among
them, so a negative cycle elsewhere (among the dead ends) changes nothing. Each node read by those two searches, and
each scan, is one expansion. A source that is a target is not answered at cost 0: a walk may cost less. Answers add
`negative_cycle`:

- **An optimum** (`negative_cycle` null): `found` and `optimal` true, the least cost, and as witness the least-cost
  walk with the fewest hops, the first a breadth-first search finds from the sources at cost 0 (in ascending ID)
  over the hops whose cost is the difference of their ends' least costs, in canonical order (a choice that depends
  on the costs alone). The target is the cheapest, ties by node ID. Each node that breadth-first search expands is
  one expansion too: a budget it runs out leaves the answer interrupted, with what the search knew.
- **A negative cycle** on a walk from a source to a target: no walk is cheapest. `found` true, `optimal` false,
  `cost`, `witness` and `certificate` null, and `negative_cycle` `{"lead_in", "cycle", "lead_out"}`: three walks
  (`{"source", "target", "length", "hops"}`, hops as a witness's), from a source to the cycle's start, around the
  cycle (with its `cost`, and `cost_exact` in rational mode, below 0), and from its start to a target by the hops the
  backward search found. The cycle is the one the search closes first: the tree path from the node whose cost fell
  to the node whose hop lowered it, then that hop.
- **A complete negative** keeps its closure certificate.
- **An interrupted** search knows no lower bound (`lower_bound` null) and certifies nothing; its witness, if any, is
  the search tree's path to the cheapest target in the tree, a real walk whose cost is not proven least.

With `certificate: true`, an optimum carries **feasible potentials**: `{"kind": "feasible_potentials", "bound",
"potentials": [{"node", "value"}], "dead_ends": [...]}`, each live node with its least cost, and the dead ends, both
by node ID. Every hop in scope from a listed node leads to a dead end or to a listed node at most its cost above;
every hop from a dead end leads to a dead end; every source is listed at 0 or below, or a dead end; no target is a
dead end, and every listed target is at the bound or above. A walk from a source to a target then never meets a dead
end or an unlisted node, and its cost telescopes to at least its target's value less its source's: at least the
bound, the witness's cost. Its `layers` (`[{"node", "hops"}]`, by node ID) list what the breadth-first search that
chose the witness reaches in fewer hops than it, each with its fewest hops, over the tight hops (a hop in scope from a
listed node to a listed node whose value is exactly its cost above): every source listed at 0 is at 0 hops, every
tight hop from a node below the witness's length less one leads to a node listed at most one hop further, and the
witness's target is not listed. A walk to that target at the bound starts at a source listed at 0 and uses tight hops
only (its cost telescopes with no slack), so it has no fewer hops than the witness. The `potentials` kind, whose
unlisted nodes sit at the bound, proves nothing when a cost is negative, and a checker rejects it then.

**Pattern.** Matches a pattern of variables and steps, SCALE's query plans in generic form, trees and cycles:

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
- **Cycles.** A step with `to` instead of `var` closes a cycle: it binds no variable, and needs a hop in its scope
  (through its edges, when it names them) from the node of `from` to the node of `to`, two variables bound before it
  by the match or by required steps (the same one, for a loop). It is never optional and constrains no node; its link
  is the hop's edge, one row per parallel edge, in canonical order. Corroboration and contradiction motifs (two
  supports of a claim from one dataset; a triangle of citations) are such patterns. A tree step keeps its form, so
  tree patterns keep their request and result IDs.

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

A pattern with a cycle has no full reducer. After the tree's semi-join reduction (its closing steps aside), each
closing step is indexed over the kept candidates: from each candidate of its `from` variable (one expansion each), its
hops landing on a candidate of its `to` variable, by that node. Rows are then built by a generic join (the worst-case
optimal join of Ngo, Porat, Ré and Rudra; Veldhuizen's Leapfrog Triejoin): when a step binds the later of a closing
step's two variables, its candidates are intersected with what every such closing step allows, iterating the smallest
of the sets and keeping the canonical order of the rest; each candidate tried, and each closing link, is one
expansion. `answer: rows` lists rows this way up to `max_results`. When every row fits, `total` is the number listed;
when one more turns up, the listing stops (an `output_limit` interruption) and `total` is counted without listing.

Counting without listing is a sum of products over the required variables (FAQ, Abo Khamis, Ngo and Rudra, 2016),
which `answer: count` and `exists` use directly. Each required step is a factor of its two variables, valued by the
number of kept hops between their nodes (a closing step from a variable to itself, by its loops), and each optional
branch folds into its required variable as the number of ways it continues, as a tree's total does; factors of the
same variables multiply into one.

The elimination order comes first, from the pattern alone, along a tree decomposition: a weighted min-fill order (the
variable whose neighbours need the fewest new links, each weighted by the product of its ends' candidates; then the
least sum of the neighbours' candidate counts in bits; then the earliest variable). A variable with more than sixteen
neighbours (a hub) goes after every other, by that sum alone, without its fill. An order that needs a bag of more than
32 neighbours (a pattern too dense to decompose narrowly) is not used: the rows are counted by listing them, as above.
Then each variable in turn: the factors that hold it, and those within its neighbours (looked up by scope when it has
at most ten), are joined by a generic join and the variable is summed out into one factor of the neighbours (the last
bag sums out everything). The join binds first the variable most linked to those bound (none, at first), then the one
with the fewest candidates, then the earliest; each binding intersects its factors' sets from the smallest.

The intersection and each value it yields are one expansion each. The rest of the work is charged too, before it is
done:

- the setup: one, plus one per candidate of each variable; then each loop (a closing step from a variable to itself)
  drops the candidates it misses, before any product, for one plus one per candidate its variable still has; then
  each step, last first, one, plus, for a loop or an optional step, one per candidate its variable still has;
- the order: one per required variable and one per variable of each factor, then for each fill computed, one plus one
  per pair of the variable's neighbours, and for each elimination, one plus the square of its number of neighbours;
  except for the last, for each link the clique of its neighbours adds, the smaller of its two ends' numbers of
  neighbours (counted with the clique), and one per variable whose weight is recomputed;
- each join: one per non-empty subset of the neighbours looked up, and, before the join starts, one per variable of
  its bag and one per variable of each factor.

A triangle costs the join of its triangles, a longer cycle the paths around it, not the rows: closed walks of length
four in the complete bipartite graph K(60, 60), 26 million rows, count with fewer than a million expansions.

An interruption while listing keeps the rows found so far (a prefix of the canonical order) and leaves `total` null,
`exists` true when a row was found; an interruption while counting leaves `total` null (and, with no row listed,
`exists` null). `coverage` gives a closing step's indexed hops.

**Injective patterns.** With `injective: true`, distinct variables bind distinct nodes and distinct steps link
distinct edges: subgraph monomorphism, the substructure search of chemistry (a motif, a catalytic site, a ring), which
counts every binding (a pattern with automorphisms matches one subgraph several times). A loop on one variable stays
a loop. The rows are those of the same pattern without `injective` that use no node or edge twice, in the same
canonical order. Distinctness is global, so no count along the kept hops respects it: an injective pattern, tree or
not, is enumerated as a cycle's is, over the same semi-join-reduced candidates (a match with distinct nodes is a
match), skipping a node another variable holds and an edge another step links. `total` is known when the enumeration
ends. The problem is NP-complete: the expansion budget bounds it, and an interruption leaves `total` null and claims
no absence. An injective pattern has no optional step (`unsupported`, `unsupported_mode`): which node an optional step
would bind among those left is not yet defined.

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
"reached", "layers"}`) lists what the sources reach by hops strictly better than the threshold, closed under those
hops, with no target. It proves that no path is strictly better than the threshold: the value, or the bound. For a
found path, `layers` (`[{"node", "hops"}]`, by node ID; null when interrupted) lists what the second step's search
reaches in fewer hops than the witness, each with its fewest hops over the hops at least as good as the value: every
source at 0, every such hop from a node below the witness's length less one leads to a node listed at most one hop
further, and no target is listed. A path at the value with fewer hops would stay among the listed nodes, so the
witness has the fewest hops among the best paths. A **closure**
proves a complete negative, as for shortest paths.

**Regular path.** Finds walks whose hops match a regular expression of steps: the regular path queries of
Mendelzon and Wood (1995), which SPARQL 1.1 property paths and GQL path patterns also write. One request replaces a
family of typed paths (any number of repetitions, alternatives) and of reachability questions (a scope that changes
along the walk).

- **Expressions.** A `step` matches one hop: an edge in its scope (with the relation hierarchy), followed in its
  direction, landing on a node of its `node_types` (any node when null). `seq` matches its items one after another,
  `alt` any one of them, and `repeat` its item `min` to `max` times (`max` null: no bound). The normal form is a
  tree: `{"op": "step", "scope", "node_types"}`, `{"op": "seq" | "alt", "items"}` (at least two; nested ones
  flattened; an alternation de-duplicated and sorted by canonical JSON, so `A|B` and `B|A` are one request) and
  `{"op": "repeat", "item", "min", "max"}` (never `{1, 1}`). A request may give the tree, a list (a sequence) or
  text, also for any part of a tree: `CITES+/^SUPPORTS[Claim]` is one or more CITES hops forward, then one SUPPORTS
  hop in reverse landing on a Claim. The text syntax is `|` (alternation), `/` (sequence), postfix `*`, `+`, `?`,
  `{m}`, `{m,}` and `{m,n}`, parentheses, `^` before a relation (reverse) and `[T1,T2]` after it (node types); a name
  is letters, digits and `_ . : -`, or any text between backquotes. An expression written out (below) has at most
  4,096 steps.
- **The automaton.** Each expression has a Glushkov automaton, without empty moves. State 0 is the start; state i
  (from 1) means the i-th step was the last hop matched. Steps are numbered in the order they are written once
  bounded repetitions are written out: `e{m,n}` is m copies of `e` then n - m optional copies, `e{m,}` (m > 0) is
  m - 1 copies then `e+`, and `e{0,}` is `e*`. The states a hop can move to from state q are those whose step may come
  next; a walk matches when it ends in an accepting state (a step that may come last, or the start when the
  expression matches the empty walk). States appear in answers and certificates, so this numbering is part of the
  contract.
- **Search.** Breadth-first over product states (node, automaton state), from every source at state 0, sources in
  ascending ID. A state is expanded once; a state from which no step may follow is not expanded. From a node, its
  hops in scope of any step that may follow are read once, in the canonical order; each hop moves to every state
  whose step it matches, in ascending state order. The first discovery of a product state has its fewest hops.
- **`answer: witness`** finds one walk with the fewest hops from a source to a target (any node when `targets` is
  null), stopping at the first accepting product state at a target. When the expression matches the empty walk, a
  source that is a target is the answer with no hop. Answer: `{"found", "witness": {"source", "target", "length",
  "hops": [{"edge", "relation", "direction", "from", "to", "state"}]}, "certificate"}`; each hop names the state it
  moves to.
- **`answer: reached`** lists every node (within `targets`, when given) at which a matching walk from a source
  ends, with its fewest hops and the accepting state that gives them. `max_results` bounds the nodes listed; more is
  an `output_limit` interruption, the listed ones chosen in discovery order. `tree` holds the product states on the
  listed nodes' witnesses, by node then state, each with the hop it was first reached by (`via`: `{"edge",
  "relation", "direction", "from", "from_state"}`, null for a source at the start), and `result.path_to(node)`
  rebuilds a witness from it. Without a certificate, a search with targets stops once every target is listed.
  Answer: `{"reached": [{"node", "length", "state"}], "count", "tree": [{"node", "state", "length", "via"}],
  "certificate"}`.

With `certificate: true`, a complete answer that claims more than its witnesses carries its proof: `{"kind":
"product_states", "bound", "states": [{"node", "state", "length"}]}`, the product states found with fewer than
`bound` hops (all of them when `bound` is null), by state then node ID, each with its fewest hops. Every source is
listed at state 0 with no hop. From every listed state with fewer than `bound` - 1 hops, every move leads to a listed
state at most one hop further, and no listed state ends a match at a target, except at a node a `reached` answer
lists no further away. By induction on the hops, no matching walk to a target is shorter than `bound` (none exists
when `bound` is null), and every listed fewest-hop count is exact. A found witness of length L carries `bound` L; a
complete negative and a complete `reached` answer carry `bound` null; a witness with no hop and an interrupted answer
carry none.

**Derivation.** The best derivation of each target in an AND/OR graph: Knuth's generalization of Dijkstra's
algorithm (1977), the lightest derivation problem of Felzenszwalb and McAllester (2007). A hop in `scope` leads from
a node to one of its inputs, for example from a species to a reaction that makes it, and from a reaction to each of
its reactants. In a reaction network it answers: from these feedstocks, what is the cheapest way to make each target,
the way whose highest barrier is lowest, or the way whose slowest step is fastest.

- **Rules.** A ground (`grounds`; by default, a node with no input that needs one) is given. A node of an `all_of`
  type (through the hierarchy) is derived from every one of its distinct inputs, each through one hop; it is derived
  from nothing when it has no input. Any other node is derived from one input through one hop. A derivation is a
  finite tree, so support in a circle derives nothing.
- **Values.** `min_sum`: a derivation's value is the sum of its hops' costs, every use counted (a tree, not a DAG),
  lower is better, and a given node is worth 0; costs are non-negative and exact as a shortest path's, with
  `value_exact` in rational mode. `min_max`: the highest cost among its hops, lower is better. `max_min`: the lowest,
  higher is better. For the last two, costs are any finite numbers, compared and never added, and a given node is
  worth null (no hop). Of parallel hops to one input, the best is used (the first in canonical order among equals).
- **Search.** First the region: the targets and, transitively, their inputs, stopping at grounds; each node read once
  is one expansion. Then nodes are settled best value first, ties by node ID, each settle one expansion: grounds, and
  `all_of` nodes without inputs, start at the identity; a settled node offers its value to the nodes that use it; a
  one-input node takes a strictly better offer, and an `all_of` node is ready when its last input settles. A settled
  value is final and the best there is (every combination is at least as bad as its parts). The search ends when every
  target is settled or nothing is left to settle.
- **Answer.** `derived` lists each derived target with its best value; `underived` (complete answers only) the
  targets with no derivation; `bound` (interrupted answers only): no target the answer does not list has a derivation
  better than it (null: nothing is known). `derivation` lists every node the derived targets' derivations use, by node
  ID: its `rule` (`given`, `all` or `one`), its value, and the hops it `uses` (`{"edge", "relation", "direction",
  "from", "to", "cost"}`), in canonical order. Each value follows from the node's uses alone: an `all` node combines
  its uses in the order listed, keeping the earlier on a tie. Every listed node's value is its own best. Answer:
  `{"derived": [{"node", "value"}], "underived", ["bound"], "derivation": [{"node", "rule", "value", "uses"}],
  "certificate"}`.

With `certificate: true`, an answer carries its proof whenever it claims more than its derivation: `{"kind":
"values", "bound", "values": [{"node", "value"}]}`, the settled nodes' values, by node ID; every other node is
worth `bound` (null: no derivation at all). At every node of the targets' region, no rule gives a value better than
the node's: a ground's identity, an `all_of` node's combination of its inputs' values each through its best hop, and,
for any other node, each hop's cost with its input's value. By induction on derivation trees, no derivation beats
these values; the derivation shows each derived value is reached, so it is the best, and a target worth a null bound
has no derivation. A complete answer carries one; an interrupted answer only when its bound says something.

**Personalized PageRank.** A relevance ranking around the sources (Page and Brin; Jeh and Widom), the retrieval step
of graph-based retrieval such as HippoRAG, with every number proven. A walk starts at a source chosen uniformly; at
each step it stops with probability `alpha`, or else follows a hop in scope chosen uniformly (each parallel edge
counts); a walk that must move from a node with no hop in scope is lost. A node's personalized PageRank, pi(v), is the
probability that the walk stops there.

- **Push.** The local push of Andersen, Chung and Lang (2006) keeps estimates p and residuals r with the invariant
  `p (I - (1 - alpha) P) / alpha + r = s` (P the walk, s the sources' distribution), so pi = p + sum_u r(u) pi_u. It
  runs in exact integer units of 1/N, N the least multiple of 2^64 that the number of sources divides (so each
  source starts with exactly its share): rounds push, in ascending node order, every node whose residual divides into a
  whole move (a multiple of b times its hops, for alpha = a/b), until the total residual R is at most `tolerance` or
  nothing can move. Each push is one expansion. The invariant then holds exactly.
- **Intervals.** Each pi_u is non-negative and gives u at most 1 and every other node at most 1 - alpha, so
  `p(v) <= pi(v) <= p(v) + r(v) + (1 - alpha) (R - r(v))`, a node the push never reached at most (1 - alpha) R. These
  bounds hold whenever the push stops, interrupted or not.
- **Answer.** `ranked`: the `top` nodes by lower bound (ties by node ID), each with `lower` and `upper` as their
  nearest floats and exact `p/q` values (`lower_exact`, `upper_exact`); `certified_top`: the largest m such that the
  first m nodes are proven to be, as a set, the m nodes with the highest PageRank (their lowest lower bound exceeds
  every other node's upper bound); `residual` (and `residual_exact`): R.
- **Certificate.** With `certificate: true`, `{"kind": "estimates", "values": [{"node", "value", "value_exact"}]}`:
  every nonzero estimate, by node ID. A checker recomputes every residual exactly from them through the invariant; when
  none is negative, every interval, the residual and the certified prefix follow. The estimates alone prove the
  answer.

Answer: `{"ranked": [{"node", "lower", "lower_exact", "upper", "upper_exact"}], "count", "certified_top",
"residual", "residual_exact", "certificate"}`.

**Periodicity.** How each requested image's component repeats, on a periodic projection: the classic analysis of a
periodic net through its quotient graph, which crystallography uses to tell molecules, chains, layers and frameworks
apart (Chung, Hahn and Klee, 1984; Delgado-Friedrichs and O'Keeffe, 2003), here exact and witnessed.

- **Tree.** From the image's node, a breadth-first spanning tree over the cell's nodes that the scope reaches (each
  read once, one expansion; hops in canonical order) gives each of them the image the tree reaches, its potential.
- **Lattice.** Every edge among those nodes closes a cycle whose net shift, `p(source) + shift - p(target)`, is a
  translation the component has; these nets generate all of them. `basis` is the lattice's Hermite normal form (row
  style: echelon, positive pivots, entries above a pivot reduced below it), unique for the lattice; `rank` its number
  of rows: 0 a finite component, 1 a chain, 2 a layer, 3 a framework. `copies` is its index among the integer
  vectors of the space it spans: how many translated copies of the component run through the same space (a
  framework's degree of interpenetration). The reduction carries each row's combination of the nets sparsely, and
  each of its row operations is one expansion.
- **Components.** An image belongs to a component when its node is among the component's and its offset from that
  node's image is in the lattice; otherwise it seeds another, translated component (a parallel layer, or an
  interpenetrating copy). Components are listed in order of their first seed.
- **Witnesses.** The tree's hops (named by lifted edge IDs) prove the potentials, `cycles` lists each edge with a
  nonzero net and its net, and `witnesses` writes each basis row as a combination of those nets: each row is a
  translation the component has. Every cycle's net is in the lattice the basis generates, so the basis generates
  exactly the component's translations.

Answer: `{"components": [{"seeds", "nodes": [{"node", "image", "via"}], "rank", "basis", "copies", "cycles":
[{"edge", "net"}], "witnesses": [[{"edge", "times"}]]}], "count"}`. An interrupted analysis (in a tree or in a
reduction) lists the components it finished.

**Components.** How the nodes fall into pieces (Tarjan, 1972). `weak`: two nodes are together when hops in scope,
each followed either way, join them: the pieces a structure or a corpus falls into. `strong`: when a walk of hops leads
from each to the other: feedback loops, circular derivations, the communicating classes of a Markov chain. Every node
is in exactly one component; a node alone is a component of one.

- **Selection.** Every component, or with `nodes` only those holding one of these nodes. `min_size` leaves out
  smaller components (2 lists the nontrivial ones). They are listed in ascending order of their first node, each with
  its `nodes`, `size` and `edges` (the edges in scope with both ends in it, each once). `total` counts the components
  of the selection, whatever their size (the number of pieces, or of linkage classes).
- **Strong flags.** A strong component is `cyclic` when a walk inside it returns to its start (two nodes or more, or a
  hop from a node to itself), `initial` when no hop enters it from outside, `terminal` when none leaves it: a terminal
  component is what a walk that enters it can never leave (a recurrent class of a Markov chain, a terminal linkage
  class of a reaction network).
- **Search.** Breadth-first (weak) or Tarjan's depth-first search (strong), each node one expansion, from the
  selection's nodes in ascending order; a strong search from named nodes explores everything they reach. Linear.
- **Certificate.** With `certificate: true`, `{"kind": "partition", "groups": [{"nodes", "out": [{"node", "via"}],
  "in"}]}`. `groups` holds every component the search finished, strong ones in topological order (no hop leads from a
  group to an earlier one), weak ones in the order found; no hop leads out of the groups, and (weak) no link joins two
  of them. Each group has a spanning tree from its first node: `out`, the hop that reaches each other node (`via` with
  `from`; a weak tree may follow an edge either way); and for strong components `in`, the hop that leads from each
  other node toward the first (`via` with `to`; null for weak ones). The trees prove a group connected; closure and
  order prove nothing outside it belongs with it (a walk that left a strong group could never return to it). So every
  group is a component, listed or not, and `total` is proven with the list.
- **Limits.** An interrupted search lists only components it finished, each exact; `total` is then null. More
  listed components than `max_results` is an `output_limit` interruption.

Answer: `{"components": [{"nodes", "size", "edges", "cyclic", "initial", "terminal"}], "count", "total",
"certificate"}` (the three flags for strong components only).

**Biconnectivity.** Where one failure would split a component (Hopcroft and Tarjan, 1973). Every edge in scope, in
either direction, is a link between its two ends, whichever way it points; parallel edges are distinct links, so two
of them between the same nodes are never bridges; an edge from a node to itself links nothing and is in no block.

- **Answer.** `articulation_points`: each node whose removal splits its component, with `splits`, the number of pieces
  that removal leaves of it (2 or more); `bridges`: each edge whose removal splits its component, with its relation,
  source and target; with `blocks: true`, `blocks`: the maximal pieces without an articulation point, each with its
  `nodes` and `edges` (every two of its edges lie on a common cycle; a bridge is a block of its own), listed by first
  edge; `components`: the connected components searched. With `nodes`, only the components holding them are searched.
- **Search.** One depth-first search from the selection's nodes in ascending order, each node one expansion, reading
  links in canonical order. `low(u)` is the least preorder number a link from u's subtree reaches, other than u's own
  link to its parent (a parallel link to the parent counts). A child c of q with `low(c) >= pre(q)` is separated by
  q, its subtree's links forming a block with the link to c; with `low(c) > pre(q)` that link is a bridge. Linear.
- **Certificate.** With `certificate: true`, `{"kind": "dfs", "forest": [{"node", "pre", "via"}]}`: each searched
  node, by node ID, with its preorder number and the edge to its parent (null for a root; with `nodes`, every root is
  one of them). A checker confirms it is a depth-first forest (each `via` a link to a node numbered earlier, the
  numbers a preorder of the forest, no link between two nodes neither above the other) and recomputes every low point
  from the records; each claim follows.
- **Limits.** An interrupted search lists what it finished: an articulation point once its subtree is done, a bridge
  or a block once the subtree below it is; each is exact. `components` is then null.

Answer: `{"articulation_points": [{"node", "splits"}], "bridges": [{"edge", "relation", "source", "target"}],
"blocks": [{"nodes", "edges"}] or null, "components", "certificate"}`.

**Max flow.** As much as the capacities let through from the sources to the targets (Ford and Fulkerson, 1956), and
what stops more from getting through. Each hop in scope carries at most its edge's capacity; an edge in scope both
ways carries flow either way, up to its capacity each way, and its net flow is reported. With `node_capacity`, every
node other than the sources and targets passes at most that much. With the default capacities (every edge 1) the
value is the number of edge-disjoint paths, and with `node_capacity` 1 the number of internally node-disjoint ones
(Menger, 1927): how many independent lines join the sources to the targets.

- **Search.** Dinic's algorithm (1970): breadth-first levels in the residual graph, then a blocking flow along them,
  until no target is reached. A node split by a node capacity is two residual nodes joined by an arc of that
  capacity. The residual graph is built only where the search reads it. Each node a level search reads, and each
  advance of the flow search, is one expansion. Arithmetic is exact: integer capacities as they are, and when any
  capacity is a float, every capacity as an integer count of the largest power-of-two denominator among them.
- **Answer.** `value` (the flow's size), `optimal` (true exactly when complete), `upper_bound`, `flows`: the net
  flow on each edge that carries any, by edge ID, with the direction it goes; and, when complete, the minimum cut
  nearest the sources: `cut`, the hops from its source side to the rest (each with its capacity), and `cut_nodes`,
  the nodes whose own capacity separates them. They add up to the value. Numbers are integers, or when any capacity
  is a float the nearest float with the exact value beside it (`value_exact`, as `p/q`).
- **Certificate.** With `certificate: true`, `{"kind": "cut", "source_side": [node]}`: the nodes whose outward side
  the final residual graph reaches. A checker confirms the flow (capacities, conservation, node capacities, the
  value leaving the sources) and recomputes the cut from the records: every hop in scope from the source side to a
  node whose inward side is outside it, and every cut node. A flow and a cut of the same value are both optimal
  (max-flow min-cut theorem), so nothing else is searched.
- **Limits.** An interrupted search keeps the flow found so far, valid and never claimed optimal: `value` is a lower
  bound, `upper_bound` the least of what the sources can send and the targets receive, and the cut is null.

Answer: `{"value", "optimal", "upper_bound", "flows": [{"edge", "relation", "direction", "from", "to", "flow"}],
"cut": [{"edge", "relation", "direction", "from", "to", "capacity"}], "cut_nodes": [{"node", "capacity"}],
"certificate"}`.

**Matching.** As many pairs as possible of a node of the `left` set with a node outside it along a hop in scope, no
node in two pairs (Hopcroft and Karp, 1973): distinct assignments, such as samples to instruments or claims to
reviewers. A left node's candidates are its hops in scope to nodes outside the left set; hops between left nodes are
not candidates.

- **Search.** Phases of a breadth-first search from the unpaired left nodes along alternating paths, then augmentation
  along the shortest ones; each left node a phase reads is one expansion. A pair uses the first hop, in canonical
  order, to its partner.
- **Answer.** `pairs` by left node, each with its partner and the hop that pairs them; `size`; `left_count`, how many
  left nodes there are; and, when complete, `optimal` and `deficiency`, the left nodes no maximum matching pairs.
- **Certificate.** With `certificate: true` on a complete answer, `{"kind": "cover", "nodes": [node]}`: König's
  vertex cover (1931), as many nodes as pairs, touching every candidate hop, so no matching has more pairs. When the
  deficiency is not zero, the uncovered left nodes S and their candidates N(S), all in the cover, have fewer
  candidates than nodes: Hall's condition fails, which is why they cannot all be paired.
- **Limits.** An interrupted search keeps the pairs found so far, a valid matching never claimed maximum.
- **Costs.** With `costs` (as weights: any finite numbers, by relation or by edge), the matching is one of least total
  cost among the largest: the assignment problem (samples to instruments at least total time, catalysts to
  conditions at least total penalty; for a heaviest one, negate the weights). A left node's candidate for a partner
  is its cheapest hop to it (the first in canonical order among equally cheap ones). The search is successive
  shortest paths (Kuhn and Munkres's Hungarian method, with Dijkstra's searches on costs reduced by potentials, after
  Edmonds and Karp and Tomizawa) in the network source -> left node -> partner -> sink: potentials start at 0 for the
  source and the left nodes, at each partner's cheapest incoming cost, and at the least of those for the sink; each
  phase searches from the source over reduced costs (states by distance, then node index; each settled one an
  expansion; a left node read is one too), raises each potential by its node's distance (the sink's for a node not
  settled), and augments along the path to the sink. The answer adds `cost` (and `cost_exact` in rational mode).
  The certificate is `{"kind": "cover_and_potentials", "nodes", "potentials": [{"node", "value"}], "source",
  "sink"}`: König's cover proves the size, and the potentials (of every left node and partner, the source and the
  sink) leave no arc of the residual network costing less than nothing once reduced (source to an unpaired left node,
  a paired one back, a candidate hop not used by a pair, a pair's hop backwards at minus its cost, an unpaired partner
  to the sink, the sink back to a paired one), so no matching of that size is cheaper.

Answer: `{"pairs": [{"left", "right", "edge", "relation", "direction"}], "size", "left_count", "optimal",
"deficiency", "certificate"}`.

**Dominators.** The nodes every walk of hops in scope from the sources to a node passes through (Prosser, 1959; Lowry
and Medlock, 1969): the intermediate every route from a feedstock to a product forms, the evidence every derivation of
a claim rests on, a single point of failure on every route. A node d dominates v when every walk from a source to v
passes through d; v's immediate dominator is its nearest one, and they form a tree whose ancestors of v are v's
dominators. With several sources, a start joined to each of them stands for "some source": a source, and any node no
single node dominates, has none (null).

- **Search.** An iterative depth-first search from the sources in ascending order (each node reached is one
  expansion), then Lengauer and Tarjan's algorithm (1979, the simple version with path compression).
- **Answer.** `reached`: how many nodes the sources reach. Without targets, `tree`: each reached node with its
  immediate dominator, by node ID. With targets, `chains`: for each target, in the request's order, whether it is
  reached and its dominators from the sources toward it (the target excluded).
- **Certificate.** With `certificate: true`, `{"kind": "low_high", "order": [{"node", "idom"}]}`: every reached node
  with its immediate dominator, in a low-high order of the tree (Georgiadis and Tarjan, 2016): a preorder in which
  every node has a hop from its dominator, or a hop from a node before it and one from a node after it that is not its
  descendant (the start counts as before everything and has a hop to each source). A checker confirms that the order
  is a preorder of the tree, that every hop in scope from a listed node leads to a listed node whose dominator is an
  ancestor of the hop's tail (the parent property), and the low-high condition; then the tree is the dominator tree,
  with no dominator computed again. The order is built from two independent spanning trees that the semi-dominator
  computation gives (their Algorithms 8, 7 and 5).
- **Limits.** Dominance is a claim about every walk, so an interrupted search claims nothing: every field is null.

Answer: `{"reached", "tree": [{"node", "idom"}] or null, "chains": [{"target", "reachable", "dominators"}] or null,
"certificate"}`.

**Critical path.** Read as precedence (a hop from u to v: u comes before v, the hop's cost the time between), an
acyclic graph is a schedule (Kelley and Walker's critical path method, 1959): a node's earliest time is its longest
path from a node nothing precedes, the makespan (`cost`) the longest of these, and a critical path one that long; a
node's latest time is the makespan less its longest path to a node nothing follows, and its slack, latest less
earliest, is how far it can slip without delaying the whole. Build systems, workflows, the depth of a derivation or a
provenance chain, reaction steps in sequence.

- **Order.** Kahn's algorithm (1962), taking the smallest node first among those whose predecessors are all placed:
  the lexicographically smallest topological order. Each node scheduled (or, with `sources`, reached) is one
  expansion. Costs are exact; without costs every hop takes 1, and earliest times are layers.
- **Answer.** `acyclic`; when true, `cost` (the makespan) and `path`, a critical path ending at the smallest node whose
  earliest time is the makespan, each hop's predecessor the first in canonical order that attains the maximum; with
  `answer: "schedule"`, `schedule`: every node by ID with `earliest`, `latest` and `slack`. When false, `cycle`: a
  cycle among the nodes no order can place (`hops`), and with `sources` the hops that reach it from one (`reach`).
- **Certificate.** With `certificate: true` on an acyclic answer, `{"kind": "topological", "order": [{"node",
  "earliest"}]}`: the order with each node's earliest time. A checker confirms the order covers what is scheduled,
  every hop between its nodes goes forward, and the times satisfy the longest-path recurrence exactly, whose solution
  in an acyclic graph is unique: the makespan, the critical path and the schedule's latest times follow, hop by hop.
- **Limits.** An interrupted search claims nothing: every field is null.

Answer: `{"acyclic", "cycle": {"hops", "reach"} or null, "cost", "path": {"source", "target", "length", "hops"},
"schedule": [{"node", "earliest", "latest", "slack"}] or null, "certificate"}`.

**d-separation.** Whether a causal graph makes `x` and `y` independent given `given` (Pearl, 1988; Geiger, Verma and
Pearl, 1990). A hop in scope from u to v reads as "u is a parent of v". A trail follows hops either way; it is active
when every collider on it (a node both neighbouring hops point into) is given or has a given descendant, and every
other inner node is not given. `x` and `y` are d-separated when no trail between them is active: then they are
independent given `given` in every distribution the (acyclic) graph models. On a graph with cycles the same test is
computed; its causal reading needs an acyclic one.

- **Search.** Shachter's Bayes-ball (1998), as Koller and Friedman write it: the given nodes and their ancestors
  (each one expansion), then a breadth-first search over states (a node, and whether the trail arrived from a child,
  going up, or from a parent, going down) from each node of `x` as if arrived going up (each state one expansion):
  from a node not given, down to its children, and if it arrived going up, up to its parents; from a node arrived at
  going down that is given or an ancestor of a given node, up to its parents. It stops at the first node of `y`.
- **Answer.** `separated`. When false, `trail`: the active trail from a node of `x` to one of `y`, each step a hop
  (from parent to child) with `travel` `down` or `up`; and `colliders`: each collider on it, in order, with
  `to_given`, a directed path down from it to a given node (empty when it is given).
- **Certificate.** With `certificate: true` on a separated answer, `{"kind": "bayes_ball", "ancestors", "states":
  [{"node", "up", "down"}]}`: the ancestor set used and the visited states. A checker confirms the ancestors hold the
  given nodes and every parent of theirs, the states hold every start and every state a move reaches, and no node of
  `y` is among them: no active trail reaches `y`. A connected answer needs none: its trail is its proof.
- **Limits.** An interrupted search claims nothing.

Answer: `{"separated", "trail": [{"edge", "relation", "direction", "from", "to", "travel"}] or null, "colliders":
[{"node", "to_given"}] or null, "certificate"}`.

**Incidence.** Exact linear algebra of an incidence structure. The matrix N has a row for each row node and a column
for each column node (both in ascending ID); N[i][j] adds the coefficients of the edges in scope joining row i and
column j, whichever way they point. For a reaction network's stoichiometric matrix (species by reactions, consumption
negative, production positive), the rank is the number of independent reactions (with the numbers of complexes and
linkage classes it gives the deficiency of Feinberg's theory, 1987), the left kernel holds the conservation laws
(species weights no reaction changes) and the right kernel the reaction combinations that change nothing. For a Petri
net's incidence matrix, they are the place and transition invariants. Coefficients are exact: integers, or floats
read as their binary values.

- **Search.** The matrix is read from the edges at the row nodes (each row one expansion, each row scaled to
  integers) and brought to row echelon form by exact fraction-free elimination, column by column, the sparsest open
  row taking each pivot: the rank and a nonsingular minor. A kernel comes from back elimination of the echelon form
  of the matrix (right) or of its transpose (left). Each pivot, forward or back, is one expansion.
- **Answer.** `rows`, `columns`, `rank`, and the requested kernels: `left_kernel` (vectors over the rows, `rows -
  rank` of them) and `right_kernel` (over the columns, `columns - rank`), each vector its nonzero entries by node ID.
  Each basis is canonical: the kernel's reduced row echelon basis (one vector per free coordinate, 1 there and 0 at
  the other free ones), each vector scaled by a positive factor to a primitive integer vector. Its form is checked
  without the records: a vector's free coordinate is its last node, where it is positive; the vectors are in
  ascending order of them, and each is zero at the others'. A basis in the kernel of that form is the canonical one
  (the free coordinates are then the possible last nodes of a kernel vector, and they fix each vector).
- **Certificate.** With `certificate: true`, `{"kind": "rank", "minor": {"rows", "columns"}, "kernel"}`: an r by r
  minor of N with a nonzero determinant (rank at least r), and, when the answer lists no kernel, the smaller one as
  `{"side", "vectors"}` (rows - r independent vectors on the left, or columns - r on the right: rank at most r). A
  checker rebuilds N from the records, multiplies every vector by it, sees each basis independent (every vector
  nonzero at a node where the others are zero) and shows the minor nonsingular, by elimination modulo the prime
  2^61 - 1 (a determinant nonzero modulo p is nonzero) or, should that not settle it, by Bareiss's fraction-free
  elimination: the rank, and the kernels' completeness, follow.
- **Limits.** An interrupted reduction claims nothing: rank, kernels and certificate are null.

Answer: `{"rows", "columns", "rank", "left_kernel": [[{"node", "value"}]], "right_kernel": [...], "certificate"}`.

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
- **Certificate.** With `certificate: true`, a complete answer carries `{"kind": "fixpoints", "nodes": [{"node",
  "before", "after"}]}`: every node the analysis read (the region and everything it rests on), by node ID, with its
  rank in each least fixpoint, or null where it does not stand. A ground ranks 0; any other standing node ranks one
  more than its lowest standing support, an `all_of` node one more than its highest, so the ranks depend on the
  fixpoint alone. A checker confirms each fixpoint rule by rule. A ranked node is a ground or meets its rule on
  supports of lower rank, so by induction on rank it stands. An unranked node is failed (after the failure), or has
  supports and fails its rule on unranked ones (all of them, or one for an `all_of` node); such a set is unfounded,
  so none of it stands. The certificate must classify the region and every support of it; nothing outside the
  region changes. Both fixpoints, and from them every listed node, count, standing support and cause, are then
  proven without recomputing either one.

Answer: `{"failed", "lost": [{"node", "supports", "standing", "failing": [hop], "causes"}], "weakened": [...],
"counts": {"lost", "weakened", "ungrounded"}, "certificate"}`.

**Spanning forest.** The lightest set of links that keeps every connected piece connected (Kruskal, 1956). Each edge in
scope is a link between its ends, whichever way the scope follows it, as for biconnectivity; a loop never joins
anything. The forest holds one tree per piece of the projection's nodes (a node with no link is a tree of its own),
with the piece's node count less one links. Kruskal's algorithm takes the links in the order of (weight, edge ID), or
of weight descending then edge ID for `objective: max`, and keeps each that joins two trees. Weights are compared
exactly (floats as exact rationals, any sign), so the forest is the one least in that strict order: unique, of least
(or greatest) total weight, ties broken by edge ID. Without weights every link weighs 1, and the forest is the first
in edge ID order. Reading each node's links is one expansion; an interrupted search claims nothing (every field null).
Answer: `{"edges", "count", "trees", "weight", ["weight_exact"]}`: the forest's edges in ascending ID, their number,
the number of trees (nodes less edges) and the exact total, as costs are written (the nearest float and `p/q` in
rational mode).

The answer proves itself, with no certificate (Tarjan's cycle property): links in scope with no cycle among them,
whose trees join the ends of every other link in scope (so they span each piece), and in which every link left out
comes after every edge of the tree path between its ends in that order, are the forest Kruskal's algorithm keeps.
A checker finds those paths' last edges by binary lifting, in O((n + m) log n).

**Minimum-cost flow.** Sends `value` (or, when null, as much as the capacities allow) from the sources to the targets
at least total cost: supply chains, the cheapest routes for a total throughput. Each hop in scope carries at most its
edge's capacity at its edge's cost per unit (an edge in scope both ways carries flow either way, up to its capacity
each way); a loop carries nothing. Capacities, costs and the value are exact (floats as exact rationals). Successive
shortest paths (Jewell, 1958; Busacker and Gowen, 1960; with potentials, Edmonds and Karp, 1972, and Tomizawa,
1971): a virtual source feeds the sources and a virtual sink drains the targets; each phase is Dijkstra's search
from the virtual source over the residual network on costs reduced by potentials (states by distance, then node
index), an augmentation along the cheapest path by all it carries or all that is still wanted, and the potentials
raised by the search's distances (the sink's for a node not settled). Reading each node's hops is one expansion, and
so is each settled state. The flow of each value reached is a cheapest one of that value. When the value asked for
cannot be sent, the answer is a maximum flow at least cost, `feasible` false.

Answer: `{"value", ["value_exact"], "feasible", "cost", ["cost_exact"], "optimal", "flows": [{"edge", "relation",
"direction", "from", "to", "flow", ["flow_exact"]}], "certificate"}`, flows by (edge, direction). An interrupted search
keeps the flow sent, valid but never claimed cheapest or largest (`feasible` null, `optimal` false). With
`certificate: true`, a complete answer carries `{"kind": "potentials", "potentials": [{"node", "value"}], "source",
"sink", "source_side"}`: potentials for every node with a hop in scope (and every source and target) and for the
virtual source and sink. Every arc of the residual network costs nothing negative once reduced by them (a hop with
room left; a hop with flow, backwards at minus its cost; the virtual source to every source, and back from one that
sends; every target to the virtual sink, and back from one that receives), so no flow of the same value is cheaper.
When the flow is a maximum one (no value asked, or `feasible` false), `source_side` is a minimum cut's: every source
and no target, every hop out of it full and every hop into it empty, so its capacity is the value; otherwise null.

**Elementary flux modes.** The minimal pathways of a reaction network at steady state: a network's indivisible routes,
from which every steady flux is a non-negative combination (Schuster and Hilgetag, 1994). The stoichiometric matrix S
is `incidence`'s: a row per species, a column per reaction, each entry the sum of the coefficients of the edges in
scope joining them, either way. A flux v is feasible when S v = 0 and every irreversible reaction carries v ≥ 0; an
elementary mode is a feasible v ≠ 0 whose support (its reactions) no other feasible vector's support lies strictly
inside. Modes are listed as primitive integer vectors by reaction, ordered by support size, then by their reactions
and values in ID order; both directions of a cycle of reversible reactions are listed, each a mode.

Enumeration is exact double description in its nullspace form (Wagner, 2004; Gagneur and Klamt, 2004; Terzer and
Stelling, 2008): each reversible reaction is split into a forward and a reverse column; the kernel's canonical basis
(as `incidence` gives it) spans the vectors non-negative on its free columns; then each other column in turn, the one
with the fewest positive-negative pairs first (the lowest on ties), is made non-negative: rays positive or zero there
stay, and each adjacent pair of a positive ray p and a negative ray n (no other ray is zero wherever both are, on the
columns made non-negative so far) gives (−n_j) p + p_j n. At the end the rays, their halves merged and a reaction's two
halves alone (the trivial cycle) dropped, are the modes. The matrix and its kernel cost as `incidence`'s; choosing a
column reads every ray once per column left; each pair tried costs one expansion plus one per ray its adjacency test
reads; each proof one, plus one per entry of its support's columns, plus one per pivot. The number of modes can grow exponentially with the network, and the budget bounds
the enumeration: an interrupted one lists none (`total` null). Each ray, the starting ones included, is also charged
one expansion per 64 split columns (its support's bitset) before it is built, and each ray merged at the end one. More
than `max_results` modes is an `output_limit` interruption whose `total` is exact.

Answer: `{"species", "reactions", "modes": [{"vector": [{"reaction", "value"}], "support", "certificate"}], "count",
"total"}`. With `certificate: true`, each mode carries `{"kind": "support_rank", "rows", "columns"}`: |J| − 1 species
and |J| − 1 of its reactions whose minor is nonsingular, so the columns of its support have rank |J| − 1, and with the
mode itself in their kernel, no feasible vector has a support strictly inside J. A checker rebuilds S from the records,
checks S v = 0 exactly, the signs, the primitive scale and the minor. That the list holds every mode is the
enumeration's, not certified. A network whose number of modes doubles with every layer (12 layers of two parallel
reactions, 26 reactions) gives its 4,096 modes in 0.6 s, all proven in 0.85 s.

**Canonical form and isomorphism.** Whether two fragments are the same structure, and one name for every copy of a
structure: deduplicating molecules, motifs and provenance fragments. The graph of a node set is its induced subgraph:
its nodes, each colored by its types (with their ancestors; `node_colors: none` colors all alike), and the edges in
scope (by relation, or one above it; the scope's directions do not matter) with both ends in the set, each with its own
relation, directed from source to target or, with `directed: false`, as an undirected link. Parallel edges and loops
count with their multiplicity.

Color refinement (one-dimensional Weisfeiler-Leman) gives a node, in each round, the rank, in sorted order, of its old
color with the sorted multiset of (neighbour's color, relation) over its out-edges and, separately, its in-edges (a
loop in both), or over its links when undirected (a loop once); it stops when no class splits. Colors depend on
structure alone, never on IDs. The canonical form comes from individualization and refinement (McKay and Piperno,
2014): refine; take the smallest class of several nodes (the lowest color on ties); for each of its nodes in turn (by
ID), give that node a color of its own just before the rest of its class, and search on. Every leaf (every class one
node) orders the nodes; its encoding is the nodes' colors in that order and the sorted edges `[i, j, relation]`
between positions (the smaller first when undirected); the least encoding over the leaves is canonical, and the first
leaf reaching it gives the order. Two leaves with the same encoding give an automorphism; a node in the orbit of one
already searched, under the automorphisms found that fix the current prefix, has the same leaves and is skipped. The
least encoding is the same for every isomorphic copy; only which order achieving it is reported depends on IDs. Reading
the set costs one expansion per node, per edge leaving it and per type it has, charged before they are read; each
refinement round one per node and per arc read; each individualization one per node; each leaf one per node and edge;
each orbit computation one per node and usable automorphism. Worst cases stay exponential, and the
budget bounds them.

`canonical_form` answers `{"order", "encoding": {"nodes", "edges", "directed"}, "canonical_id"}`, the identity the
digest of the encoding; all null when interrupted. A checker confirms that the encoding is the set's graph relabeled by
the order (so equal identities prove isomorphism); that it is the least is the search's, not certified.

`isomorphism` answers `{"isomorphic", "mapping": [{"left", "right"}], "edge_mapping": [{"left", "right"}],
"certificate"}` (each canonical identity, proven, is `canonical_form`'s). It first tries the easy proofs of a negative: other numbers of nodes or edges (`{"kind":
"counts"}`), or color refinement of the two graphs side by side, as one disjoint union, giving their sides other color
histograms after some round (`{"kind": "refinement", "rounds"}`); with `certificate: true` a negative so found carries
it, and a checker recomputes it. Otherwise both canonical forms are computed: equal identities give the mapping by
position and the edge mapping by (positions, relation, edge ID), which prove the isomorphism; other identities answer
false, proven by the exact search alone (`certificate` null). Interrupted: `isomorphic` null, nothing mapped.

A random 3-regular graph on 500 nodes against a relabeled copy is decided in 3.4 s (networkx's VF2 matcher: 114 s), a
random graph of 1,000 nodes and 3,000 edges in 0.05 s (0.76 s); highly symmetric small graphs (a hypercube, a complete
graph) take VF2 milliseconds and this search a little longer.

**Datalog.** Rules that derive facts from the records, recursively: provenance closures, lineage, "supported through
a dataset", any question that is a fixpoint of joins. A program is a list of rules, `head :- body`, each atom a
predicate over terms, a variable `"?x"` or a constant. It is positive (no negation), function-free and range-restricted
(every variable of a head occurs in its body; a rule without a body states a ground fact), so its meaning is its least
model: the smallest set of facts that holds the inputs and is closed under every rule (Abiteboul, Hull and Vianu,
1995). The inputs are the records: `node(N)` for every node; `type(N, T)` for every type a node has, its declared
types' ancestors included; `edge(E, S, T, R)` for every edge E from S to T, for its relation and every relation above
it. A rule may not define an input; every predicate has one arity, at most 32; every body predicate is an input or a
rule's head.
Rules are sorted by `id`, so their order in the request does not matter.

Evaluation is semi-naive (Bancilhon, 1986), the derived predicates in the order of their dependencies (strongly
connected components of the predicate graph, dependencies first). Within a component, each round fires every rule once
for each of its body atoms of the component, that atom over the facts the previous round found and the others over
every fact found so far, so that each derivation uses a new fact; a round's facts join the model when it ends, in
sorted order. A rule's atoms are joined in a fixed order: the new-fact atom first, then always the atom with the most
arguments bound (the earliest on ties), its candidates looked up in an index on its bound arguments. Every fact read,
input or derived, is one expansion, and so is every input fact loaded and every parent link followed to find an edge
relation's ancestors; building an index reads its relation's every fact (one expansion each), keeping an index reads
each new fact once more, each join order (one per rule and atom the new facts take) costs one expansion per atom of
the rule's body, and keeping a derivation (only when derivations or the certificate are asked for) costs one
expansion per premise. Each derived fact keeps the first derivation
found in that order: its rule and, for each body atom, the fact it matched; its rank is the round that found it, and
every premise's rank is lower. Facts, ranks and derivations do not depend on the interpreter's hash seed.

Answer: `{"rows": [{"values", "fact"}], "count", "total", "exists", "facts": [{"predicate", "args", "rule", "rank",
"premises"}], "model": {"facts", "rounds"}, "certificate"}`. The rows are the facts of the query's predicate that match
the query (its constants, and a variable repeated in it, alike), ascending, up to `max_results` (more is an
`output_limit` interruption whose `total` is still exact); `answer: count` and `exists` list none. With `derivations`,
each row names its derivation (`fact`, an index into `facts`), and `facts` lists every derived fact those derivations
use, by (rank, predicate, arguments), each premise `{"fact": index}` (an earlier fact) or `{"input": predicate,
"args"}` (a record). With `certificate: true`, a complete answer lists the whole least model in `facts`, and
`certificate` is `{"kind": "least_model"}`: every listed fact derived, and every rule, fired over the inputs and the
listed facts, deriving only listed facts, so they are the least model and the rows all of the query's matches. An
`output_limit` interruption keeps the exact total but not the certificate. Without `derivations`, rows name no fact
(`fact` null), with or without the certificate. An
interruption keeps the facts of the rounds completed: every row listed is derived, but `total` and `model` are null and
absence is unknown (a later round may find a fact that sorts first).

Transitive closure over 2,600 random edges among 2,000 nodes (583,630 facts in 60 rounds) takes 1.8 s, 4.7 s with the
whole model as certificate (10 s to check); networkx's `transitive_closure`, which proves nothing, 0.5 s. A naive
fixpoint takes 39 s for the 14,528 facts of 300 nodes, where semi-naive evaluation takes 0.03 s.

**Reachability between pairs.** Whether each source reaches its target in scope, for many pairs at once: provenance
checks, dependency questions asked again and again of one projection. A node reaches itself, by the empty walk. The
answer comes from a 2-hop index (Cohen, Halperin, Kaplan and Zwick, 2003): for each node, the hubs it reaches (its
out-label) and the hubs that reach it (its in-label); a source reaches a target exactly when the source's out-label and
the target's in-label share a hub. The index is built by pruned landmark labelling (Akiba, Iwata and Yoshida, 2013;
for reachability, Yano, Akiba, Iwata and Yoshida, 2013): the nodes in order of the product of one more than their in-
and out-degrees in scope, descending, then by ID; each in turn a hub, whose breadth-first search backwards labels every
node that reaches it and whose search forwards labels every node it reaches, except that a node whose labels already
share an earlier hub with the hub's is neither labelled nor searched through. Each label entry keeps the hop its search
came by, so a reachable pair's witness is the walk from the source to the first common hub (by order) and on to the
target, with every loop cut: a simple path.

The index is a function of the projection and the scope. It is built once and kept on the projection (in memory, with
it; the last four indexes built), and every request is charged its full cost, one expansion per node whose hops it
reads and one per label entry, whether it is built then or was kept: the same request gets the same expansions,
status and result either way, and a budget smaller than the index is exhausted the same way both times. A deadline or a
cancellation that stops the building keeps nothing. Then each pair, in order: one expansion, plus one per entry of
the smaller of the two labels read, plus, for a witness, one per hop of the walk through the hub it is cut from, charged
before each hop is followed (loops included: a witness can be short while that walk is long).
With `certificate: true`, each source of an unreachable pair gets its closure, every node it reaches (one expansion per
node): a set holding the source and not the target, from which every hop in scope stays inside, which a checker
verifies against the records.

The index pays when it is reused, or when many pairs reach far: on a random graph of 100,000 nodes and 300,000 edges
(one giant strong component, 390,000 label entries), 10,000 pairs take 1.2 s with the build and 0.33 s with the
index kept (networkx's `has_path`, pair by pair, 1.6 s). Labels stay small when some nodes are hubs; a random layered DAG without hubs (100,000 nodes, 46 million
entries) takes minutes to index, then answers 10,000 pairs in 0.3 s. Where most pairs are unreachable and reach sets
are small, a search per pair is cheaper than any index.

Answer: `{"pairs": [{"source", "target", "reachable", "witness"}], "count", "reachable", "index": {"entries",
"largest"}, "certificate"}`, in the request's (canonical) order; `witness` is `{"source", "target", "length", "hops"}`
for a reachable pair when witnesses are asked for, else null; `index` gives the label entries and the largest pair of
labels (null until built). An interruption keeps the pairs answered, a prefix, each decided; its certificate is null.
The certificate is `{"kind": "closures", "closures": [{"source", "nodes"}]}`, one per source of an unreachable pair,
in ascending order.

**K shortest paths.** The first k simple paths from a source to a target: alternative reaction pathways, backup routes,
the next best derivations. A path visits no node twice; other sources and targets may lie on it, and a source that is
a target is a path of no hops. Paths are ranked in a strict order: by cost (as for shortest paths, non-negative and
exact; without costs every hop costs 1), then by hops, then by the start node's ID and the sequence of edge IDs, so
parallel edges give distinct paths and ties have one order. Yen's algorithm (1971): the least path first; then, for
each accepted path and each of its nodes (its last included), the least path that keeps the hops before that spur
node, avoids the nodes before it, and leaves it neither by a hop nor by stopping that an accepted path with the same
root took is a candidate, and the least candidate is the next path. A virtual start before the sources makes the
choice of source the first step. Each least path is Dijkstra's search on the full order (extending paths to a node by
one hop keeps their order, so a least path extends a least path); each settled state is one expansion. Answer:
`{"paths": [{"source", "target", "length", "hops", "cost", ["cost_exact"]}], "count", "exhausted", "certificate"}`,
hops as a shortest path's; `exhausted` is true when fewer than k simple paths exist (all of them are listed), false
when k are listed, null when interrupted. An interrupted search lists the paths accepted so far: a prefix of the
order. Each path is a witness a checker confirms; without a certificate, that no simple path is missing before the
last one listed is Yen's theorem, checked by oracle tests, and such answers are not revalidated (`no_certificate`).

**Certificate.** With `certificate: true`, a complete answer carries `{"kind": "deviations", "potentials": [{"node",
"cost", "hops"}], "explored": [{"path", "at", "labels": [{"node", "cost", "hops", "side"}]}]}`, which proves the
ranking: that no unlisted simple path comes before the last path listed, or, when `exhausted`, that there is none.
Costs are written as a shortest path certificate's (integers, or exact `p/q` strings in rational mode).

- *Potentials.* For every node that reaches a target in scope (in ascending ID order), a lower bound on the least
  (cost, hops) of reaching one, compared by cost and then hops: every target is listed at (0, 0) or below, no hop
  in scope from a listed node u to a listed node v leaves u above v's potential plus the hop's (cost, 1), and every
  hop in scope into a listed node comes from a listed node. An unlisted node then reaches no target.
- *The prefix tree.* The listed paths' prefixes (a source and its first j hops) form a tree. A simple path that is
  not listed leaves it somewhere: at the start, from a source no listed path starts at, or at a prefix, by a hop
  no listed path with that prefix takes next (a prefix that ends at a target is listed, since it comes before the
  paths it begins: a prefix that ends at one unlisted refutes the ranking). Such a hop, and the rest of the path,
  avoid the prefix's nodes.
- *Explored prefixes.* For the start (`path` and `at` null) and for each prefix (the first `at` hops of path
  `path`, the first listed path with that prefix), the walks that leave it this way and avoid its nodes are
  followed while their potentials do not put them at or after the last path: each node they reach so is labelled
  with its least (cost, hops, side). `side` says whether, on a tie of cost and hops, such a walk comes before the
  last path or after it in the order of IDs, which is fixed where it leaves the tree (by the IDs the prefix and the
  hop share with the last path, then the first that differs); it is null when the answer is `exhausted`, where
  every path counts and no bound applies. Only prefixes with a label are listed, the start first, then by path and
  `at`; labels in ascending node ID order.

The check, from the records: for every leaving hop and every hop from a labelled node to a node off the prefix, the
(cost, hops, side) it brings is either at or after the last path's (cost, hops, after) once the potential of the node
it reaches is added (always so for a node without a potential; never so, unless it has none, when `exhausted`), or
no less than that node's label; and no labelled node is a target. A walk that left the tree and reached a target
before the last path would carry labels along it to that target, so there is none, and the listed paths are the
first ones in the order. The certificate's own searches (a reverse Dijkstra search from the targets, then one A*
search per prefix with those potentials) count against `max_expansions`: an answer whose certificate is cut short
is interrupted, with no certificate. Revalidation checks the certificate against the new records.

**Cycle basis.** A minimum cycle basis: the rings of a molecule, the independent loops of a reaction or citation
network. Each edge in scope is a link between its ends, read undirected; a loop is a cycle of one link and two
parallel links a cycle of two. The cycle space, over GF(2), has dimension `rank` = links less nodes plus connected
pieces; a basis is that many independent simple cycles, and a minimum one has the least total weight. With every link
1 that is the fewest links: chemistry's smallest set of smallest rings (SSSR), one minimum basis among possibly
several. Weights are as costs: non-negative and exact.

De Pina's algorithm (1995; Kavitha, Mehlhorn, Michail and Paluch, 2004): a spanning forest by edge ID leaves `rank`
links outside it, whose unit vectors are the first witnesses S_1..S_k. Cycle i is a lightest cycle meeting S_i oddly
(an odd number of its links in S_i); then every later witness that meets cycle i oddly takes S_i in, so S_i meets
every earlier cycle evenly. A lightest S-odd cycle is a shortest path from (v, 0) to (v, 1) in the parity graph (two
copies of the links, those in S crossing between the copies), from each end v of a link of S, read mod 2 and split
into simple cycles (the first meeting S oddly is kept, as light). The parity searches settle states by (distance,
node, parity) and stop at the first lightest walk found, starting from the smaller node ID; each settled state, and
each node's links read at the start, is one expansion. Each cycle is final once found, so an interrupted search lists
the cycles found, a prefix of the basis, with `weight` null.

Answer: `{"rank", "cycles": [{"nodes", "edges", "length", "weight", ["weight_exact"]}], "count", "weight",
["weight_exact"], "certificate"}`: each cycle's nodes in order around it, from its smallest node ID toward its
neighbour with the smaller ID, and its edges by ID; the total weight when complete. An interruption before the links
are read leaves `rank` null and lists nothing. With `certificate: true`: `{"kind": "witnesses", "witnesses": [[edge
IDs], ...]}`, one per cycle. They prove the basis (de Pina's theorem): cycle i meets witness i oddly and every
earlier cycle meets it evenly, so the cycles are independent; and when no cycle meeting witness i oddly is lighter
than cycle i, which a checker recomputes in the parity graph, the basis is minimum.

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
| `runtime`, `contract`, `kind` | `graph-runtime-sci` and its version, `graph-runtime/v0.2`, `graph_runtime.result` |
| `result_id` | the digest of everything but `timing` |
| `timing` | wall-clock seconds for `bind_s`, `search_s`, `witness_s`, `result_s` and `total_s`; errors may report only result and total time; not part of `result_id` |

- **Completed.** The answer is complete for this request on this projection. A complete negative (nothing reached,
  `found: false`) is scoped to the requested operation on the declared projection. It is not an absence claim about
  the literature or the physical world.
- **Interrupted.** What the answer lists is valid, each item with its witness, and listed depths are least depths.
  Nothing is certified absent: `found` is `null` (unknown), or `true` when a witness was already found.
  `result.reachable(x)` (reachability, and a regular path's `reached` answer), `result.affected(x)`, `result.found`
  and `result.exists` (patterns) give `False` only for a complete answer, and `None` when the answer cannot say.
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
  - that shortest-path costs are the exact sum of the hop costs; with a negative cost, that a negative cycle's three
    walks chain from a source to its start, around it at the negative cost it states, and on to a target, and that
    an interrupted answer gives no lower bound
  - for a bottleneck path, and a least-cost walk with a negative cost, the certificate's layers below the witness:
    every source at 0, every hop that can lie on a best path from a node below the last layer leading to a node
    listed at most one hop further, and the witness's end (a bottleneck path: every target) unlisted
  - for a minimum-cost flow, each flow a hop in scope within its capacity, conservation at every other node, the
    value the sources' net output and the exact cost; from the certificate, every residual arc non-negative once
    reduced, and a maximum flow's cut holding every source and no target with every hop out full and every hop in
    empty
  - for k shortest paths, each path's hops chaining in scope from a source to a target, no node twice, at the exact
    cost of its hops; the paths distinct and in the strict order; k of them unless `exhausted`; and from a
    certificate, the potentials against every hop in scope into a listed node, and for the start and every prefix of
    the listed paths, every hop leaving it and every hop from its labels, which proves the ranking
  - for flux modes, each vector's reactions columns of the matrix rebuilt from the records, its values non-zero
    integers without a common factor, irreversible reactions non-negative, S v = 0 exactly, the modes in order; from
    each certificate, a nonsingular (|J| − 1)-minor of the mode's support
  - for a canonical form, the order a permutation of the node set and the encoding the set's graph relabeled by it,
    with its digest; for an isomorphism, the node mapping a color-keeping bijection and the edge mapping a bijection
    keeping relations and mapped ends, and for a certified negative, the counts or the refinement histograms
    recomputed
  - for Datalog, each listed fact's derivation: its rule, its head and every body atom matching the fact and its
    premises under one substitution, each premise a record (with the hierarchies) or an earlier fact of lower rank;
    each row a match of the query with that derivation; from the certificate, every rule fired over the inputs and the
    listed facts deriving only listed facts, and a complete answer's rows the first matches of the query among them;
    a closure check that would read more than five million facts stops and reports the certificate not checked
  - for reachability between pairs, the request's pairs in order (a prefix when interrupted), a node reaching itself,
    each reachable pair's witness a path in scope from its source to its target, no node twice; from the certificate,
    each closure holding its source and none of the targets it is said not to reach, and closed under every hop in
    scope
  - for a cycle basis, each cycle a simple cycle of links in scope through the nodes it lists in order, with its exact
    weight; the cycles independent (by GF(2) elimination) and, when complete, `rank` of them, the dimension the
    records give; and with witnesses, each cycle odd against its own and every earlier cycle even against it, its
    weight that of a lightest cycle odd against its witness, recomputed in the parity graph
  - for a spanning forest, its edges against the records: links in scope, no loop and no cycle, spanning every
    piece, and every link left out after every edge of its tree path in the order of (weight, edge ID); then its
    count, trees and exact weight
  - that every pattern row's links are input edges in their steps' scopes joining the variables they bind, and that an
    injective pattern's rows bind no node and link no edge twice
  - that a complete reachability or impact answer is closed: every hop in scope from a node it expanded (a changed
    node at depth 0) leads to a listed node at most one hop deeper. With the witnesses, this proves the answer
    complete and its depths least
  - that every certificate proves its answer, as section 4 describes
  - for support, everything: what stands before and after, from its certificate when it has one (each rank and
    each unranked node against its rule, and the region's classification), otherwise recomputed from the records
    by re-evaluating each node's rule until nothing changes rather than by the engine's counting; then every listed
    node, count, failing hop and cause
  - for a derivation, every listed node's rule against the records (given: a ground; all: a node of an `all_of`
    type using one hop to each distinct input; one: any other node) and every use as a hop in scope; and the
    certificate's conditions at every node of the targets' region, which it finds from the records
  - for personalized PageRank, from its certificate: every residual recomputed exactly from the estimates and the
    records' hops, none negative, the intervals, residual and certified prefix exactly those they prove, and no node
    with a positive estimate left out that outranks a listed one by lower bound (or fits within `top`)
  - for components, every listed component's edge count and flags recounted from the records; and from a certificate,
    each group's tree hops (a spanning tree of the group from its first node), the groups disjoint, closed and
    (strong) in an order no hop goes back in, each listed component one of them, and a complete answer's groups
    covering the selection, with every group of `min_size` nodes or more listed and `total` their number
  - for biconnectivity, every bridge against its record; and from a certificate, the forest as a depth-first forest of
    the records' links, the low points recomputed from them, and the articulation points (with their splits),
    bridges and blocks exactly those they prove (an interrupted answer: some of them)
  - for a maximum flow, every flow against its edge's record and capacity, conservation and node capacities at every
    other node, the value leaving the sources, the cut's capacities adding up to the value, and with a certificate
    the cut recomputed from the records from its source side
  - for a matching, every pair a hop in scope from a left node to a node outside the left set, no node twice,
    `left_count` the left set's size, and a cover as large as the matching touching every candidate hop; with costs,
    the pairs' exact cost, and the potentials leaving no residual arc negative once reduced
  - for dominators, from a certificate: the order a preorder of the tree, every hop in scope from a listed node
    landing on a listed node whose dominator is an ancestor of the hop's tail, and every node low-high; the tree or
    chains read from it
  - for a critical path, a cycle's hops closing on themselves; from a certificate, the order covering what is
    scheduled with every hop forward, the earliest times by the longest-path recurrence, and from them the makespan,
    the critical path's costs and the schedule's latest times
  - for d-separation, every hop of the trail and of each collider's path against the records, and from a
    certificate the ancestors closed under parents and the visited states closed under the moves, with no node of `y`
  - for incidence, the matrix rebuilt from the records, every listed vector multiplied by it to zero, each basis
    independent, and from a certificate the minor's determinant nonzero: the rank and the kernels' completeness
  - for periodicity, against the cell's records: the tree's hops, every hop from a listed node's image leading to a
    listed node by a lattice vector, and the cycles being exactly the edges with a nonzero net, with those nets
  - for a regular path, every hop of a witness or of the tree against the path's automaton, which it builds itself
    (writing repetitions out first, then nullable, first, last and follow sets by their textbook definitions): each
    state may follow the one before it, each hop is in its step's scope and lands on its step's types, and a witness
    or a reached node ends in an accepting state; and a certificate's closure below its bound

  A typed or shortest path negative without a certificate, and a pattern's rows and total, are left to the oracle
  tests.
- `graph_runtime.conformance.run_conformance(execute)` runs 217 language-neutral cases (`conformance/cases.json`),
  with expectations written by hand from their graphs, against any backend's `execute`. A completed answer must
  match exactly and bind the case's projection and normalized request. Named `witness_edges` expectations check
  the canonical witness choice, including ties. An interrupted answer may list only what the full answer holds.
  Cases without deadline or cancellation controls are repeated to check `result_id` reproducibility.

## 7. Revalidation

`graph_runtime.revalidate` decides whether a cached result still holds on another projection, such as one built
after the records were edited, without searching again. It trusts a result to be GRAPH's own answer for the projection
it names, and checks what an edit could change: on that same projection a result holds without a check, and
`verify_result` is the check for a result from elsewhere. So a canonical form, whose minimality no certificate proves,
holds on its own projection and is otherwise refuted or unknown.

- `revalidate(result, projection)` checks the result's answer and certificate against the new projection, in time
  proportional to the answer and the hops from what it lists.
- `diff(old, new)` lists what changed, by original IDs: nodes added, removed and retyped, and edges added and removed
  (an edge whose ends or relation changed counts as both). It reads each projection once.
- `revalidate(result, projection, diff=d)` checks only what the edit touches, in time proportional to the edit. It
  looks up the answer's canonically ordered lists by binary search. It trusts that the result was correct on its own
  projection. When the vocabulary (relations, node types, hierarchies) changed, the full check runs instead.
- `revalidate_all(results, old, new)` revalidates every result with one diff.

A shortest path answer with a negative cost holds while its witness, or its negative cycle's three walks, are still
hops of the new projection (a cycle that remains still makes walks ever cheaper, whatever else changed), and its
feasible potentials stay feasible: an added hop from a listed node must lead to a dead end or to a listed node at
most its cost above, and an added hop from a dead end to a dead end; an added tight hop from a node below the last
layer must lead to a node listed at most one hop further.

A bottleneck path answer holds while its witness is still made of hops of the new projection and its certificate is
unbroken: an added hop strictly better than the threshold from a reached node must lead to a reached node, and an
added hop at least as good as the value from a node below the last layer must lead to a node listed at most one hop
further.

A regular path answer holds while its witness (or its tree) is still made of hops of the new projection, each in its
step's scope and landing on its step's types, and its certificate is unbroken: an added move from a listed state
below the bound must reach a listed state no further, and end no match the answer lacks. A listed state carries no
witness of its own, so an edit that breaks the certificate leaves the answer unknown rather than refuted.

A ranking holds while its estimates still prove it on the new projection, as `verify_result` checks it: put into the
new walk's equation they leave no negative residual, and the residual, intervals and certified prefix they prove are
exactly the ones the answer gives (tighter ones would leave its exact numbers false). It is always checked in full.

A spanning forest holds while it is still the forest the new records prove (its links, the cycle property and the
pieces it spans, as `verify_result` checks them); an edit that breaks any of these refutes it. It is always checked in
full.

Components, biconnectivity, a maximum flow, a minimum-cost flow, a matching, dominators, incidence, critical paths,
d-separation, cycle bases, k shortest paths, reachability between pairs, Datalog, canonical forms, isomorphisms and support are
revalidated by their certificates, which
prove the whole answer from the records alone. A node or edge the answer names that is gone refutes it (`node_gone`,
`witness_gone`), and so does a claim of the answer itself that the new records contradict (`answer_refuted`: a
kernel vector that no longer multiplies to zero, a flow beyond a capacity, a cut that is no longer the one nearest
the sources, a support count that an added or removed support changed); otherwise it holds exactly when its certificate still checks against the new projection's records (as
`verify_result` checks it), and is unknown (`certificate_broken`, with the first problem found) when it does not, or
(`no_certificate`) when it has none, unless its witness alone proves it (a cycle, an active trail, the witnesses of
pairs that are all reachable, an isomorphism's mappings). Flux modes are refuted when a mode is no longer feasible or
its minor no longer proves it, and are otherwise unknown: their certificates prove each mode, never that the list is
complete, and an edit can add a mode. A canonical form is refuted when its encoding is no longer
the set's graph relabeled by its order, and otherwise unknown (`no_certificate`): that it is the least encoding is not
certified. Pairs need both proofs: a closure proves only negatives, so a reachable pair asked for
without its witness leaves the answer unknown (`no_certificate`). These are always checked in full.

A derivation holds while every listed node keeps its rule and every use is still a hop in scope, and its certificate's
conditions still hold over the targets' region in the new projection. An edit far from the answer can join the
region, so a derivation is always checked in full (`checked` is `answer`, with or without a diff).

The verdict is one of three:

| Verdict | Meaning | Reasons |
| --- | --- | --- |
| `holds` | the answer is still a correct answer to the same request on the new projection. A new search might choose other witnesses among equally good ones | none |
| `fails` | it is not | `node_gone`, `witness_gone`, `type_changed`, `rule_changed` (a derivation's ground is no longer one, or an `all_of` node's inputs changed), `reaches_unlisted` (a hop from an expanded node to a node the answer does not list), `reaches_shallower`, `unanswerable` (the request names a node, relation, type or edge the new projection lacks, or an edge in scope has no cost), `answer_refuted` (a claim of a certificate-checked answer the new records contradict) |
| `unknown` | neither can be shown without a new search | `not_complete` (an interrupted or unanswered result), `not_certified` (pattern), `no_certificate` (a negative, an optimum, a fewest-hop witness or a reached set without one), `certificate_broken` (an edit broke the proof without refuting the answer) |

Each reason names the edge or node concerned. The record is `{"kind": "graph_runtime.revalidation", "contract",
"operation", "result_id", "request_id", "from_projection", "to_projection", "verdict", "reasons", "reason_count",
"revalidation_id"}`. `reasons` holds at most 100 entries, in canonical order. `revalidation_id` digests the rest.
The full and diff checks give the same record. `checked` (`answer` or `diff`) says how the verdict was reached and is
not part of the record.

A revalidated result keeps its own `result_id`: the record, not a new result, says that it holds on the new
projection. A new search could reach the same answer with other witnesses and statistics.

## 8. Not in v0

These are later extensions:

- approximate modes without proven bounds, and learned graph reasoning (personalized PageRank is in, as proven
  intervals)
- GPU execution and alternative backends (to be introduced after profiling, behind the conformance suite)
- AND/OR execution beyond the rules of `support` and `derivation` (hyperedges as records, counted or thresholded
  support)
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
