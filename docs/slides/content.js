// The content of the GRAPH deck: every operation, number and milestone the slides show.
//
// build_deck.js lays these out; edit here first. Inline markup in any text: `code` is set in a monospace font,
// **bold** in bold. Every claim must come from the repository (README.md, docs/API_CONTRACT.md, docs/*.md, the
// git log, benchmark output): each entry names its source in `source`, which goes into the speaker notes.
//
// Text budgets (14 pt body, so the cards do not overflow): an operation's theory, algorithm and proof are at most
// about 130 characters each on a three-column slide and about 200 on a two-column slide.

"use strict";

const meta = {
  title: "GRAPH",
  expansion: "Graph Runtime for Analysis, Provenance, Hypotheses",
  tagline: "Exact graph reasoning with witnesses and certificates, shared by the group's scientific software",
  author: "Hongliang Xin · Virginia Tech",
  // Update these three when the library moves on.
  date: "October 2026",
  version: "0.1.0.dev0",
  contract: "graph-runtime/v0.2",
  operations: 20, // the request operations (src/graph_runtime/request.py, OPERATIONS)
};

// ---------------------------------------------------------------------------------------------------------------
// Operation families. One slide per family (see build_deck.js, familySlide). Each operation:
//   name     the request's operation (or a short label), shown in monospace
//   title    what it answers, in plain words
//   theory   the idea in one or two lines
//   algorithm  the algorithm, with its reference
//   proof    what its certificate proves (proofLabel "Certificate proves"), or how it is checked
//   isNew    true while it is implemented and tested but not yet committed
//   notes    optional: more detail for the speaker notes only
const families = [
  {
    key: "paths1",
    family: "Paths",
    slideTitle: "Paths: reach, follow, optimize",
    lede: "Breadth-first, Dijkstra and Bellman-Ford-Moore searches in canonical order: one request, always the same witness.",
    icon: "LuSpline",
    source: "docs/API_CONTRACT.md §4: Reachability, Dependency impact, Typed path, Shortest path (and its Negative costs paragraph); §6; src/graph_runtime/engine.py (_search_signed, _answer_signed); tests/test_negative_costs.py; git log 97723bb",
    ops: [
      {
        name: "reachability",
        title: "Reach, and dependency impact",
        theory: "Every node within the scope and depth, at its least hop count; `dependency_impact` runs the same search from changed nodes.",
        algorithm: "Breadth-first search in canonical order; each node's first hop builds a witness tree.",
        proofLabel: "Closure proves",
        proof: "Every hop from an expanded node leads to a listed node at most one hop deeper: complete, and depths least.",
      },
      {
        name: "typed_path",
        title: "A walk along a relation sequence",
        theory: "One walk matching a finite sequence of relations, with optional node types at each position.",
        algorithm: "Breadth-first search over (node, position) states.",
        proofLabel: "Certificate proves",
        proof: "Closed states: all states the sources reach, closed under the pattern's hops, none ending at a target.",
      },
      {
        name: "shortest_path",
        title: "The cheapest route, exactly",
        theory: "A least-cost walk from a source to a target, costs of any sign (signed energies). A negative cycle on the way leaves no optimum.",
        algorithm: "Dijkstra if no cost is negative; else Bellman-Ford-Moore with Tarjan's subtree disassembly, over the nodes between sources and targets.",
        proofLabel: "Certificate proves",
        proof: "Potentials prove an optimum (with the dead ends if a cost is negative); three walks, a negative cycle; a closure, no path.",
        notes: "Costs are exact: integers, or rationals held as scaled integers. Non-negative requests are answered exactly as before, by Dijkstra's algorithm; its potentials also prove an interrupted search's lower bound. With any negative cost (docs/API_CONTRACT.md §4, Negative costs; engine.py _search_signed and _answer_signed): Bellman-Ford-Moore, first in first out, with Tarjan's subtree disassembly (Tarjan, 1981; Cherkassky and Goldberg, 1999), run only over the live nodes, those reachable from a source that can reach a target, so a negative cycle among the dead ends changes nothing. Outcomes: an optimum whose witness is the fewest-hop least-cost walk (breadth-first over the hops tight under the least costs, so it does not depend on the order of the search); or a negative cycle on a walk from a source to a target, reported as three walks (lead_in, the cycle with its negative cost, lead_out) that the verifier checks; a complete negative keeps its closure certificate; an interrupted search claims no lower bound. Certificate feasible_potentials: the least costs of the live nodes and the dead ends; every hop from a listed node goes to a dead end or to a listed node at most its cost above, dead ends are closed, sources sit at or below 0 and targets at or above the bound. Typical use: exergonic steps and signed energies in reaction networks. Tests: tests/test_negative_costs.py (1,500 random graphs against an independent fixpoint oracle, forged proofs rejected, revalidation against fresh runs); 5 hand-worked conformance cases.",
      },
    ],
  },
  {
    key: "paths2",
    family: "Paths",
    slideTitle: "Paths: bottlenecks and path expressions",
    lede: "Two questions a plain shortest path cannot ask: the best weakest link, and walks shaped by a regular expression.",
    icon: "LuSpline",
    source: "docs/API_CONTRACT.md §4: Bottleneck path, Regular path; git log ae48035, f65497b",
    ops: [
      {
        name: "bottleneck_path",
        title: "The fastest rate-limiting step, the lowest highest barrier",
        theory: "The path whose weakest edge is strongest (`max_min`, e.g. rates) or whose highest edge is lowest (`min_max`, e.g. barriers), fewest hops among those. Weights are compared, never added.",
        algorithm: "Settle nodes best value first, as Dijkstra settles costs; then a breadth-first search over the hops at least as good finds the fewest hops.",
        proofLabel: "Certificate proves",
        proof: "Threshold: what the sources reach by hops strictly better than the value is closed and holds no target, so no path is better. A closure proves a negative.",
      },
      {
        name: "regular_path",
        title: "Walks that match a regular expression of steps",
        theory: "Regular path queries (Mendelzon and Wood, 1995), as SPARQL property paths and GQL write them: `DERIVED_FROM*/SUPPORTED_BY[Measurement]/IN`",
        algorithm: "Breadth-first search over the product of the graph and the expression's Glushkov automaton: one walk with the fewest hops, or every node reached.",
        proofLabel: "Certificate proves",
        proof: "Closed product states below a bound prove the fewest hops, a negative or a complete reached set. The verifier builds its own automaton.",
      },
    ],
  },
  {
    key: "reasoning",
    family: "Reasoning",
    slideTitle: "Reasoning: AND/OR rules",
    lede: "A node of an `all_of` type needs every input, any other node one: evidence that must all hold, reactions that need all reactants.",
    icon: "LuGitMerge",
    source: "docs/API_CONTRACT.md §4: Support, Derivation; git log 5e61ca0, eeadc6a, 297ca1e",
    ops: [
      {
        name: "support",
        title: "What falls when nodes fail",
        theory: "A node with no support stands; an `all_of` node needs every support, any other node one. What stands is the least fixpoint, so support in a circle grounds nothing.",
        algorithm: "Two fixpoints by counting standing supports, before and after the failures, over only the region they reach: lost and weakened nodes, with their causes.",
        proofLabel: "Certificate",
        proof: "Both fixpoints' ranks: a ranked node meets its rule on lower ranks; the unranked fail theirs on unranked supports, an unfounded set. Generalizes SCALE's \"rests on a retraction\".",
      },
      {
        name: "derivation",
        title: "The best route from feedstocks to each product",
        theory: "Lightest derivations: a reaction needs all its reactants, a species any reaction that makes it. Cheapest (`min_sum`), lowest highest barrier (`min_max`), fastest slowest step (`max_min`).",
        algorithm: "Knuth's generalization of Dijkstra's algorithm (1977); the lightest derivation problem of Felzenszwalb and McAllester (2007). A settled value is final.",
        proofLabel: "Certificate proves",
        proof: "Values no rule improves on, over the targets' region, prove the best values and the underivable targets at once. The derivation DAG is the witness.",
      },
    ],
  },
  {
    key: "order",
    family: "Reasoning and order",
    slideTitle: "Order and causality: schedules and independence",
    lede: "Two readings of a directed graph: precedence for schedules, causation for causal claims and adjustment for confounding.",
    icon: "LuGanttChart",
    source: "docs/API_CONTRACT.md §4: Critical path, d-separation; git log 1d6dbbf, 83f9ffe",
    ops: [
      {
        name: "critical_path",
        title: "Schedule, makespan and slack, or a cycle",
        theory: "A hop from u to v means u comes first. Kelley and Walker's critical path method (1959): earliest times by the longest-path recurrence, the makespan, a critical path, latest times and slack. A graph with a cycle gives a cycle instead.",
        algorithm: "Kahn's algorithm (1962), taking the smallest ready node first: the lexicographically smallest topological order. Costs are exact; without them, earliest times are layers.",
        proofLabel: "Certificate proves",
        proof: "The order with earliest times: every hop goes forward and the recurrence holds exactly, which proves the makespan, the path and the schedule.",
      },
      {
        name: "d_separation",
        title: "Causal independence, or the trail that breaks it",
        theory: "A hop from u to v means u is a parent of v. x and y are d-separated given Z when no trail between them is active (Pearl, 1988; Geiger, Verma and Pearl, 1990).",
        algorithm: "Shachter's Bayes-ball (1998), as Koller and Friedman's Algorithm 3.1: the given nodes' ancestors, then a breadth-first search over (node, going up or down) states.",
        proofLabel: "Certificate proves",
        proof: "Not separated: the active trail, with a directed path from each collider down to Z. Separated: the ancestor set and visited states, closed under the moves, holding no node of y.",
      },
    ],
  },
  {
    key: "patterns",
    family: "Patterns",
    slideTitle: "Patterns: multi-step questions, substructures",
    lede: "SCALE's query plans in generic form: variables, steps, optional steps; trees, cycles and injective matches.",
    icon: "LuNetwork",
    source: "docs/API_CONTRACT.md §4: Pattern, Injective patterns; §6; tests/test_injective_pattern.py; git log 8b79971, 7b39f7d, b7e4f12",
    ops: [
      {
        name: "pattern · trees",
        title: "Tree patterns without dead ends",
        theory: "Variables bound by types (through the hierarchy) or IDs, then steps, each one hop from an earlier variable; optional steps keep their row.",
        algorithm: "Yannakakis' semi-join reduction: discover down, prune up, prune down. Totals are counted exactly without listing the rows.",
        proofLabel: "Checked by",
        proof: "`verify_result` checks every row; rows and totals match a brute-force enumerator (400 random patterns) and SCALE's own engine.",
        notes: "Rows come in canonical order; counting without listing handles 60⁴ rows in well under a second (git log 8b79971).",
      },
      {
        name: "pattern · cycles",
        title: "Corroboration and contradiction motifs",
        theory: "A step may close a cycle between bound variables: two supports of a claim from one dataset, a triangle of citations.",
        algorithm: "Worst-case optimal join (Ngo, Porat, Ré and Rudra; Leapfrog Triejoin): candidates meet what every closing step allows.",
        proofLabel: "Checked by",
        proof: "Row for row against a brute-force enumerator (300 random cyclic patterns). An interruption keeps a prefix of the rows, never denies.",
        notes: "The join intersects a variable's candidates with what every closing step allows, iterating the smallest set first (Veldhuizen's Leapfrog Triejoin).",
      },
      {
        name: "pattern · injective",
        title: "Substructure search",
        theory: "`injective: true`: distinct variables bind distinct nodes, distinct steps distinct edges. Motifs, catalytic sites, rings. No optional steps yet.",
        algorithm: "Over the same Yannakakis-pruned candidates, skipping held nodes and used edges. NP-complete: budget-bounded; exact totals when done.",
        proofLabel: "Checked by",
        proof: "Brute force filtered for distinctness, row for row (400 random patterns); networkx's monomorphisms. Interrupted: no absence claimed.",
        notes: "Subgraph monomorphism (docs/API_CONTRACT.md §4, Injective patterns): it counts every binding, so a pattern with automorphisms matches one subgraph several times (a hexagon twelve times). The rows are those of the same pattern without injective that use no node or edge twice, in the same canonical order. Distinctness is global, so no count along the kept hops respects it: an injective pattern, tree or cycle, is enumerated as a cycle's is, over the same semi-join-reduced candidates (a match with distinct nodes is a match). Totals are exact when the enumeration ends; an interruption leaves total null and claims no absence. No optional steps yet (unsupported, unsupported_mode): which node an optional step would bind among those left is not yet defined. The verifier and the contract checks reject a row that repeats a node or an edge. Every pattern request now states injective, so earlier pattern requests have new request and result IDs with the same answers (the golden workload's 160 pattern results regenerated). Row enumeration (cycles, injective patterns, tree rows) runs on an explicit stack: a Codex adversarial review found a 1,100-step chain hit Python's recursion limit. Tests (tests/test_injective_pattern.py): 400 random patterns row for row against the brute-force oracle filtered for distinctness; five shapes (a path, a star, a triangle, a square with a chord, a two-cycle) agree with networkx's subgraph monomorphisms (DiGraphMatcher); 3 hand-worked conformance cases. Benchmark (git log b7e4f12): 7,200 hexagon matches (600 rings × 12 symmetries) in a 1,300-atom graphene-like sheet in 0.05 s; networkx's GraphMatcher 0.54 s.",
      },
    ],
  },
  {
    key: "ranking",
    family: "Ranking",
    slideTitle: "Ranking: certified personalized PageRank",
    lede: "PageRank cannot be computed exactly by a finite push, so every score is an interval proven to contain it.",
    icon: "LuBarChart3",
    source: "docs/API_CONTRACT.md §4: Personalized PageRank; git log 0d4d2b3",
    aside: "rankingSchematic",
    ops: [
      {
        name: "personalized_pagerank",
        title: "A relevance ranking whose every number is proven",
        theory: "Personalized PageRank (Page and Brin; Jeh and Widom): the probability that a walk from the sources, stopping with probability α at each step, stops at v. The retrieval step of graph-based retrieval such as HippoRAG.",
        algorithm: "The local push of Andersen, Chung and Lang (2006), in exact integer units of 2⁻⁶⁴. Its invariant holds exactly, so whenever the push stops: p(v) ≤ π(v) ≤ p(v) + r(v) + (1 − α)(R − r(v)).",
        proofLabel: "Certificate proves",
        proof: "The estimates alone: a checker recomputes every residual exactly; if none is negative, every interval, the residual and the certified top follow.",
      },
    ],
  },
  {
    key: "incidence",
    family: "Ranking and algebra",
    slideTitle: "Incidence: exact rank and conservation laws",
    lede: "The first operation aimed squarely at RATES: what a reaction network conserves, and what leaves it unchanged.",
    icon: "LuSigma",
    source: "docs/API_CONTRACT.md §4: Incidence; git log 05d3ea5; the worked example run on the runtime",
    aside: "incidenceExample",
    ops: [
      {
        name: "incidence",
        title: "Exact rank and canonical kernel bases",
        theory: "The matrix of edge coefficients between row and column nodes. For a stoichiometric matrix the left kernel holds the conservation laws and the right the steady combinations; with the linkage classes the rank gives Feinberg's deficiency (1987). For a Petri net: place and transition invariants.",
        algorithm: "Exact fraction-free elimination over the integers: the rank and a nonsingular minor, then back elimination only for the kernels asked for. Each basis is canonical: reduced row echelon, primitive integer vectors.",
        proofLabel: "Certificate proves",
        proof: "A nonsingular minor (rank at least r), shown by elimination modulo 2⁶¹ − 1 with Bareiss as fallback, and the smaller kernel (rank at most r): the rank and the kernels' completeness follow.",
      },
    ],
  },
  {
    key: "periodic",
    family: "Periodic",
    slideTitle: "Periodic graphs: crystals as infinite graphs",
    lede: "A crystal, a slab or a polymer is an infinite graph that repeats; GRAPH searches it from one cell.",
    icon: "LuAtom",
    source: "docs/API_CONTRACT.md §1 (Periodic projections), §4 (Periodicity); docs/STATES_INTEGRATION.md; git log 699d263",
    footer: "ranks",
    ops: [
      {
        name: "periodic projection",
        title: "Crystals and slabs without a box of images",
        theory: "One cell of an infinite periodic graph, each edge with an integer image shift (a voltage graph; a crystal net's quotient graph).",
        algorithm: "Reachability and impact read only the images they reach, named `atom@i,j,k`; answers equal a finite lift's (600 random periodic graphs).",
        proofLabel: "Checked by",
        proof: "`verify_result` checks periodic results against the cell's records, image by image.",
      },
      {
        name: "periodicity",
        title: "How each component repeats",
        theory: "The translation lattice of a periodic net (Chung, Hahn and Klee, 1984; Delgado-Friedrichs and O'Keeffe, 2003).",
        algorithm: "A spanning tree gives each node a potential; each other edge closes a cycle whose net shift is a translation. Basis: the nets' Hermite normal form.",
        proofLabel: "Certificate proves",
        proof: "Witnesses write each basis row from cycle nets; the verifier checks every hop's net lies in the lattice, so the basis is exactly the component's.",
      },
    ],
  },
  {
    key: "structure",
    family: "Structure",
    slideTitle: "Structure: pieces, weak points, backbones",
    lede: "Recommended by the literature surveys. Each answer proves itself from the records alone: two on request, the forest always.",
    icon: "LuSplit",
    source: "docs/API_CONTRACT.md §4: Components, Biconnectivity, Spanning forest; §6; §7; tests/test_spanning_forest.py; git log 99fb868, 741c2dd",
    ops: [
      {
        name: "components",
        title: "How a graph falls into pieces",
        theory: "Weak (fragments) or strong (feedback loops, circular derivations), flagged initial, terminal and cyclic.",
        algorithm: "Breadth-first search (weak) or Tarjan's depth-first search (1972) for strong components. Linear time.",
        proofLabel: "Certificate proves",
        proof: "Spanning trees prove each listed component connected (strong: one out, one in); a closed partition in topological order proves nothing outside belongs with it.",
        notes: "Weak: hops either way. Strong: a walk each way. A terminal class is one a walk never leaves.",
      },
      {
        name: "biconnectivity",
        title: "Where a single failure splits a structure",
        theory: "Articulation points (with the pieces each leaves), bridges and blocks. Parallel edges are distinct links, never bridges.",
        algorithm: "One depth-first search with low points (Hopcroft and Tarjan, 1973). Linear time.",
        proofLabel: "Certificate proves",
        proof: "The depth-first forest: a checker confirms it is one, recomputes every low point from the records, and each claim follows.",
        notes: "Parallel edges are distinct links, so two between the same nodes are never bridges.",
      },
      {
        name: "spanning_forest",
        title: "Backbones, single-linkage clusters",
        theory: "The minimum (or maximum) spanning forest: one tree per piece. Any finite exact weights; ties go to the smaller edge ID.",
        algorithm: "Kruskal (1956) with a union-find, links in the order of (weight, edge ID): the forest is unique in that order.",
        proofLabel: "Proves itself by",
        proof: "Tarjan's cycle property: each link left out comes after every edge of its tree path (path maxima by binary lifting). Revalidation re-checks it.",
        notes: "docs/API_CONTRACT.md §4, Spanning forest: each edge in scope is a link between its ends, whichever way the scope follows it; a loop never joins anything; a node with no link is a tree of its own. Weights are compared exactly (floats as exact rationals, negatives allowed); without weights every link weighs 1 and the forest is the first in edge ID order. objective max takes weights descending, then edge ID. Reading each node's links is one expansion; an interrupted search claims nothing (every field null). Answer: the forest's edges in ascending ID, their count, the number of trees and the exact total weight. No certificate is needed: links in scope with no cycle, spanning every piece, and every link left out after every edge of its tree path in that order, are the forest Kruskal's algorithm keeps; the verifier finds those path maxima by binary lifting in O((n + m) log n). Revalidation (§7) re-verifies these claims on the new records, so a lighter or earlier new link refutes a cached forest. A Codex adversarial review found that the verifier skipped a forest given as a tuple (a Result's frozen answer), which let a stale forest revalidate as holding; fixed, with regression tests (a Result, a plain dict, a frozen answer). Tests (tests/test_spanning_forest.py): 800 random cases agree with an independent Kruskal; weights and trees agree with networkx's minimum and maximum spanning edges; forged forests are refused (ties, gaps, cycles, loops, weights, objective); revalidation agrees with fresh runs on 400 random edits; 4 hand-worked conformance cases. Benchmark (git log 741c2dd): 100,000 nodes and 300,000 edges in 0.89 s (networkx minimum_spanning_edges: 0.92 s); the verifier proves the forest in 3.05 s.",
      },
    ],
  },
  {
    key: "flows",
    family: "Structure and flows",
    slideTitle: "Flows: throughput, cuts and pairings",
    lede: "Each answer is proven optimal by its dual: a minimum cut for a flow, a vertex cover for a matching.",
    icon: "LuWaves",
    source: "docs/API_CONTRACT.md §4: Max flow, Matching; §6; git log 573ce2b",
    ops: [
      {
        name: "max_flow",
        title: "Throughput, and the cut that limits it",
        theory: "As much as the capacities let through from the sources to the targets (Ford and Fulkerson, 1956). Unit capacities count edge- or node-disjoint paths (Menger, 1927).",
        algorithm: "Dinic's algorithm (1970) in exact integers: breadth-first levels in the residual graph, then a blocking flow; the residual graph is built only where the search reads it.",
        proofLabel: "Certificate proves",
        proof: "The cut's source side: a checker confirms the flow and recomputes a cut of the same value from the records, so both are optimal (max-flow min-cut theorem).",
      },
      {
        name: "matching",
        title: "As many distinct pairings as possible",
        theory: "A maximum matching of a left set to partners outside it, no node in two pairs: samples to instruments, claims to reviewers.",
        algorithm: "Hopcroft and Karp (1973): breadth-first phases along alternating paths from the unpaired left nodes, then augmentation along the shortest ones.",
        proofLabel: "Certificate proves",
        proof: "König's vertex cover (1931): as many nodes as pairs, touching every candidate hop, so no matching is larger. When pairs fall short, Hall's condition shows why.",
      },
    ],
  },
  {
    key: "dominators",
    family: "Structure and flows",
    slideTitle: "Dominators: what every route passes through",
    lede: "The intermediate every route from a feedstock to a product forms; the evidence every derivation of a claim rests on.",
    icon: "LuMilestone",
    source: "docs/API_CONTRACT.md §4: Dominators; git log 1e8678d",
    aside: "dominatorSchematic",
    ops: [
      {
        name: "dominators",
        title: "Unavoidable intermediates, single points of failure",
        theory: "Node d dominates v when every walk from the sources to v passes through d (Prosser, 1959; Lowry and Medlock, 1969). The immediate dominators form a tree: the whole tree, or each target's chain.",
        algorithm: "A depth-first search from the sources, then Lengauer and Tarjan's algorithm (1979), the simple version with path compression. Dominance is a claim about every walk, so an interrupted search claims nothing.",
        proofLabel: "Certificate proves",
        proof: "A low-high order of the tree (Georgiadis and Tarjan, 2016), checked arc by arc in linear time, without computing dominators again. It is built from two independent spanning trees (their Algorithms 8, 7 and 5).",
      },
    ],
  },
];

// The overview grid (one card per family, in this order).
const overview = [
  { family: "Paths", icon: "LuSpline", ops: ["reachability", "dependency_impact", "typed_path", "shortest_path", "bottleneck_path", "regular_path"], answers: "What connects to what, and the best way there." },
  { family: "Reasoning and order", icon: "LuGitMerge", ops: ["support", "derivation", "critical_path", "d_separation"], answers: "AND/OR rules, schedules and slack, causal independence." },
  { family: "Patterns", icon: "LuNetwork", ops: ["pattern"], answers: "Multi-step questions counted exactly, cycles by a worst-case optimal join, injective substructure search." },
  { family: "Ranking and algebra", icon: "LuBarChart3", ops: ["personalized_pagerank", "incidence"], answers: "Relevance as proven intervals; exact rank and conservation laws." },
  { family: "Periodic", icon: "LuAtom", ops: ["periodicity"], answers: "Periodic projections: crystals, slabs and polymers searched from one cell." },
  { family: "Structure and flows", icon: "LuSplit", ops: ["components", "biconnectivity", "spanning_forest", "max_flow", "matching", "dominators"], answers: "Pieces, weak points, backbones, flows, pairings and unavoidable nodes." },
];

// ---------------------------------------------------------------------------------------------------------------
// Results. Times were measured on a heavily loaded machine unless a source says otherwise: ratios are more reliable
// than absolute times.
const speedTiles = [
  { big: "1.3–1.9×", label: "faster searches", context: "Compiled per-scope hop lists; 100k nodes, 500k edges", source: "git log f65497b" },
  { big: "4.5×", label: "faster float-cost shortest paths", context: "Exact rationals held as scaled integers", source: "git log f65497b" },
  { big: "0.66 s", label: "lightest derivations", context: "AND/OR graph of 60,000 nodes and 105,000 edges", source: "git log eeadc6a" },
  { big: "0.2 s", label: "certified top 10 by PageRank", context: "10k nodes, tolerance 1/1000; 100k nodes to residual 1/100 in 1.6 s", source: "git log 0d4d2b3" },
  { big: "0.36 s", label: "22,086 triangles", context: "Around a 2,500-degree hub, by the worst-case optimal join", source: "git log 7b39f7d" },
  { big: "2.4 s", label: "exact rank and conservation laws", context: "Random 2000 × 3000 network, verified in 5.4 s (first version: 163 s)", source: "git log 05d3ea5; measured on the owner's machine" },
];

// STATES contexts: mean of the 42 benchmark contexts (docs/STATES_INTEGRATION.md, Cost).
const statesChart = {
  labels: ["STATES's own code", "GRAPH, lifted box", "GRAPH, periodic cell"],
  values: [58.5, 305, 17.1],
  source: "docs/STATES_INTEGRATION.md, Cost (benchmarks/consumers.py, 26 October 2026)",
};

// GRAPH against networkx (pure Python too), seconds. Components, articulation points (GRAPH: biconnectivity, which
// also finds bridges) and max flow (5 sources, 5 targets, integer capacities) on 100k nodes / 500k edges (about 250k
// in scope for the structure runs); matching on 50k + 50k nodes with 250k candidate edges; dominators on 100k nodes /
// 300k edges, 94k of them reached (networkx immediate_dominators); critical path on 100k nodes / 400k edges (networkx
// dag_longest_path_length, which gives no order certificate); negative costs on 20k nodes / 100k edges, 31% of them
// negative, no negative cycle (networkx bellman_ford; GRAPH with its certificate); hexagon search: every injective match
// of a six-ring in a 1,300-atom graphene-like sheet (networkx GraphMatcher's subgraph monomorphisms); spanning forest on
// 100k nodes / 300k edges (networkx minimum_spanning_edges).
const networkxChart = {
  labels: ["Strong components", "Articulation points", "Max flow", "Matching", "Dominators", "Critical path", "Negative costs", "Hexagon search", "Spanning forest"],
  graph: [0.38, 0.57, 4.5, 2.5, 0.65, 0.67, 0.10, 0.05, 0.89],
  networkx: [0.34, 0.36, 6.4, 2.1, 0.93, 0.26, 0.13, 0.54, 0.92],
  caption: "100k nodes, 500k edges; matching 50k + 50k, 250k edges; dominators and forest 300k edges; critical path 400k edges (networkx: no order certificate). Negative costs: 20k nodes, 100k edges, 31% negative. Hexagons: 7,200 matches in a 1,300-atom sheet.",
  source: "Structure benchmark (99fb868 increment), git log 573ce2b, 1e8678d and 1d6dbbf; flows, matching, dominators and critical path measured on the owner's machine. Also: weak components 0.45 s, unit-capacity max flow 3.8 s. Negative costs (git log 97723bb; measured on an Apple M4): 20,000 nodes and 100,000 edges, 31% negative, no negative cycle; GRAPH 0.10 s with its feasible-potentials certificate, networkx bellman_ford 0.13 s and goldberg_radzik 0.39 s, the same cost. With a negative cycle planted near the source, networkx find_negative_cycle takes 0.002 s and GRAPH 0.042 s: GRAPH first computes the corridor between the sources and the targets (one linear pass), so that a cycle that cannot reach a target is ignored. Hexagon search (git log b7e4f12): an injective six-ring pattern counted in a graphene-like sheet of 1,300 atoms (networkx hexagonal_lattice_graph(20, 30)), 7,200 matches, 600 rings × 12 symmetries, GRAPH 0.05 s, networkx GraphMatcher's subgraph monomorphisms 0.54 s. Spanning forest (git log 741c2dd): 100,000 nodes and 300,000 random edges with integer weights, GRAPH 0.89 s, networkx minimum_spanning_edges (Kruskal) 0.92 s on the same multigraph; GRAPH's verifier proves the forest in 3.05 s. Machine load for these two was not recorded in the commits",
};

// GRAPH against SCALE's own query engine (scale.pgraph_query.run) on SCALE's documents, the same rows in the same
// order on every query. Measured on 26 October 2026 (a benchmark script kept outside the repository).
const scaleComparison = {
  title: "Head to head: SCALE's query engine",
  lede: "SCALE's own engine on SCALE's documents; the largest, methanol synthesis, has 2,851 nodes and 9,335 links.",
  rows: [
    { what: "Competency questions: 39 question–document pairs, median", scale: "0.56 ms", graph: "0.087 ms", ratio: "6.4×" },
    { what: "Methanol synthesis: all 1,052 one- and two-step plans", scale: "5.6 s", graph: "0.61 s", ratio: "9.2×" },
    { what: "Its largest answer: 10,470 rows", scale: "475 ms", graph: "21 ms", ratio: "23×" },
    { what: "Three steps, dead ends: 9,459 intermediate rows, 6 answers", scale: "18.9 ms", graph: "0.76 ms", ratio: "25×" },
    { what: "Methanol, 16 copies: 45,616 nodes, 149,360 links", scale: "17.1 s", graph: "1.14 s", ratio: "15×" },
  ],
  caption: "The same rows in the same order on every query, asserted row for row; GRAPH was never slower on 1,131 queries. Counting without listing: 16× faster on methanol's plans, 33× on 16 copies; its largest answer counted in 0.6 ms. Both engines scale linearly with the copies.",
  cards: [
    { icon: "LuLayers", head: "What SCALE's time includes", body: "Per-row evidence and a deep copy of the answer, about half its time. Without them GRAPH is still 3.7× faster on methanol, 6.3× on 16 copies." },
    { icon: "LuTimer", head: "Where SCALE is faster", body: "Building its index: 2.2 ms, against 20 ms for GRAPH's projection of methanol. The small retraction workflows: 0.04 ms against 0.32–0.36 ms." },
    { icon: "LuFilter", head: "Why", body: "SCALE expands rows step by step. GRAPH's semi-join reduction (Yannakakis) never meets a dead end, and counts without listing the rows." },
  ],
  source: "A benchmark run on 26 October 2026, kept outside the repository: scale.pgraph_query.run against GRAPH's pattern operation through the SCALE adapter (integrations/scale/scale_graph_adapter.py) on SCALE's documents, row-for-row parity asserted on every query. Times are each query's best of several runs; machine load was not recorded, so compare ratios. Competency questions: the 39 question-document pairs with a plan, medians per query. Methanol synthesis (SCALE's public example, 2,851 nodes, 9,335 links): every generated one- and two-step plan (1,052), totals 5.6 s against 0.61 s listing rows (9.2×) and 0.34 s counting (16×); its largest answer, 10,470 rows, 475 ms against 21 ms (23×, rounded), 0.6 ms counting. Three steps: the plan with the most intermediate rows per answer among 4,673 three-step candidates. 16 copies of the document: 17.1 s against 1.14 s (15×; 33× counting). 1,131 queries = 39 + 1,052 + 40 three-step plans. SCALE without per-row evidence and the deep copy of its answer (pgraph_query._run): methanol 2.27 s, still 3.7× GRAPH's time; 16 copies 7.2 s, 6.3×. SCALE's index (pgraph_query.Graph) 2.2 ms; GRAPH's projection 20 ms with the document's binding digests computed once. Retraction workflows: benchmarks/consumers.py scale (SCALE lifecycle.resting 0.04 ms; GRAPH 0.36 ms for resting on retractions, 0.32 ms for support) and docs/SCALE_INTEGRATION.md (Cost). Why: docs/API_CONTRACT.md §4, Pattern (Yannakakis' semi-join reduction; evaluation never meets a dead end)",
};

// Revalidation (git log e365fdc; README).
const revalidation = {
  lede: "Decided from the answer and its certificate, without searching again; certified operations re-check theirs on the new records.",
  verdicts: [
    { word: "holds", body: "Still correct on the new projection. Structure, flows, matchings, dominators, incidence, critical paths and d-separation hold exactly when their certificate checks, a forest when it verifies; a cycle or active trail by itself." },
    { word: "fails", body: "Refuted by the edit: a node or witness edge gone, or a claim the new records contradict (`answer_refuted`: a kernel vector no longer zero, a flow beyond a capacity, a cut no longer nearest the sources)." },
    { word: "unknown", body: "Not decidable without a new search: interrupted, uncertified, or an edit broke the proof without refuting it (`certificate_broken`)." },
  ],
  fast: "0.1 ms",
  slow: "≈100 ms",
  context: "100,000 nodes, 500,000 edges, after a 40-edge edit (one 0.32 s diff)",
  scale: { labels: ["holds", "fails", "unknown"], values: [312, 23, 10] },
  source: "git log e365fdc, 0a58767, 05d3ea5, 1d6dbbf, 83f9ffe, 297ca1e, 741c2dd; README.md (Revalidation); docs/API_CONTRACT.md §7 (a spanning forest holds while its links, the cycle property and the pieces it spans still check, as verify_result checks them; an edit that breaks any of these refutes it)",
};

// Verification counts at the time of the build. Update with every increment.
const verification = [
  { big: "582", label: "runtime tests pass" },
  { big: "180", label: "conformance cases" },
  { big: "16", label: "SCALE parity tests" },
  { big: "6", label: "STATES parity tests" },
  { big: "73 / 73", label: "real requests agree" },
];

// How increments are reviewed (git log 99fb868, 573ce2b, 1e8678d, 05d3ea5, 1d6dbbf, 04532ba, 107dc68, 297ca1e, 97723bb, b7e4f12, 741c2dd). Shown under the checks on the verification slide.
const reviews = [
  "Also: an independent fuzzing review (about 50,000 cases), a Codex review of the contract (v0.1), and a golden test of 1,200 pinned result_ids.",
  "Codex reviews each increment adversarially before commit. Found and fixed with regressions: a request_id collision and two certificate holes (structure); an unverified interrupted bound (flows); quadratic checks (dominators, support); a float entry that let a false rank verify, a stale nearest cut held, contradicted answers left unknown (incidence); exact fields beside false values, a path from outside the sources' region, unpolled controls (critical path); a nonuniform PageRank start, a lenient ranking revalidation, a quadratic cyclic join (26 October together); a whole certificate read for an unrelated edit (negative costs, else sound on 12,000 random graphs); the recursion limit hit by a 1,100-step chain (injective patterns); frozen answers the verifier skipped (forests). d-separation: no findings.",
];
const reviewsSource = "git log 8917563, 7ff04db; Codex adversarial reviews before commit: 99fb868 (a request field named kind was overwritten by the digest's record kind, now connectivity; unlisted groups and stray trees in the components certificate), 573ce2b (an interrupted max flow's upper bound was not verified), 1e8678d, 05d3ea5, 1d6dbbf, 04532ba, 107dc68, 297ca1e; 97723bb and 260d7b2 (negative costs and the faster projection identity, reviewed together: no soundness or identity counterexample, 12,000 larger random graphs all matching an independent oracle; one medium issue, diff-based revalidation reading the whole certificate even for an unrelated edit, fixed, and a regression test now forbids it); b7e4f12 (injective patterns: a 1,100-step chain hit Python's recursion limit, so row enumeration for cycles, injective patterns and tree rows now runs on an explicit stack, with a regression test covering rows, count and exists); 741c2dd (spanning forests: the verifier skipped a forest given as a tuple, a Result's frozen answer, which let a stale forest revalidate as holding; it now checks tuples and refuses a missing edge list, and so do three other list-only checks; regression tests cover a Result, a plain dict and a frozen answer)";

// ---------------------------------------------------------------------------------------------------------------
// Development timeline (git log). `when` is local time, -0400.
const timelineLede = "Forty commits on 25–26 October 2026: from four operations to twenty.";
const timelineSource = "git log, 550c362 (25 October, 17:17) to 741c2dd (26 October, 22:55): 40 commits, slide commits included; some rows group neighbouring commits (11:16 is incidence, 05d3ea5, with critical paths at 11:32, 1d6dbbf, and d-separation at 11:44, 83f9ffe; 17:45 is the fixes from a review of the day's increments, 107dc68, with support certificates at 18:23, 297ca1e), and 22:11 is two (260d7b2, the projection identity hashed with each node ID, relation and type encoded once: 61 to 34 ms, the build 245 to 206 ms on 50,000 nodes and 150,000 edges, identity bytes unchanged on 400 random projections; 97723bb, negative costs)";
const timeline = [
  { day: "25 Oct 2026" },
  { when: "17:17", what: "v0: projections, 4 operations, results; SCALE parity" },
  { when: "17:36", what: "Independent and Codex reviews → contract v0.1" },
  { when: "20:55", what: "STATES as a consumer; hot spots removed; CI" },
  { when: "23:08", what: "Hierarchies (v0.2) · pattern · retrieval · support" },
  { day: "26 Oct 2026" },
  { when: "00:14", what: "Certificates, revalidation, tools · `bottleneck_path`" },
  { when: "04:25", what: "Compiled hop lists · `regular_path` · `derivation`" },
  { when: "05:12", what: "`periodicity` · certified PageRank · cyclic patterns" },
  { when: "10:09", what: "`components` · `biconnectivity` · these slides" },
  { when: "10:29", what: "`max_flow` · `matching`" },
  { when: "10:43", what: "`dominators` · revalidation by certificate" },
  { when: "11:16", what: "`incidence` · `critical_path` · `d_separation`" },
  { when: "17:45", what: "Review fixes · support certificates, revalidated" },
  { when: "22:11", what: "Negative-cost shortest paths · faster projection identity" },
  { when: "22:38", what: "Injective patterns: substructure search" },
  { when: "22:55", what: "`spanning_forest`, proven by the cycle property", latest: true },
];

// Hand-worked conformance cases after each increment (commit messages; cases.json now).
const growth = {
  labels: ["v0", "v0.1", "hier.", "pattern", "support", "certs", "bottleneck", "regular", "deriv.", "periodic", "PageRank", "cycles", "structure", "flows", "dominators", "incidence", "crit. path", "d-sep.", "sup. cert.", "neg. costs", "injective", "forest"],
  cases: [34, 49, 54, 62, 70, 74, 78, 91, 100, 112, 116, 121, 133, 143, 148, 153, 158, 165, 168, 173, 176, 180],
  operations: "4 → 20",
  tests: "178 → 582",
};

// ---------------------------------------------------------------------------------------------------------------
// Roadmap: the next increments, in the order of a Codex literature survey of 26 October 2026 (ranked by value
// against effort and the cost of proving each answer). Its first three are done: negative-cost shortest paths
// (97723bb), injective patterns (b7e4f12) and the minimum spanning forest (741c2dd); the fourth is being built.
const roadmapLede = "Ranked in a Codex survey. Done: negative costs, injective patterns, spanning forests. Being built: ring perception.";
const roadmap = [
  { name: "Minimum cycle basis, relevant cycles", icon: "LuHexagon", answers: "Ring perception: a smallest set of smallest rings; the relevant rings.", cert: "Elimination over GF(2) proves a basis; de Pina's supports, a minimum.", ref: "Horton 1987; Vismara 1997; Kavitha et al." },
  { name: "Weighted matching, min-cost flow", icon: "LuArrowLeftRight", answers: "Assignment, allocation and transport at least cost.", cert: "Dual potentials: no residual arc has a negative reduced cost.", ref: "Munkres 1957; Király and Kovács 2012" },
  { name: "Counting cyclic patterns", icon: "LuTally5", answers: "Exact motif counts without listing rows, over a tree decomposition.", cert: "The decomposition's messages, each recomputed by a checker.", ref: "Abo Khamis, Ngo and Rudra 2016 (FAQ)" },
  { name: "k shortest simple paths", icon: "LuListOrdered", answers: "Alternative pathways and explanations, not only one optimum.", cert: "The paths; a Lawler partition tree proves they are the first k.", ref: "Yen 1971; Lawler 1972" },
  { name: "Reachability index", icon: "LuZap", answers: "Many reachability queries on one large, fixed projection: 2-hop labels.", cert: "A positive expands hub pointers into hops; a negative needs a closure.", ref: "Cohen, Halperin, Kaplan and Zwick 2003" },
  { name: "Positive Datalog", icon: "LuBraces", answers: "Rules over the records: provenance questions in a small language.", cert: "Derivation trees, ranks strictly increasing, checked in their size.", ref: "Abiteboul, Hull and Vianu 1995; Green et al. 2007" },
];
const roadmapLater = "**Then:** isomorphism and canonical labeling · elementary flux modes.";
const roadmapSource = "A Codex literature survey of 26 October 2026, kept outside the repository; its recommended order weighs scientific usefulness, implementation effort and the cost of independently proving each answer. Its first three items have shipped: negative-cost shortest paths (97723bb); injective patterns (b7e4f12: the survey suggested VF2++-style backtracking with Glasgow-style filtering, embeddings checked in O(q + r), a negative needing an exhaustive refutation tree; GRAPH enumerates over its Yannakakis-pruned candidates, budget-bounded, and an interrupted search claims no absence); and the minimum spanning forest (741c2dd: Kruskal, the forest its own certificate by the cycle property). The fourth, minimum cycle basis (ring perception), is being built. Survey notes per row: minimum cycle basis (de Pina; GF(2) elimination proves a basis, de Pina's supports with shortest-path proofs prove minimality; relevant cycles after Vismara); min-cost flow and weighted matching (successive shortest paths with potentials; reduced costs non-negative on every residual arc); cyclic pattern counting (variable elimination, FAQ InsideOut; a decomposition and dynamic-programming transcript); k shortest simple paths (Yen with Lawler's partitioning; a partition tree proves the ranking); reachability index (strong components condensed, then pruned landmark 2-hop labels; Cohen, Halperin, Kaplan and Zwick 2003; Akiba, Iwata and Yoshida 2013; a positive expands hub routing pointers into ordinary hops, checked in the walk's length; disjoint labels alone do not prove a negative, which needs a closed set); positive Datalog (range-restricted, function-free, semi-naive evaluation; a derivation tree or DAG with strictly increasing ranks, checked in the proof's size; Abiteboul, Hull and Vianu 1995; Green, Karvounarakis and Tannen 2007, provenance semirings); then isomorphism and canonical labeling (individualization-refinement; McKay and Piperno 2014) and elementary flux modes (exact double description; Gagneur and Klamt 2004). Each increment is planned to ship with code, docs/API_CONTRACT.md, the schema, hand-worked conformance cases, tests against an independent oracle, verification and revalidation, and a Codex adversarial review of its diff.";

const boundaries = {
  wont: [
    "Infer scientific edges or their truth: neighbor rules, bond perception, units and kinetics stay in the adapters.",
    "Become a database server, a chemistry toolkit, an ODE solver or an SMT engine.",
    "Take sampled or learned answers as authority: Monte Carlo and learned pruning may only order work that is then proven.",
    "Read provenance as probability or causation.",
  ],
  open: [
    "Which workloads dominate: targeted paths, complete reached sets, pattern counts, repeated scopes, or edits?",
    "Must every result verify from raw records, or may a verifier reuse a checked, identity-bound index?",
    "Which new operations must run on periodic projections?",
    "First acceptance workloads: a RATES balance problem, a STATES equivalence or ring query, a SCALE or Cathanor provenance question.",
  ],
  alsoSurveyed: "Also surveyed earlier: Katz intervals, bidirectional search, factorized pattern answers. Critical paths and d-separation, from the same list, are done.",
};

module.exports = {
  meta, families, overview, speedTiles, statesChart, networkxChart, scaleComparison, revalidation, verification,
  reviews, reviewsSource, timelineLede, timelineSource, timeline, growth, roadmapLede, roadmap, roadmapLater,
  roadmapSource, boundaries,
};
