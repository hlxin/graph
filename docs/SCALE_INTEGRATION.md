# SCALE integration

SCALE is GRAPH's first consumer. This document records the baseline the integration was built against, the adapter,
the four read-only workflows it runs, and their parity with SCALE's own behaviour. Nothing in SCALE was changed. The
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

A typed path does not express those shapes, and the adapter refuses them (`Unsupported`). Workflow 3 runs every
plan whose steps are edge steps, whatever its shape.

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

Which evidence a retracted dataset withdraws stays SCALE's rule, and so does what resting means for an answer (rows
left out by default, labels). Impact only follows the dependencies, from what was retracted to what depends on it,
with a witness for each record.

**All or in part (support).** `resting_support(doc, spec)` also answers SCALE's `rests: all | some`, in one
`support` request on a support projection of the same records (`support_projection`):

- A record stands on any one of its items of support: its evidence items, its inclusions in retracted datasets,
  and, one hop further, those of the records it uses. Each is an edge to the used record's items, never to the
  record itself, so support goes no deeper than SCALE's.
- An evidence item stands only on all of its supports (`all_of`): the source it cites, and any retracted dataset
  that withdraws that source's evidence of its record.
- What was retracted fails. A `lost` record rests on retractions entirely (`all`); a `weakened` one rests on them in
  part (`some`). Each one's `causes` are its retracted sources and datasets.

| Test | Scope | Result |
| --- | --- | --- |
| The source-lifecycle example | `source-lifecycle/retracted-source.json` (CQ-05's document) | the same 4 records as `lifecycle.resting`, with the same retracted source |
| Injected retractions | the 134 documents SCALE reads that cite sources, each copied in memory twice: with notices retracting or withdrawing a third of the sources it cites, and with a partial retraction recorded as a dataset (`REPORTED_IN` a cited source, `INCLUDES` records that cite it and records that do not) | the same 18,951 records as `lifecycle.resting`, each with the same retracted sources and datasets (2,548 dataset attributions); through support, the same verdict for each (18,501 `all`, 450 `some`) |

Support is generic, and on SCALE's documents it costs more than SCALE's own one-level computation. On the 134
documents with injected retractions, `lifecycle.resting` takes 57 ms in all. `resting_support` takes 1.65 s,
mostly building the support projection and reading the answer. On the largest document (296 nodes, 1,355 links),
SCALE takes 3.4 ms; GRAPH takes 33 ms, of which 11 ms is the request. What support adds is the general rule. Support
propagates to any depth, through AND and OR nodes and through cycles, with witnesses and causes, and `verify_result`
recomputes it from the records.

## Workflow 3: tree-shaped plans (pattern)

`pattern_request(plan, spec)` maps a plan whose steps are all edge steps, of any tree shape and with optional
steps, to a GRAPH `pattern` request:

- the match's label (with its subtypes, through the projection's type hierarchy) or its id
- each step's edge type with every type that specializes it (the relation hierarchy), in its direction
- each step's `to` label, `edge_as` and `optional`

**Parity.** On every competency question whose plan has only edge steps, GRAPH's rows are SCALE's structural rows,
in SCALE's order: the same bound records and the same links, row for row. That is 39 question–document pairs and
203 rows, among them 8 tree-shaped plans and 3 with optional steps. GRAPH's exact `total` equals SCALE's row count.
Value filters, entries and field steps stay SCALE's. A consumer passes the records its own filters keep to GRAPH as
a step's `nodes` or `edges`.

## Workflow 4: evidence search, and text stages

SCALE's evidence search (`tools/passages.py`) is GRAPH's optional retrieval module ([RETRIEVAL.md](RETRIEVAL.md)).
The adapter supplies SCALE's words:

- `tokenizer()`: SCALE's word pattern, formula aliases and spelling variants
- `evidence_index(doc)`: a GRAPH index of the document's evidence items (SCALE's `evidence_items`)
- `search(...)`: hits as SCALE reports them, with scores rounded to four places
- `text_ranked(...)`: a plan's text stage on the rows that reach it

**Parity**, on the public methanol-synthesis example (55 sources, 2,851 nodes, 9,335 links, 12,802 evidence
quotes):

- GRAPH's vectors and inverted index are SCALE's, byte for byte.
- BM25 and cosine scores are SCALE's, bit for bit.
- 1,980 searches (four modes, three values of k, with and without filters) return 130,173 hits, all SCALE's, in
  SCALE's order.
- The three manuscript questions with a text stage (q2, q3, q5) rank the same claims with the same text scores.
- 235 page texts split into SCALE's 1,350 passages, which index and search alike.

GRAPH builds the index in 0.38 s against SCALE's 0.93 s, because it encodes each distinct quote once. Searches take
the same time.

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
comes from, or `SCALE_REPO`. They write nothing: no bytecode, no cache. They take about 50 seconds.

## Next

1. Propose the adapter to SCALE as a pull request under SCALE's process (an `agent/…` branch, `scale check`, one
   person's approval), with the parity tests as its fixtures.
2. Measure a whole SCALE request on a real competency question with both engines: projection, execution, witness
   reconstruction and serialization, with the workload's identity and versions.
3. Decide with SCALE which of its engines it hands to GRAPH: tree-shaped plans (Workflow 3) and evidence search
   (Workflow 4) now match SCALE's own, row for row and hit for hit.
