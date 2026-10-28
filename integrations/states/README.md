# STATES adapter (staged)

`states_graph_adapter.py` is STATES's adapter to GRAPH. It belongs to STATES: it reads STATES records (atoms and
roles, periodic edges and their shifts, adsorption contacts), so that the GRAPH runtime never does. It is staged here
until STATES takes it in under its own review. It only reads STATES records.

`context` answers STATES's context extraction (the substrate atom images within some shells of a binding site, with
their relations and boundary ports) as bounded reachability over the substrate graph, lifted to a finite box of
periodic images. It matches `states.context.extract_context` on STATES's records.
[docs/STATES_INTEGRATION.md](../../docs/STATES_INTEGRATION.md) describes the mapping and the parity results.

```bash
PYTHONDONTWRITEBYTECODE=1 ../STATES/.venv/bin/python -m pytest integrations/states -p no:cacheprovider -s
```
