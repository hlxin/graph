"""Measure real consumer requests with both engines: the application's own code and GRAPH through the staged adapter,
on the application's own records, checking that both give the same answer.

    PYTHONDONTWRITEBYTECODE=1 ../STATES/.venv/bin/python benchmarks/consumers.py states
    PYTHONDONTWRITEBYTECODE=1 ../scale/.venv/bin/python benchmarks/consumers.py scale

Each run reads the consumer's checkout (read-only), takes the best of a few repetitions per request, and writes the
raw timings to benchmarks/runs/ (not committed). GRAPH's time is split into building the projection (once per
record, reusable by every request on it) and answering the requests, so that the two can be weighed separately.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

import graph_runtime  # noqa: E402
from graph_runtime import execute  # noqa: E402

REPEAT = 3


def best(fn):
    """(the best wall time of a few runs, the last result)."""
    times, out = [], None
    for _ in range(REPEAT):
        t = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t)
    return min(times), out


def states_rows() -> list[dict]:
    sys.path.insert(0, str(HERE.parent / "integrations" / "states"))
    import states
    import states_graph_adapter as adapter
    from states.context import extract_context
    from states.io import load_state
    repo = Path(states.__file__).resolve().parents[2]
    rows = []
    for path in sorted((repo / "examples" / "data").glob("*.states")) + \
            sorted((repo / "examples" / "demo_output").glob("*.states")):
        state = load_state(path)
        if not any(state["atoms"][s["atom"]]["role"] == "substrate" for c in state["contacts"] for s in c["sites"]):
            continue                              # the workflow's default roots: the deposited contacts
        refs, _ = adapter.roots_of(state)
        widest, _ = adapter.lifted_projection(state, refs, 3)          # one lift, reused for shells 1 to 3
        for shells in (1, 2, 3):
            native_s, want = best(lambda: extract_context(state, shells=shells))
            lift_s, (p, lift) = best(lambda: adapter.lifted_projection(state, refs, shells))
            sources = [adapter.image_id(a, img) for a, img in refs]
            query_s, res = best(lambda: execute(p, p.request("reachability", sources=sources, scope=adapter.SCOPE,
                                                             max_depth=shells, limits={"max_results": 1000})))
            whole_s, got = best(lambda: adapter.context(state, shells=shells))
            reused_s, again = best(lambda: execute(widest, widest.request(
                "reachability", sources=sources, scope=adapter.SCOPE, max_depth=shells, limits={"max_results": 1000})))
            wanted = {(n["source_atom"], tuple(n["image"])): n["shell"] for n in want["nodes"]}
            same = wanted == {(n["atom"], tuple(n["image"])): n["shell"] for n in got["nodes"]} == \
                {adapter.parse_image_id(e["node"]): e["depth"] for e in again.answer["reached"]}
            rows.append({"record": path.name, "shells": shells, "atom_images": len(want["nodes"]),
                         "lifted_nodes": p.node_count, "lifted_edges": p.edge_count, "native_s": native_s,
                         "graph_projection_s": lift_s, "graph_query_s": query_s, "graph_whole_s": whole_s,
                         "graph_query_on_reused_projection_s": reused_s, "same_answer": same})
    return rows


def scale_rows() -> list[dict]:
    sys.path.insert(0, str(HERE.parent / "integrations" / "scale"))
    import scale
    import scale_graph_adapter as adapter
    from scale import layout, lifecycle, pgraph, pgraph_query
    from scale.util import load_doc, load_yaml
    repo = Path(scale.__file__).resolve().parents[1]
    base = layout.domain_layers(repo)["catalysis"]
    manifest = layout.graph_manifests(repo, experimental=True)["catalysis"]
    rows = []
    for path in sorted((repo / "domain" / "competency").glob("*.yaml")):
        q = load_yaml(path) or {}
        if not q.get("plan"):
            continue
        try:
            adapter.chain(q["plan"], pgraph.load_spec(manifest))
        except adapter.Unsupported:
            continue
        for rel in q.get("documents") or []:
            doc = load_doc(base / rel)
            spec = pgraph.load_spec(pgraph.pinned(manifest, doc["graph"].get("schema_version"), doc))
            plan = adapter.structural(q["plan"])
            index_s, g = best(lambda: pgraph_query.Graph(doc, spec))
            native_s, want = best(lambda: pgraph_query.run(doc, spec, plan, None, g))
            build_s, p = best(lambda: adapter.projection_from_document(doc, spec))
            query_s, got = best(lambda: adapter.trace(doc, spec, q["plan"], p))
            root = (q["plan"].get("match") or {}).get("as", "self")
            rows_by_root = {row["bound"][root] for row in want["rows"]}
            same = {r["record"] for r in got["records"] if r["found"]} == rows_by_root
            rows.append({"workflow": "competency chain", "question": q.get("id"), "document": rel,
                         "records": len(doc["nodes"]) + len(doc["links"]), "roots": len(got["records"]),
                         "native_index_s": index_s, "native_s": native_s, "graph_projection_s": build_s,
                         "graph_query_s": query_s, "same_answer": same})
    for rel in ("examples/scale-catal/source-lifecycle/retracted-source.json",
                "examples/scale-catal/example.json"):
        doc = load_doc(base / rel)
        spec = pgraph.load_spec(pgraph.pinned(manifest, doc["graph"].get("schema_version"), doc))
        native_s, want = best(lambda: lifecycle.resting(doc, spec))
        whole_s, got = best(lambda: adapter.resting_records(doc, spec))
        rows.append({"workflow": "resting on retractions", "document": rel,
                     "records": len(doc["nodes"]) + len(doc["links"]), "native_s": native_s,
                     "graph_whole_s": whole_s, "same_answer": set(got["records"]) == set(want)})
    return rows


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("states", "scale"):
        print(__doc__)
        return 2
    rows = states_rows() if argv[0] == "states" else scale_rows()
    report = {"consumer": argv[0], "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "versions": {"graph_runtime": graph_runtime.__version__, "contract": graph_runtime.CONTRACT,
                           "python": platform.python_version(), "platform": platform.platform()},
              "rows": rows}
    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    path = runs / f"{argv[0]}-{report['started'].replace(':', '').replace('-', '')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{argv[0]}: {len(rows)} requests, all answers the same: {all(r['same_answer'] for r in rows)}")
    for workflow in sorted({r.get("workflow", "context") for r in rows}):
        group = [r for r in rows if r.get("workflow", "context") == workflow]
        total, count = Counter(), Counter()
        for r in group:
            for k, v in r.items():
                if k.endswith("_s"):
                    total[k] += v
                    count[k] += 1
        print(f"  {workflow} ({len(group)} requests), mean per request:")
        for k in sorted(total):
            print(f"    {k:36} {total[k] / count[k] * 1000:9.2f} ms")
    print(f"raw results: {path}")
    return 0 if all(r["same_answer"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
