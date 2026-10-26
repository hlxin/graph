# GRAPH

GRAPH (Graph Runtime for Analysis, Provenance, Hypotheses) is a small Python library: exact graph operations with
witnesses, for scientific applications (SCALE first). Start with the developer note in
`docs/GRAPH_Developer_Note_for_Claude.md`, then `docs/API_CONTRACT.md` and `docs/IMPLEMENTATION_PLAN.md`.

## Commands

```bash
python3 -m pytest                                    # runtime tests (needs pytest; networkx, jsonschema, numpy too)
PYTHONPATH=src python3 examples/synthetic_revision/run.py --check
python3 benchmarks/bench.py --sizes small,medium     # raw results go to benchmarks/runs/ (ignored)
PYTHONDONTWRITEBYTECODE=1 ../scale/.venv/bin/python -m pytest integrations/scale -p no:cacheprovider   # SCALE parity
PYTHONDONTWRITEBYTECODE=1 ../STATES/.venv/bin/python -m pytest integrations/states -p no:cacheprovider  # STATES parity
PYTHONDONTWRITEBYTECODE=1 ../STATES/.venv/bin/python benchmarks/consumers.py states   # both engines, real requests
```

## Layout

- `src/graph_runtime/`: the runtime. `projection` (immutable views, hierarchies, identity), `request`
  (normalization), `engine` (the five operations, limits), `result` (the result contract), `verify` (independent
  witness checks), `hypotheses` (candidates), `schemas/` (the JSON Schema), `conformance/` (hand-written backend
  cases), `retrieval` (optional text search, needs numpy).
- `tests/`: `oracle.py` holds the independent oracles and the random graph generator.
- `integrations/scale/`, `integrations/states/`: the SCALE and STATES adapters (application-owned, staged here) and
  their parity tests.

## Invariants

- The runtime is standard library only and never imports an application's vocabulary (SCALE, STATES, RATES,
  Cathanor). Application knowledge lives in that application's adapter. The one exception to standard library
  only is `retrieval`, which imports numpy inside its functions; nothing else imports `retrieval`.
- Exact only. An interrupted result never certifies a negative: `found: false`, `complete: true` and
  `reachable(x) is False` happen only when the search finished.
- Answers name original IDs. Parallel edges stay distinct. Order is canonical (ascending IDs), so the same
  projection and request give the same `result_id`; only `timing` is left out of it.
- A projection is immutable, and its identity covers its records, vocabularies and bindings. Consumer records are
  only read, never changed.
- Hypothesis candidates never enter a projection. Their `epistemic_status` is always `unverified`, and their history
  is append-only.

## Changing the contract

Change the code, `docs/API_CONTRACT.md` and `src/graph_runtime/schemas/graph-runtime-v0.schema.json` together.
Add conformance cases to `src/graph_runtime/conformance/cases.json` with expectations worked out by hand, never
copied from the engine's output. Raise `CONTRACT` in `projection.py` when a consumer could notice the change.

## Repository

`hlxin/graph` on GitHub (private). Commit or push only when asked. SCALE (`../scale`) and STATES (`../STATES`) are
read-only from here: run their code only with `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`, and never write
into them (other sessions may be working in them). A change to SCALE goes through SCALE's own process
(`../scale/AGENTS.md`).
