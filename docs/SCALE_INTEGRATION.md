# SCALE integration

SCALE is GRAPH's first consumer. This document records the baseline the integration was built against, the adapter,
the two read-only workflows it runs, and their parity with SCALE's own behaviour. Nothing in SCALE was changed. The
adapter only reads SCALE documents and schemas, and the SCALE checkout's working tree was clean before and after
every run.

## Baseline (recorded 25 October 2026)

| What | Observed |
| --- | --- |
| Checkout | `../scale` (`hlxin/scale`), branch `agent/synthesis-conditions`, HEAD `287432646afec999e603d391f529595903481abe` (2026-10-25, "Conditions of a synthesis step, quantities of its inputs, varied preparations and recipes (schema 0.7.8)"), working tree clean |
| The note's baseline | `fb0e285bf1865129548eeca802ab289b54db86ba`, the local `main`, an ancestor of HEAD |
| Remote main | `origin/main` as last fetched here: `3572553e72ac8f8da24ee28413e59ebbe3e270ee`. The note's later observation `8632007a9c19735620facd5faa5f2188ef5efbb4` is not in this checkout (nothing was fetched) |
| Unchanged since `fb0e285` | `tools/pgraph_query.py`, `tools/lifecycle.py`, `tools/passages.py`, `AGENTS.md`, `PRINCIPLES.md` |
| Schema | head `scale-catal 0.7.8`, spec sha256 `135f9a2b5a5d238b91d733a119516ba67b72863cf117d99b854a22cff4507c1e`; versions 0.1.0 to 0.7.8 held. Each document is read under the version it pins: `example.json` pins 0.7.1, spec sha256 `b4ffe60d58b717196cf729b5806941b7cfc3b9337fc5c7c281b22e36591c706f` |

SCALE's query engine (`tools/pgraph_query.py`) already indexes outgoing and incoming links, indexes labels with
their ancestors, and caches what every plan of a document needs (`Graph`). `run` reuses a prebuilt `Graph` only
when it was built from the same document and schema objects. The integration does not replace any of this. It
shares execution where the operation and its guarantees are the same, and it relies on the same immutability
assumption: a projection describes one document under one schema and policy, and its bindings say which.

## The adapter

`integrations/scale/scale_graph_adapter.py` is application-owned. It reads SCALE's vocabulary so that the GRAPH
runtime never does. It is staged here until SCALE takes it in under its own review: an `agent/…` branch, a pull
request, and one person's approval.

| GRAPH | From the scale-catal document |
| --- | --- |
| node | every node; its types are its label and every label it `is_a` (the schema's ancestors), so a step's `to` matches as SCALE's `Graph.is_a` does |
| edge | every link; its relation is its type |
| declared relations and node types | the schema's, so a plan naming a declared type that no link uses gets a complete empty answer (SCALE: `no_matching_record`), and an undeclared one fails as `unknown_relation` (SCALE: `schema_gap`) |
| scope of a step | the step's edge type and every type that specializes it, forward for `out` and reverse for `in` |
| binding `source` | the document's `graph_id` and its digest, as a query result names it (`graph_sha256`) |
| binding `schema` | the pinned version and its spec digest |
| binding `policy` | the document's access level (`document_access`), so a lapsed embargo makes the projection stale |

## Workflow 1: evidence traversal along a competency question

`trace(doc, spec, plan)` takes a plan whose steps are one chain of edge steps. It runs the chain as a GRAPH typed
path from each matched record. Each record gets a witness (the original link IDs, in step order) or a complete
negative, plus the evidence those links cite (`source`, `locator`, `quote`) and the `result_id` of its result.
When a projection is supplied for reuse, `trace` checks the current document, schema and policy bindings before
execution. A mismatch produces `stale_snapshot` with no witness or evidence mapped from the changed document.

**Parity.** SCALE runs the plan's structural form (`structural`: the same match and steps, with filters, text,
date, retraction and own-work rules left out, and evidence on). GRAPH and SCALE must then agree on three things:

- GRAPH finds a witness exactly when SCALE returns a row for that record.
- The witness's links are the links of one of SCALE's rows.
- The evidence the adapter maps to them is the link evidence that row carries.

Results:

| Test | Scope | Result |
| --- | --- | --- |
| Competency questions | the 14 questions whose plans are one chain of edge steps, on their 29 question–document pairs; among them CQ-26 (three steps), CQ-29 and CQ-38 (two steps), and CQ-02 and CQ-11 (`CONTROL_FOR`, which `DARK_CONTROL_FOR` specializes) | agree on all 64 witnesses (with their evidence) and all 57 complete negatives |
| Generated chains | 1,654 one- and two-step chains, through each link type and the type it specializes, in both directions, on all 31 public example documents | agree on all 4,520 witnesses and all 2,126 complete negatives |

The other questions are not chains:

- 14 bind value entries or follow a field
- 8 branch
- 2 have an optional step
- 2 have no steps

A typed path does not express those shapes, and the adapter refuses them (`Unsupported`).

## Workflow 2: what rests on a retraction (dependency impact)

`resting_records(doc, spec)` finds the records that rest on retracted or withdrawn sources. It runs dependency
impact on a dependency projection of SCALE's source lifecycle (`retraction_dependencies`):

- a record depends on each evidence item it holds
- an evidence item depends on the source it cites, or on a dataset retraction that withdraws it (SCALE's
  `retracted_by`)
- a record depends on its inclusion in a retracted dataset that none of its cited sources belong to (SCALE's
  `unsourced_inclusions`)
- a record depends on the records it uses (`HAS_INPUT` and its specializations) and on the experiments its values
  name (`produced_by`)

Impact runs from everything retracted, to depth 3 (retraction, then item, then record, then a record that uses it):
SCALE's "one hop further". One impact per retraction attributes each record's retracted sources and datasets.

Which evidence a retracted dataset withdraws stays SCALE's rule, and so does what resting means for an answer
(`rests: all | some`, rows left out by default, labels). GRAPH only follows the dependencies, from what was
retracted to what depends on it, with a witness for each record.

| Test | Scope | Result |
| --- | --- | --- |
| The source-lifecycle example | `source-lifecycle/retracted-source.json` (CQ-05's document) | the same 4 records as `lifecycle.resting`, with the same retracted source |
| Injected retractions | the 134 documents SCALE reads that cite sources, each copied in memory twice: with notices retracting or withdrawing a third of the sources it cites, and with a partial retraction recorded as a dataset (`REPORTED_IN` a cited source, `INCLUDES` records that cite it and records that do not) | the same 18,951 records as `lifecycle.resting`, each with the same retracted sources and datasets (2,548 dataset attributions) |

## Cost

On the 29 competency-question requests, GRAPH takes 0.41 ms per document to build the projection and 0.48 ms per
query, against 0.51 ms for SCALE's own engine (`benchmarks/consumers.py`). The adapter computes SCALE's spec digest
once per schema object. SCALE's loaders hand out one object per version and treat it as unchanging, as its query
engine does when it reuses a `Graph`. Computing the digest on every call cost about 7 ms on average, up to 21 ms.

## What stays with SCALE

These are SCALE's scientific definitions and answer semantics, and GRAPH reproduces none of them:

- value filters, units, bases and contexts, and scientific comparison
- entries and field steps
- retraction, date and own-work rules on rows, and the answer statuses (`answered`, `no_matching_record`,
  `missing_context`, `schema_gap`)
- access and its limitations
- evidence interpretation

## Running the tests

```bash
PYTHONDONTWRITEBYTECODE=1 ../scale/.venv/bin/python -m pytest integrations/scale -p no:cacheprovider -s
```

The tests run in SCALE's environment, because they import `scale`. They read the checkout the imported package
comes from, or `SCALE_REPO`. They write nothing: no bytecode, no cache. They take about 45 seconds.

## Next

1. Propose the adapter to SCALE as a pull request under SCALE's process (an `agent/…` branch, `scale check`, one
   person's approval), with the parity tests as its fixtures.
2. Measure a whole SCALE request on a real competency question with both engines: projection, execution, witness
   reconstruction and serialization, with the workload's identity and versions.
3. Decide with SCALE whether tree-shaped plans (the 8 branching questions) need a GRAPH operation, or stay with
   SCALE's engine.
