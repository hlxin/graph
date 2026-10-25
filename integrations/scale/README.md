# SCALE adapter (staged)

`scale_graph_adapter.py` is SCALE's adapter to GRAPH. It belongs to SCALE: it reads SCALE's labels, edge types and
evidence, so that the GRAPH runtime never does. It is staged here until SCALE takes it in under its own review. It
only reads SCALE documents and schemas.

It provides two read-only workflows:

- `trace`: a competency question's chain of edge steps as a typed path, with original link IDs and their evidence.
- `resting_records`: the records resting on retracted sources, as dependency impact.

Both match SCALE's own engine and lifecycle on SCALE's documents. [docs/SCALE_INTEGRATION.md](../../docs/SCALE_INTEGRATION.md)
records the baseline, the mapping and the parity results.

```bash
PYTHONDONTWRITEBYTECODE=1 ../scale/.venv/bin/python -m pytest integrations/scale -p no:cacheprovider -s
```
