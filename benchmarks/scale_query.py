"""GRAPH against SCALE's own query engine, on SCALE's documents, with row-for-row parity asserted on every query.

Run it with SCALE's environment (SCALE is only read)::

    PYTHONDONTWRITEBYTECODE=1 ../scale/.venv/bin/python benchmarks/scale_query.py [--repeat 3] [--copies 16]

SCALE answers a plan with ``scale.pgraph_query.run(doc, spec, structural(plan), graph=index)``: every row with its
evidence (and a deep copy of the answer, which its API returns). GRAPH answers the same plan as a pattern request
through the SCALE adapter (``integrations/scale/scale_graph_adapter.py``), listing rows and, separately, counting
them without listing. Both reuse their per-document structure (SCALE's ``Graph`` index, GRAPH's projection), whose
build time is reported apart. Every query's rows must be the same, in the same order; a query SCALE cannot express
(a schema gap) is skipped by both. Each time is the best of ``--repeat`` runs.

Workloads: the competency questions (each question on each of its documents, edge-step plans); every generated one-
and two-step plan on the public methanol-synthesis example (2,851 nodes, 9,335 links) and on the full pre-1966 graph
(4,866 nodes, 16,584 links); and the public example copied ``--copies`` times (disjoint copies, IDs suffixed), every
generated plan again. Results go to benchmarks/runs/ (ignored) as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "integrations" / "scale")]

import scale_graph_adapter as adapter  # noqa: E402
from graph_runtime import COMPLETED, execute  # noqa: E402


def best(fn, repeat: int) -> tuple[float, object]:
    out, times = None, []
    for _ in range(repeat):
        start = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - start)
    return min(times), out


def generated(doc: dict, spec: dict) -> list[dict]:
    """Every one- and two-step chain over the link types a document uses, in both directions and through the edge
    type each one specializes, matched by the labels at their ends (the parity tests' generator, without its cap)."""
    label = {n["id"]: n["label"] for n in doc["nodes"]}
    seen, plans, out_of = set(), [], {}
    for e in doc["links"]:
        out_of.setdefault(label[e["source"]], set()).add((e["type"], label[e["target"]]))
    for e in sorted(doc["links"], key=lambda e: e["id"]):
        parent = (spec["edges"].get(e["type"]) or {}).get("specializes")
        for edge in [e["type"]] + ([parent] if parent else []):
            for direction, start, end in (("out", e["source"], e["target"]), ("in", e["target"], e["source"])):
                key = (edge, direction, label[start], label[end])
                if key in seen:
                    continue
                seen.add(key)
                step = {"from": "x", "edge": edge, "direction": direction, "to": label[end], "as": "y"}
                plans.append({"match": {"label": label[start], "as": "x"}, "steps": [step]})
                if direction == "out":
                    for edge2, to2 in sorted(out_of.get(label[end], ())):
                        plans.append({"match": {"label": label[start], "as": "x"},
                                      "steps": [step, {"from": "y", "edge": edge2, "to": to2, "as": "z"}]})
    return plans


def copies(doc: dict, n: int) -> dict:
    """``n`` disjoint copies of a document's nodes and links, every ID suffixed by its copy."""
    out = {k: v for k, v in doc.items() if k not in ("nodes", "links")}
    out["nodes"] = [{**x, "id": f"{x['id']}~{k}"} for k in range(n) for x in doc["nodes"]]
    out["links"] = [{**e, "id": f"{e['id']}~{k}", "source": f"{e['source']}~{k}", "target": f"{e['target']}~{k}"}
                    for k in range(n) for e in doc["links"]]
    return out


def compare(name: str, doc: dict, spec: dict, plans: list[dict], run, Graph, repeat: int) -> dict:
    """Both engines on every plan of one document, parity asserted; totals and per-query times."""
    t_index, index = best(lambda: Graph(doc, spec), repeat)
    t_proj, p = best(lambda: adapter.projection_from_document(doc, spec), repeat)
    queries, skipped = [], 0
    for plan in plans:
        try:
            request = adapter.pattern_request(plan, spec)
        except adapter.Unsupported:
            skipped += 1
            continue
        t_scale, answer = best(lambda: run(doc, spec, adapter.structural(plan), graph=index), repeat)
        if answer["status"] == "schema_gap":
            skipped += 1
            continue
        want = [(dict(row["bound"]), list(row["links"])) for row in answer["rows"]]
        rows = {**request, "projection": p.identity, "limits": {"max_results": 10**7, "max_expansions": 10**9}}
        t_rows, res = best(lambda: execute(p, rows), repeat)
        assert res.status == COMPLETED, (name, plan, res.error)
        got = [(dict(row["nodes"]), [x for x in row["links"] if x is not None]) for row in res.answer["rows"]]
        assert got == want, (name, plan)                  # the same rows in the same order
        t_count, counted = best(lambda: execute(p, {**rows, "answer": "count"}), repeat)
        assert counted.answer["total"] == len(want), (name, plan)
        queries.append({"steps": len(plan["steps"]), "rows": len(want), "scale": t_scale, "graph": t_rows,
                        "graph_count": t_count})
    total = {k: sum(q[k] for q in queries) for k in ("scale", "graph", "graph_count")}
    return {"name": name, "nodes": len(doc["nodes"]), "links": len(doc["links"]), "queries": len(queries),
            "skipped": skipped, "rows": sum(q["rows"] for q in queries), "scale_index": t_index,
            "graph_projection": t_proj, "total": total,
            "median": {k: statistics.median(q[k] for q in queries) for k in ("scale", "graph", "graph_count")}
            if queries else None,
            "largest": max(queries, key=lambda q: q["rows"]) if queries else None,
            "never_slower": sum(q["graph"] <= q["scale"] for q in queries), "per_query": queries}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--copies", type=int, default=16)
    args = ap.parse_args()
    import scale
    from scale import layout, pgraph, pgraph_query
    from scale.util import load_doc, load_yaml
    repo = Path(os.environ.get("SCALE_REPO") or Path(scale.__file__).resolve().parents[1])
    base = layout.domain_layers(repo)["catalysis"]
    manifest = layout.graph_manifests(repo, experimental=True)["catalysis"]

    def spec_of(doc):
        return pgraph.load_spec(pgraph.pinned(manifest, (doc.get("graph") or {}).get("schema_version"), doc))
    run, Graph = pgraph_query.run, pgraph_query.Graph
    suites = []
    started = time.time()

    # Competency questions: each on each of its documents, one suite per pair, folded into one
    cq = []
    for path in sorted((repo / "domain" / "competency").glob("*.yaml")):
        q = load_yaml(path) or {}
        steps = (q.get("plan") or {}).get("steps") or []
        if not steps or any("edge" not in st for st in steps):
            continue
        for rel in q.get("documents") or []:
            doc = load_doc(base / rel)
            cq.append(compare(f"{path.stem} on {rel}", doc, spec_of(doc), [q["plan"]], run, Graph, args.repeat))
    queries = [x for s in cq for x in s["per_query"]]
    suites.append({"name": "competency questions", "pairs": len(cq), "queries": len(queries),
                   "rows": sum(q["rows"] for q in queries),
                   "total": {k: sum(q[k] for q in queries) for k in ("scale", "graph", "graph_count")},
                   "median": {k: statistics.median(q[k] for q in queries) for k in ("scale", "graph", "graph_count")},
                   "never_slower": sum(q["graph"] <= q["scale"] for q in queries), "per_query": queries})
    print(f"competency questions: {len(queries)} queries", flush=True)

    public = load_doc(repo / "examples" / "methanol-synthesis-public")
    full = load_doc(repo / layout.GRAPHS / "methanol-synthesis-pre1966")
    for name, doc in (("methanol synthesis, public example", public), ("methanol synthesis, full pre-1966 graph", full),
                      (f"methanol synthesis, public example x{args.copies}", copies(public, args.copies))):
        spec = spec_of(doc)
        suites.append(compare(name, doc, spec, generated(doc, spec), run, Graph, args.repeat))
        s = suites[-1]
        print(f"{name}: {s['queries']} queries, {s['rows']} rows, SCALE {s['total']['scale']:.2f} s, "
              f"GRAPH {s['total']['graph']:.2f} s (count {s['total']['graph_count']:.2f} s)", flush=True)

    out = {"kind": "graph_runtime.benchmark.scale_query", "started": started, "repeat": args.repeat,
           "python": platform.python_version(), "machine": platform.machine(), "load": os.getloadavg(),
           "scale_commit": os.popen(f"git -C {repo} rev-parse --short HEAD").read().strip(),
           "graph_commit": os.popen(f"git -C {ROOT} rev-parse --short HEAD").read().strip(), "suites": suites}
    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    target = runs / f"scale_query-{time.strftime('%Y%m%d-%H%M%S')}.json"
    target.write_text(json.dumps(out, indent=1))
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
