# Synthetic revision example

Invented records: an input table, a model that depends on it, two predictions, a ranking of them and a report
(`records.json`). They describe nothing real. The example exercises transport and provenance; it asserts no
scientific result.

`run.py` does four things:

1. Builds a projection of the records.
2. Asks which records depend on the revised input (`dependency_impact`), with a witness for each:
   `out/impact_result.json`.
3. Records a hypothesis candidate: the revision might change the order of the two predictions in the ranking
   (`out/candidate.json`).
   - Its basis is the impact result and the ranking's witness.
   - Its `0.8` priority is an arbitrary ordering value, not an 80 % probability.
   - Its proposed recomputation is a plan, not a completed validation.
   - It is `unverified`, as every candidate is.
4. Asks whether the candidate is stale after a later change to the model: it is, with the witnesses that say why
   (`out/staleness_after_model_change.json`). Stale means re-examine, not false.

```bash
PYTHONPATH=src python examples/synthetic_revision/run.py           # rewrite out/
PYTHONPATH=src python examples/synthetic_revision/run.py --check   # fail if out/ differs from a fresh run
```
