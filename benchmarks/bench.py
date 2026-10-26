"""Measure whole requests: input preparation (projection build), binding, search, witness reconstruction, result
making and JSON serialization, on synthetic workloads named by their generator, parameters and seed, with the
versions that ran them and correctness parity against NetworkX.

    python benchmarks/bench.py                      # sizes small and medium
    python benchmarks/bench.py --sizes small,medium,large

Raw results go to benchmarks/runs/<UTC time>.json (not committed). Numbers are for comparing runs on one machine,
not for claims about other machines.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

import graph_runtime  # noqa: E402
from graph_runtime import Projection, execute  # noqa: E402

SIZES = {"small": (1_000, 5_000), "medium": (10_000, 50_000), "large": (100_000, 500_000)}
RELATIONS = ("R0", "R1", "R2", "R3")


def workload(n: int, m: int, seed: int) -> tuple[dict, list, list]:
    """A random directed multigraph: n nodes, m edges with uniformly random ends (self-loops and parallel edges
    allowed), relations uniform over four, integer costs 1 to 100 per edge."""
    rng = random.Random(seed)
    nodes = [{"id": f"n{i:07d}", "types": [rng.choice(("A", "B"))]} for i in range(n)]
    edges = [{"id": f"e{j:08d}", "source": f"n{rng.randrange(n):07d}", "target": f"n{rng.randrange(n):07d}",
              "relation": rng.choice(RELATIONS)} for j in range(m)]
    costs = {e["id"]: rng.randint(1, 100) for e in edges}
    return {"generator": "uniform-multigraph", "nodes": n, "edges": m, "relations": len(RELATIONS),
            "seed": seed}, nodes, edges, costs


def requests(nodes: list, rng: random.Random, costs: dict) -> list[dict]:
    src, dst = nodes[0]["id"], nodes[rng.randrange(len(nodes))]["id"]
    fwd = [{"relation": r, "direction": "forward"} for r in RELATIONS]
    return [
        {"operation": "reachability", "sources": [src], "scope": fwd},
        {"operation": "dependency_impact", "changed": [src], "scope": [{"relation": r, "direction": "reverse"}
                                                                      for r in RELATIONS]},
        {"operation": "typed_path", "sources": [src],
         "pattern": [{"scope": fwd[:2]}, {"scope": fwd[2:]}, {"scope": fwd, "node_types": ["B"]}]},
        {"operation": "shortest_path", "sources": [src], "targets": [dst], "scope": fwd, "costs": {"by_edge": costs}},
    ]


def parity(req: dict, res, nodes: list, edges: list, costs: dict) -> dict:
    """The answer against NetworkX (reachability, impact, shortest path) or plain layered sets (typed path)."""
    op = req["operation"]
    if op == "typed_path":
        types = {n["id"]: set(n["types"]) for n in nodes}
        frontier = set(req["sources"])
        for pos in req["pattern"]:
            fwd = {s["relation"] for s in pos["scope"] if s["direction"] == "forward"}
            rev = {s["relation"] for s in pos["scope"] if s["direction"] == "reverse"}
            want = set(pos.get("node_types") or ())
            reached = {e["target"] for e in edges if e["relation"] in fwd and e["source"] in frontier}
            reached |= {e["source"] for e in edges if e["relation"] in rev and e["target"] in frontier}
            frontier = {v for v in reached if not want or types[v] & want}
        return {"checked": True, "agrees": res.found is bool(frontier)}
    try:
        import networkx as nx
    except ImportError:
        return {"checked": False, "why": "networkx not installed"}
    g = nx.MultiDiGraph()
    g.add_nodes_from(n["id"] for n in nodes)
    reverse = op == "dependency_impact"
    for e in edges:
        u, v = (e["target"], e["source"]) if reverse else (e["source"], e["target"])
        g.add_edge(u, v, key=e["id"], cost=costs[e["id"]])
    if op == "shortest_path":
        try:
            want = nx.dijkstra_path_length(g, req["sources"][0], req["targets"][0],
                                           weight=lambda u, v, d: min(x["cost"] for x in d.values()))
        except nx.NetworkXNoPath:
            want = None
        return {"checked": True, "agrees": res.answer["cost"] == want}
    seeds = req["changed"] if reverse else req["sources"]
    if reverse:                                  # at least one hop: successors of the seeds, then their descendants
        want = set()
        for s in seeds:
            for nb in g.successors(s):
                want |= {nb} | nx.descendants(g, nb)
    else:
        want = set(seeds) | {v for s in seeds for v in nx.descendants(g, s)}
    key = "affected" if reverse else "reached"
    return {"checked": True, "agrees": {e["node"] for e in res.answer[key]} == want}


def run(sizes: list[str], seed: int) -> dict:
    out = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "versions": {"graph_runtime": graph_runtime.__version__, "contract": graph_runtime.CONTRACT,
                        "python": platform.python_version(), "implementation": platform.python_implementation(),
                        "platform": platform.platform(), "machine": platform.machine()},
           "workloads": []}
    try:
        import networkx
        out["versions"]["networkx"] = networkx.__version__
    except ImportError:
        pass
    for size in sizes:
        n, m = SIZES[size]
        ident, nodes, edges, costs = workload(n, m, seed)
        t0 = time.perf_counter()
        p = Projection.build(nodes=nodes, edges=edges, bindings={"source": f"{ident['generator']}:{seed}"})
        build_s = time.perf_counter() - t0
        entry = {"size": size, "workload": {**ident, "projection": p.identity}, "build_s": build_s, "requests": []}
        rng = random.Random(seed + 1)
        for raw in requests(nodes, rng, costs):
            limits = {"max_results": n + 1} if raw["operation"] in ("reachability", "dependency_impact") else {}
            res = execute(p, {**raw, "projection": p.identity, "limits": limits})
            if res.answer is None:
                raise SystemExit(f"{raw['operation']} was not run: {dict(res.error)}")
            t1 = time.perf_counter()
            text = res.to_json()
            json_s = time.perf_counter() - t1
            t2 = time.perf_counter()
            problems = graph_runtime.verify_result(res, nodes=nodes, edges=edges)
            verify_s = time.perf_counter() - t2
            entry["requests"].append({
                "operation": raw["operation"], "request_id": res.request_id, "result_id": res.result_id,
                "status": res.status, "stats": dict(res.stats), "timing": dict(res.timing), "json_s": json_s,
                "json_bytes": len(text.encode()), "verify_s": verify_s, "verified": not problems,
                "parity": parity(raw, res, nodes, edges, costs)})
        out["workloads"].append(entry)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", default="small,medium", help=f"comma-separated, of {', '.join(SIZES)}")
    ap.add_argument("--seed", type=int, default=20261025)
    args = ap.parse_args(argv)
    report = run([s.strip() for s in args.sizes.split(",") if s.strip()], args.seed)
    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    path = runs / (report["started"].replace(":", "").replace("-", "") + ".json")
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{'size':7} {'operation':18} {'status':10} {'expansions':>10} {'build':>8} {'bind':>8} {'search':>8} "
          f"{'witness':>8} {'result':>8} {'json':>8}  parity")
    for w in report["workloads"]:
        for r in w["requests"]:
            t = r["timing"]
            print(f"{w['size']:7} {r['operation']:18} {r['status']:10} {r['stats']['expansions']:>10} "
                  f"{w['build_s']:8.3f} {t['bind_s']:8.4f} {t['search_s']:8.4f} {t['witness_s']:8.4f} "
                  f"{t['result_s']:8.4f} {r['json_s']:8.4f}  "
                  f"{'ok' if r['parity'].get('agrees') and r['verified'] else r['parity']}")
    print(f"raw results: {path}")
    return 0 if all(r["verified"] and r["parity"].get("checked") and r["parity"].get("agrees") for w in report["workloads"]
                    for r in w["requests"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
