# Design

GRAPH (Graph Runtime for Analysis, Provenance, Hypotheses) is a small library that gives scientific applications
reliable graph execution. The applications stay independent: they keep the scientific definitions, domain rules,
authoritative records and workflows that make their results meaningful. GRAPH keeps one computational contract across
them: explicit inputs, supported operations, bounded work, reproducible outputs, and paths that can be checked against
the input records.

## The boundary

Graph execution is shared where the operation and its guarantees are the same. Scientific interpretation stays with
the application that owns it.

| Component | Responsibility |
| --- | --- |
| GRAPH | Read-only graph views, generic traversal, path and dependency witnesses, execution limits, result contracts, certificates and revalidation, candidate-hypothesis transport, and backend conformance |
| SCALE | Scientific schema, evidence, original values and units, comparability, source lifecycle, access policy, and scientific answer semantics |
| STATES | Structural and state identity, periodic and geometric representations, symmetry, equivalence, and valid state transitions |
| RATES | Reaction participation, stoichiometry, site accounting, energetic and kinetic conventions, mechanism compilation, numerical execution, and the scientific interpretation of pathways |
| Cathanor | Tasks, frozen inputs, reference answers, evaluation rules, leakage controls, and reproducibility |

The dependency direction is application, then an adapter the application owns, then GRAPH. The runtime never imports
an application's vocabulary: no SCALE labels, RATES rate laws or STATES structure definitions. A shared library does
not need a shared database or a network service; execution is local and in process.

## Principles

- **Explicit inputs.** Every operation binds an immutable projection and a normalized request. A projection keeps
  the original node and edge IDs (remapped to integers inside), keeps parallel edges as distinct records, and has an
  identity that covers its records, vocabularies and the bindings the consumer gives it (source, schema, policy). A
  projection is reused only while those bindings still match.
- **Exact only.** Costs are integers or exact rationals; weights that are only compared may be any finite number.
  Approximate ranking, learned graph reasoning and GPU execution are outside the contract.
- **Bounded work, honest results.** Expansion budgets, output limits, deadlines and cancellation stop a search. A
  limit that prevents completion makes the result `interrupted`, even when some witnesses were found, and an
  interrupted result never certifies a negative. A complete negative is scoped to the operation on the declared
  projection: it is not a claim about the literature or the physical world.
- **Checkable answers.** Every answer names original IDs and carries witnesses. On request, an answer carries a
  certificate that proves its negatives, optima or bounds against the records without a second search, and a cached
  answer can be revalidated against edited records.
- **Reproducible.** Order is canonical (ascending IDs), so the same projection and request give the same
  `result_id`.
- **Domain feasibility stays with the domain.** A path through a species-connectivity graph is not a certificate that
  every reactant, quantity, site or kinetic condition is satisfied; that judgement belongs to the application.

## Hypotheses

The H is a concrete responsibility: candidate explanations and proposed relationships are portable and inspectable,
without ever becoming established assertions. A candidate is always `unverified`, carries at least one proposed test
with discriminating outcomes, and is kept in separate immutable records that are never traversed as edges. GRAPH has
no operation that promotes a hypothesis to a fact; a later measurement is a separate scientific record with its own
provenance. [HYPOTHESES_AND_PROVENANCE.md](HYPOTHESES_AND_PROVENANCE.md) gives the contract.

## Implementation choices

The runtime is plain Python with the standard library only, so that it is a dependency any application can take on.
The one exception is the optional text search (`graph_runtime.retrieval`), which needs numpy and which nothing else
imports. Alternative backends are introduced only after profiling the whole request: building the projection,
execution, witness reconstruction and serialization together ([PERFORMANCE.md](PERFORMANCE.md)).

## Changing the contract

The contract is versioned (`CONTRACT` in `projection.py`, currently `graph-runtime/v0.2`). A change a consumer could
notice changes the code, [API_CONTRACT.md](API_CONTRACT.md) and the JSON Schema together, adds conformance cases whose
expectations are worked out by hand, and raises the contract version.
