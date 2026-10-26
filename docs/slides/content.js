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
  operations: 19, // the request operations (src/graph_runtime/request.py, OPERATIONS)
};

// ---------------------------------------------------------------------------------------------------------------
// Operation families. One slide per family (see build_deck.js, familySlide). Each operation:
//   name     the request's operation (or a short label), shown in monospace
//   title    what it answers, in plain words
//   theory   the idea in one or two lines
//   algorithm  the algorithm, with its reference
//   proof    what its certificate proves (proofLabel "Certificate proves"), or how it is checked
//   isNew    true while it is implemented and tested but not yet committed
const families = [
  {
    key: "paths1",
    family: "Paths",
    slideTitle: "Paths: reach, follow, optimize",
    lede: "Breadth-first and Dijkstra searches in a fixed canonical order, so the same request always picks the same witness.",
    icon: "LuSpline",
    source: "docs/API_CONTRACT.md §4: Reachability, Dependency impact, Typed path, Shortest path; §6",
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
        theory: "A minimum-cost path from any source to any target, with explicit non-negative costs.",
        algorithm: "Dijkstra's algorithm in exact arithmetic: integers, or rationals held as scaled integers.",
        proofLabel: "Certificate proves",
        proof: "Potentials prove the optimum, or an interrupted search's lower bound; a closure proves there is no path.",
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
    source: "docs/API_CONTRACT.md §4: Support, Derivation; git log 5e61ca0, eeadc6a",
    ops: [
      {
        name: "support",
        title: "What falls when nodes fail",
        theory: "A node with no support stands; an `all_of` node needs every support, any other node one. What stands is the least fixpoint, so support in a circle grounds nothing.",
        algorithm: "Two fixpoints by counting standing supports, before and after the failures, over only the region they reach: lost and weakened nodes, with their causes.",
        proofLabel: "Checked by",
        proof: "`verify_result` recomputes what stands from the records by re-evaluating every rule. Generalizes SCALE's \"rests on a retraction, all or in part\".",
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
    slideTitle: "Patterns: multi-step questions",
    lede: "SCALE's query plans in generic form: variables, steps, optional steps; trees and cycles.",
    icon: "LuNetwork",
    source: "docs/API_CONTRACT.md §4: Pattern; §6; git log 8b79971, 7b39f7d",
    ops: [
      {
        name: "pattern · trees",
        title: "Tree patterns without dead ends",
        theory: "Variables bound by types (through the hierarchy) or explicit IDs, then steps, each one hop from an earlier variable; optional steps keep their row.",
        algorithm: "Yannakakis' semi-join reduction: discover down, prune up, prune down. Rows in canonical order; the exact total is counted without listing (60⁴ rows in well under a second).",
        proofLabel: "Checked by",
        proof: "`verify_result` checks every row against the records; rows and totals match a brute-force enumerator (400 random patterns) and SCALE's own engine.",
      },
      {
        name: "pattern · cycles",
        title: "Corroboration and contradiction motifs",
        theory: "A step may close a cycle between bound variables: two supports of a claim from one dataset, a triangle of citations.",
        algorithm: "Worst-case optimal join (Ngo, Porat, Ré and Rudra; Veldhuizen's Leapfrog Triejoin): intersect candidates with what every closing step allows, smallest set first.",
        proofLabel: "Checked by",
        proof: "Row for row against a brute-force enumerator (300 random cyclic patterns). An interruption keeps a prefix of the rows and never denies.",
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
    slideTitle: "Structure: pieces and weak points",
    lede: "Recommended by both literature surveys. Both answers prove themselves on request, from the records alone.",
    icon: "LuSplit",
    source: "docs/API_CONTRACT.md §4: Components, Biconnectivity; git log 99fb868",
    ops: [
      {
        name: "components",
        title: "How a graph falls into pieces",
        theory: "Weak (hops either way: fragments) or strong (a walk each way: feedback loops, circular derivations), flagged initial, terminal and cyclic. A terminal class is one a walk never leaves.",
        algorithm: "Breadth-first search (weak) or Tarjan's depth-first search (1972) for strong components. Linear time.",
        proofLabel: "Certificate proves",
        proof: "Spanning trees prove each listed component connected (strong: one out, one in); a closed partition in topological order proves nothing outside belongs with it.",
      },
      {
        name: "biconnectivity",
        title: "Where a single failure splits a structure",
        theory: "Articulation points (with the pieces each leaves), bridges and blocks. Parallel edges are distinct links, so two between the same nodes are never bridges.",
        algorithm: "One depth-first search with low points (Hopcroft and Tarjan, 1973). Linear time.",
        proofLabel: "Certificate proves",
        proof: "The depth-first forest: a checker confirms it is one, recomputes every low point from the records, and each claim follows.",
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
  { family: "Patterns", icon: "LuNetwork", ops: ["pattern"], answers: "Multi-step questions: trees counted exactly, cycles by a worst-case optimal join." },
  { family: "Ranking and algebra", icon: "LuBarChart3", ops: ["personalized_pagerank", "incidence"], answers: "Relevance as proven intervals; exact rank and conservation laws." },
  { family: "Periodic", icon: "LuAtom", ops: ["periodicity"], answers: "Periodic projections: crystals, slabs and polymers searched from one cell." },
  { family: "Structure and flows", icon: "LuSplit", ops: ["components", "biconnectivity", "max_flow", "matching", "dominators"], answers: "Pieces, weak points, throughput, pairings and unavoidable nodes." },
];

// ---------------------------------------------------------------------------------------------------------------
// Results. Times were measured on a heavily loaded machine: ratios are more reliable than absolute times.
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
// dag_longest_path_length, which gives no order certificate).
const networkxChart = {
  labels: ["Strong components", "Articulation points", "Max flow", "Matching", "Dominators", "Critical path"],
  graph: [0.38, 0.57, 4.5, 2.5, 0.65, 0.67],
  networkx: [0.34, 0.36, 6.4, 2.1, 0.93, 0.26],
  caption: "100k nodes, 500k edges; matching 50k + 50k, 250k edges; dominators 300k edges (94k reached; 1.37 s with its certificate); critical path 400k edges (networkx gives no order certificate). Also: weak components 0.45 s, unit-capacity max flow 3.8 s.",
  source: "Structure benchmark (99fb868 increment), git log 573ce2b, 1e8678d and 1d6dbbf; flows, matching, dominators and critical path measured on the owner's machine",
};

// Revalidation (git log e365fdc; README).
const revalidation = {
  lede: "Decided from the answer and its certificate, without searching again; certified operations re-check theirs on the new records.",
  verdicts: [
    { word: "holds", body: "Still correct on the new projection. Structure, flows, matchings, dominators, incidence, critical paths and d-separation hold exactly when their certificate checks; a cycle or active trail holds by itself." },
    { word: "fails", body: "Refuted by the edit: a node or witness edge gone, or a claim the new records contradict (`answer_refuted`: a kernel vector no longer zero, a flow beyond a capacity, a cut no longer nearest the sources)." },
    { word: "unknown", body: "Not decidable without a new search: interrupted, uncertified, or an edit broke the proof without refuting it (`certificate_broken`)." },
  ],
  fast: "0.1 ms",
  slow: "≈100 ms",
  context: "100,000 nodes, 500,000 edges, after a 40-edge edit (one 0.32 s diff)",
  scale: { labels: ["holds", "fails", "unknown"], values: [312, 23, 10] },
  source: "git log e365fdc, 0a58767, 05d3ea5, 1d6dbbf, 83f9ffe; README.md (Revalidation); docs/API_CONTRACT.md §7",
};

// Verification counts at the time of the build. Update with every increment.
const verification = [
  { big: "543", label: "runtime tests pass" },
  { big: "165", label: "conformance cases" },
  { big: "16", label: "SCALE parity tests" },
  { big: "6", label: "STATES parity tests" },
  { big: "73 / 73", label: "real requests agree" },
];

// How increments are reviewed (git log 99fb868, 573ce2b, 1e8678d, 05d3ea5, 1d6dbbf; the d-separation review as the
// coordinator reported it). Shown under the checks on the verification slide.
const reviews = [
  "Also: an independent fuzzing review (about 50,000 cases), a Codex review of the contract (v0.1), and a golden test of 1,200 pinned result_ids.",
  "Codex reviews each increment adversarially before commit. Found and fixed with regressions: a request_id collision and two certificate holes (structure); an unverified interrupted bound (flows); a quadratic cycle check (dominators); a float entry that let a false rank verify, a stale nearest cut revalidated as holding, contradicted answers left unknown (incidence); exact fields beside false values, a path from outside the sources' region, controls never polled (critical path). d-separation: approved with no findings after 10,000 oracle comparisons and 5,000 revalidation checks.",
];

// ---------------------------------------------------------------------------------------------------------------
// Development timeline (git log). `when` is local time, -0400.
const timelineLede = "Thirty-one commits on 25–26 October 2026: from four operations to nineteen.";
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
  { when: "11:16", what: "`incidence` (conservation laws)" },
  { when: "11:32", what: "`critical_path` (schedules, slack)" },
  { when: "11:44", what: "`d_separation` (causal independence)", latest: true },
];

// Hand-worked conformance cases after each increment (commit messages; cases.json now).
const growth = {
  labels: ["v0", "v0.1", "hier.", "pattern", "support", "certs", "bottleneck", "regular", "deriv.", "periodic", "PageRank", "cycles", "structure", "flows", "dominators", "incidence", "crit. path", "d-sep."],
  cases: [34, 49, 54, 62, 70, 74, 78, 91, 100, 112, 116, 121, 133, 143, 148, 153, 158, 165],
  operations: "4 → 19",
  tests: "178 → 543",
};

// ---------------------------------------------------------------------------------------------------------------
// Roadmap: the next increments (plan.md after the two Codex surveys, codex_literature.md and codex_domains.md).
const roadmapLede = "From two Codex literature surveys, most of whose list is now done. What remains:";
const roadmap = [
  { name: "Support certificates", icon: "LuShieldCheck", answers: "Support answers checked and revalidated without recomputing.", cert: "Derivation ranks and closure under every rule prove the least fixpoint.", ref: "Dowling–Gallier 1984; McConnell et al. 2011" },
  { name: "k-best paths", icon: "LuListOrdered", answers: "Alternative routes and explanations, not only one optimum.", cert: "Path witnesses; a deviation partition with potentials proves the first k.", ref: "Yen 1971 (k shortest simple paths)" },
  { name: "Pattern query planning", icon: "LuDatabase", answers: "Start the semi-join reduction from the most selective variable.", cert: "The same answers; the work statistics, part of result_id, change.", ref: "Selinger et al. 1979 (access paths)" },
  { name: "Incremental repair", icon: "LuRefreshCw", answers: "Repair cached answers that revalidation finds broken.", cert: "A fresh answer with an ordinary certificate on the new projection.", ref: "Gupta et al. 1993; Ramalingam–Reps 1996" },
];

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
  alsoSurveyed: "Also surveyed: DAG critical paths, d-separation, minimum spanning forests, cycle bases, Datalog, Katz intervals, bidirectional search, factorized pattern answers.",
};

module.exports = {
  meta, families, overview, speedTiles, statesChart, networkxChart, revalidation, verification, reviews, timelineLede,
  timeline, growth, roadmapLede, roadmap, boundaries,
};
