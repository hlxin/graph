# GRAPH: developer note for Claude

**Graph Runtime for Analysis, Provenance, Hypotheses**

Prepared for Hongliang Xin · 25 October 2026 · Handoff revision 1

## Decision and purpose

Use **GRAPH** as the project name and **Graph Runtime for Analysis, Provenance, Hypotheses** as its exact expansion. The H stands for **Hypotheses**. The working subtitle is **Reusable graph algorithms for scientific software**.

GRAPH is intended to be a small reusable library that supplies reliable graph execution to scientific applications. SCALE, STATES, RATES, and Cathanor remain independent projects. They keep the scientific definitions, domain rules, authoritative records, and workflows that make their results meaningful. GRAPH preserves the computational contract across those applications: explicit inputs, supported operations, bounded work, reproducible outputs, and paths that can be checked against the input records.

The proposed Python distribution is `scale-graph-runtime`, with import `graph_runtime`. These identifiers have not been reserved or verified as available. The earlier discussion's `scale-graph` was a suggestion, not an established choice. The longer proposal also helps distinguish this new runtime from SCALE's historical graph/schema version line. The bare PyPI name `graph` already belongs to another project. [N1]

This handoff contains design documents, proposed contracts, synthetic validation cases, a Claude kickoff prompt, and the complete earlier research archive. It is not a completed GRAPH runtime or a production integration. The packaging work has not created a remote repository, published a distribution, or modified SCALE.

## 1. The boundary to implement

Share graph execution where the operation and its guarantees are the same. Keep scientific interpretation with the application that owns it.

| Component | Primary responsibility |
| --- | --- |
| GRAPH | Read-only graph views, generic traversal, path and dependency witnesses, execution limits, result contracts, candidate-hypothesis transport, and backend conformance |
| SCALE | Scientific schema, evidence, original values and units, comparability, source lifecycle, access policy, and scientific answer semantics |
| STATES | Structural/state identity, periodic and geometric representations, symmetry, equivalence, and valid state transitions |
| RATES | Reaction participation, stoichiometry, site accounting, energetic and kinetic conventions, mechanism compilation, numerical execution, and scientific interpretation of pathways |
| Cathanor | Tasks, frozen inputs, reference answers, evaluation rules, leakage controls, and reproducibility |

The assignments for STATES, RATES, and Cathanor are architectural proposals, not assertions about inspected implementations. SCALE's relevant source was inspected and is documented in `docs/SCALE_INTEGRATION.md`.

The dependency direction is application → application-owned adapter → GRAPH interface. The generic runtime must not import SCALE's vocabulary, RATES rate laws, or STATES structure definitions. A shared library does not require a shared database or a network service. Begin with local, in-process execution.

## 2. First implementation scope

The initial contract has four operations:

1. **Reachability:** return the requested reachable node set in an explicitly selected relation and direction scope.
2. **Typed path:** find one witness matching a finite relation sequence; the state is a node plus its position in that sequence.
3. **Shortest path:** return one minimum-cost witness using explicit, finite, nonnegative additive costs supplied by the consumer.
4. **Dependency impact:** use directed reachability on a consumer-defined dependency projection; the consumer decides what affected records require.

Every operation binds an immutable projection and a normalized request. Preserve original node and edge IDs through internal integer remapping. Parallel edges are distinct records. When a limit prevents satisfying the request, report an incomplete result even if some valid witnesses have already been found. A full answer that fits the limit can be complete. A complete negative is scoped to the requested operation on the declared projection; it is not an absence claim about the literature or the physical world.

Start with understandable CPU implementations and reuse established libraries where they fit. Add custom code for the requirements the applications need, rather than wrapping every algorithm offered by an upstream package. Alternative backends are optional and should be introduced after profiling projection, execution, witness reconstruction, and serialization together. NetworkX's official backend documentation illustrates both optional dispatch and the cost of conversions and caching. [N2]

The first contract is exact-only. Approximate ranking, personalized PageRank, learned graph reasoning, GPU execution, and generic AND/hypergraph execution are later extensions. Reaction-specific feasibility and kinetics remain domain responsibilities. A path through a species-connectivity graph is not a certificate that all reactants, quantities, sites, or kinetic conditions are satisfied.

## 3. What Hypotheses means in GRAPH

The H gives the shared library a concrete responsibility: make candidate explanations and proposed relationships portable and inspectable without turning them into established assertions.

A hypothesis candidate contains its statement and scope, input projection identity, producer and version, basis references, dependencies, assumptions, and at least one proposed test with discriminating outcomes. Optional ranking scores carry a method, version, candidate-set identity, direction, and interpretation. An uncalibrated score remains an ordering value. A probability interpretation requires a calibration reference, which a domain consumer must assess.

Candidate records have fixed `kind = hypothesis_candidate` and `epistemic_status = unverified`. Their lifecycle can be `active`, `withdrawn`, or `superseded`. These lifecycle values do not establish scientific truth. A withdrawal can reflect a change of scope or formulation; a revised dependency can make a candidate stale without proving it false.

Keep candidates in separate immutable records. The v0 execution projection accepts source records only and does not merge candidate relations into traversable edges. A future combined view needs an explicit contract for preserving candidate identity in every edge and witness. This restriction prevents a proposed link from silently becoming evidence during traversal.

Scientific assessment remains external to the generic candidate: a consumer can attach supporting, opposing, or inconclusive evidence under declared conditions. A later measurement or established assertion is a separate scientific record with its own provenance. GRAPH has no automatic "promote hypothesis to fact" operation.

The included example is synthetic: a revised input might change a model prediction's ranking. Its `0.8` priority score is arbitrary and is not an 80% probability. Its proposed recomputation is a plan, not a completed validation. The example exists to exercise transport and provenance, not to assert a scientific result.

## 4. How to begin in the existing ecosystem

Start by inspecting the actual checkout provided for implementation. Read its current `AGENTS.md`, `CLAUDE.md`, architecture, package layout, and required checks. Record the commit and the behavior being preserved. The instructions in this handoff do not replace the target repository's own instructions.

If a GRAPH checkout already exists, implement against it. Otherwise, begin with a separable internal boundary in the available consumer, normally SCALE, and make the code independent of that consumer's science modules. Create a separate repository when a real second workflow validates the same interface and independent releases become useful. Two repositories importing a trivial helper is not sufficient evidence; a synthetic example alone also does not establish the extraction criterion.

The inspected SCALE query implementation already provides indexed outgoing and incoming links, ancestor-aware label indexing, and lazy context caches. Its `run` entry point accepts a prebuilt graph, and reuse requires the same document and specification object identities. Preserve the immutability assumption and scientific comparison semantics. The work is not to replace missing adjacency indexes: they already exist. [N3]

The original source baseline is commit `fb0e285bf1865129548eeca802ab289b54db86ba`. A later read-only GitHub check observed main at `8632007a9c19735620facd5faa5f2188ef5efbb4`, with a changed schema manifest. The relevant inspected query code and instructions were unchanged in that comparison. This is a recorded observation, not a guarantee that main will remain there. Resolve the current schema and its digest before making an adapter. The exact observation is in `research/scale_remote_observation.json`.

Leave SCALE's scientific answer statuses with SCALE. The shared execution contract reports whether an operation completed, was interrupted, was unsupported, failed, or encountered a stale snapshot. It must not impose SCALE's `schema_gap` vocabulary on other consumers.

## 5. The first implementation deliverable

The first useful implementation should demonstrate one real read-only workflow with a verifiable result. For SCALE, a bounded evidence or dependency traversal is a suitable starting point if it is grounded in a real competency question. Return the original edge IDs and evidence mappings, and demonstrate parity with the existing application behavior.

The proposed work sequence is:

1. Record the baseline and agree on the small contract.
2. Implement the immutable view and four exact operations with structural witnesses.
3. Add candidate-hypothesis validation and transport without source mutation.
4. Add the first real consumer adapter and scientific parity fixtures.
5. Measure the whole request and evaluate one alternative backend only if needed.
6. Prepare the extraction path; exercise a real second consumer when one is available and use that evidence to assess a separately versioned package.

Each work item should produce reviewable code, a focused explanation, and evidence for its acceptance criteria. `docs/IMPLEMENTATION_PLAN.md` turns this sequence into six PR-sized work items. The first development session should implement the initial boundary and a useful vertical slice, rather than produce another broad survey or install all technologies mentioned in the research archive.

## 6. Required evidence before calling an implementation ready

- Exact results agree with independent small-graph checks and the relevant consumer's reference behavior.
- Witnesses survive parallel edges, cycles, reverse traversal, and repeated nodes in typed patterns.
- Large integer path costs preserve integer ordering; missing, negative, and non-finite costs are rejected under the initial contract.
- Output limits, cancellation, deadlines, and exhausted expansion budgets cannot produce certified negative answers.
- Reusing a projection is safe only while its source, schema/context bindings, and policy scope remain unchanged.
- Candidate serialization preserves unverified status, lifecycle history, assumptions, opposing evidence, and proposed tests.
- Source records are unchanged by execution and candidate creation.
- Performance reports include input preparation and witness work, workload identity, versions, raw results, and correctness parity.

The supplied contract checker validates JSON Schema and selected cross-record semantics on synthetic examples. Its independent tiny-graph oracle checks the example expectations; it is not the production runtime. The prior research archive contains separately tested reference algorithms. A passing handoff check is not proof that a new SCALE adapter, production service, or scientific model is correct.

## 7. Files Claude should read

Begin with `README.md`, `CLAUDE.md`, this note, and `docs/API_CONTRACT.md`. Then read `docs/IMPLEMENTATION_PLAN.md` and the integration note relevant to the checkout. Use `docs/HYPOTHESES_AND_PROVENANCE.md` when implementing the candidate contract. Read research chapters selectively for a concrete algorithm decision.

`prompts/CLAUDE_KICKOFF.md` is the implementation prompt. `verification/ACCEPTANCE_MATRIX.md` distinguishes supplied handoff checks from future runtime and integration gates. `verification/verification_summary.json` records what was actually run while preparing this package.

The embedded research archive remains unchanged, including its licenses, source catalog, original reference code, tests, and historical design proposals. The new GRAPH contracts govern this handoff; the older research package's experimental query envelope is background and does not supersede them.

## Sources

- **N1:** [Existing PyPI graph project](https://pypi.org/project/graph/).
- **N2:** [NetworkX backend documentation](https://networkx.org/documentation/stable/reference/backends.html).
- **N3:** [SCALE query implementation at the inspected baseline](https://github.com/hlxin/scale/blob/fb0e285bf1865129548eeca802ab289b54db86ba/tools/pgraph_query.py), [SCALE principles](https://github.com/hlxin/scale/blob/fb0e285bf1865129548eeca802ab289b54db86ba/PRINCIPLES.md), and [repository instructions](https://github.com/hlxin/scale/blob/fb0e285bf1865129548eeca802ab289b54db86ba/AGENTS.md).
- **N4:** [Claude Code project instructions](https://code.claude.com/docs/en/memory). The supplied `CLAUDE.md` is concise project context; the kickoff prompt points to detailed files as needed.

