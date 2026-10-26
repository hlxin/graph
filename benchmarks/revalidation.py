"""Measure revalidation against recomputation: cached results on a large projection, a small edit, and the time to
decide which cached results still hold (with one diff, or by checking each answer in full) against the time to run
every request again. Every verdict of "holds" is checked against the fresh run.

    python benchmarks/revalidation.py                 # 100,000 nodes, 500,000 edges, 40 cached results
    python benchmarks/revalidation.py --nodes 10000 --edges 50000

Raw results go to benchmarks/runs/revalidation-<UTC time>.json (not committed).
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

import graph_runtime  # noqa: E402
from bench import RELATIONS, workload  # noqa: E402
from graph_runtime import Projection, execute  # noqa: E402
from graph_runtime.revalidate import HOLDS, diff, revalidate  # noqa: E402


def answer_of(res):
    a = res.answer
    if res.operation == "reachability":
        return {e["node"]: e["depth"] for e in a["reached"]}
    return a["found"], a["cost"]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--nodes", type=int, default=100_000)
    ap.add_argument("--edges", type=int, default=500_000)
    ap.add_argument("--cached", type=int, default=40)
    ap.add_argument("--edit", type=int, default=20, help="edges added and as many removed")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)
    _, nodes, edges, _ = workload(args.nodes, args.edges, args.seed)
    t = time.perf_counter()
    p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=["A", "B"])
    build_s = time.perf_counter() - t
    ids = [n["id"] for n in nodes]
    scope = [{"relation": r, "direction": "forward"} for r in RELATIONS[:2]]
    raws = []
    for k in range(args.cached):
        if k % 2 == 0:
            raws.append({"operation": "reachability", "sources": [rng.choice(ids)], "scope": scope, "max_depth": 4})
        else:
            raws.append({"operation": "shortest_path", "sources": [rng.choice(ids)], "targets": [rng.choice(ids)],
                         "scope": scope, "costs": {"by_relation": {r: 1 + i for i, r in enumerate(RELATIONS)}},
                         "certificate": True})
    cached = [execute(p, {**raw, "projection": p.identity}) for raw in raws]
    removed = set(rng.sample(range(len(edges)), args.edit))
    edited = [e for j, e in enumerate(edges) if j not in removed]
    edited += [{"id": f"x{j:05d}", "source": rng.choice(ids), "target": rng.choice(ids),
                "relation": rng.choice(RELATIONS)} for j in range(args.edit)]
    t = time.perf_counter()
    q = Projection.build(nodes=nodes, edges=edited, relations=RELATIONS, node_types=["A", "B"])
    rebuild_s = time.perf_counter() - t
    t = time.perf_counter()
    d = diff(p, q)
    diff_s = time.perf_counter() - t
    t = time.perf_counter()
    fast = [revalidate(r, q, diff=d) for r in cached]
    fast_s = time.perf_counter() - t
    t = time.perf_counter()
    full = [revalidate(r, q) for r in cached]
    full_s = time.perf_counter() - t
    t = time.perf_counter()
    fresh = [execute(q, {**raw, "projection": q.identity}) for raw in raws]
    fresh_s = time.perf_counter() - t
    agree = all(a.verdict == b.verdict for a, b in zip(fast, full))
    sound = all(answer_of(f) == answer_of(c) for v, c, f in zip(fast, cached, fresh) if v.verdict == HOLDS)
    verdicts = {v: sum(1 for x in fast if x.verdict == v) for v in ("holds", "fails", "unknown")}
    answer_sizes = [len(r.answer["reached"]) for r in cached if r.operation == "reachability"]
    report = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "versions": {"graph_runtime": graph_runtime.__version__, "contract": graph_runtime.CONTRACT,
                           "python": platform.python_version(), "platform": platform.platform()},
              "workload": {"generator": "bench.workload (uniform-multigraph)", "nodes": args.nodes, "edges": args.edges,
                           "seed": args.seed, "cached": args.cached, "edit": f"+{args.edit} -{args.edit} edges"},
              "build_s": build_s, "rebuild_s": rebuild_s, "diff_s": diff_s, "revalidate_with_diff_s": fast_s,
              "revalidate_in_full_s": full_s, "recompute_s": fresh_s, "verdicts": verdicts,
              "mean_reached": sum(answer_sizes) / max(1, len(answer_sizes)),
              "diff_and_full_agree": agree, "holds_matches_fresh": sound}
    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    out = runs / f"revalidation-{report['started'].replace(':', '').replace('-', '')}.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{args.nodes:,} nodes, {args.edges:,} edges; {args.cached} cached results (mean reachability answer "
          f"{report['mean_reached']:.0f} nodes); edit +{args.edit}/-{args.edit} edges")
    print(f"  verdicts {verdicts}; diff and full checks agree: {agree}; every 'holds' matches a fresh run: {sound}")
    print(f"  projection build {build_s:.2f} s, rebuild {rebuild_s:.2f} s")
    print(f"  diff {diff_s * 1000:.1f} ms, then revalidation {fast_s * 1000:.2f} ms "
          f"({fast_s / args.cached * 1e6:.0f} µs per result)")
    print(f"  revalidation in full {full_s * 1000:.1f} ms; running every request again {fresh_s * 1000:.1f} ms")
    print(f"raw results: {out}")
    return 0 if agree and sound else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
