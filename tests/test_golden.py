"""Golden results: the engine's answers, witnesses, certificates and counters must not change unnoticed.

``golden_results.json`` holds the ``result_id`` of every result of a fixed random workload (random graphs, every
operation, with and without limits, certificates, hierarchies, integer and float costs). A result_id digests
everything a result says except its timing, so any change in an answer, a witness choice, a certificate, a statistic or an error shows up here. An
optimization must keep every one; a deliberate change of behaviour regenerates the file (``python
tests/test_golden.py --write``) and says why in its commit.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden_results.json"
RELATIONS = ("R", "S", "T")
TYPES = ("A", "B", "C")
ID_POOL = ["a", "b", "c", "B", "Z", "10", "9", "é", "ä", "n-1", "n_2", "x:y", "長", "σ", "0"]


def graph(rng: random.Random) -> tuple[list, list, dict]:
    ids = rng.sample(ID_POOL, rng.randint(1, rng.choice([4, 8, 14])))
    nodes = []
    for nid in ids:
        types = rng.sample(TYPES, rng.randint(0, 2))
        nodes.append(nid if not types and rng.random() < 0.3 else {"id": nid, "types": types})
    edges = []
    for i in range(rng.randint(0, rng.choice([6, 18, 40]))):
        if edges and rng.random() < 0.2:                 # a parallel edge
            twin = rng.choice(edges)
            src, dst, rel = twin["source"], twin["target"], twin["relation"]
        else:
            src, dst, rel = rng.choice(ids), rng.choice(ids), rng.choice(RELATIONS)
        edges.append({"id": f"e{rng.randrange(10**6):06d}-{i}", "source": src, "target": dst, "relation": rel})
    rng.shuffle(edges)
    hierarchies = {}
    if rng.random() < 0.3:
        hierarchies["relation_parents"] = {"S": ["R"]}
    if rng.random() < 0.3:
        hierarchies["type_parents"] = {"B": ["A"]}
    return nodes, edges, hierarchies


def scope(rng: random.Random) -> list[dict]:
    pairs = [(r, d) for r in RELATIONS for d in ("forward", "reverse")]
    return [{"relation": r, "direction": d} for r, d in rng.sample(pairs, rng.randint(1, len(pairs)))]


def numbers(rng: random.Random, edges: list, negative: bool) -> dict:
    def num():
        if rng.random() < 0.4:
            x = rng.choice([0.5, 0.1, 1.25, 3.0, 2.2, 1e-3, 7.75, 0.3])
            return -x if negative and rng.random() < 0.3 else x
        return rng.randint(-3 if negative else 0, 9)
    if rng.random() < 0.5:
        table = {r: num() for r in RELATIONS}
        if rng.random() < 0.1:
            table.pop(rng.choice(RELATIONS))
        return {"by_relation": table}
    table = {e["id"]: num() for e in edges}
    if table and rng.random() < 0.1:
        table.pop(rng.choice(list(table)))
    return {"by_edge": table}


def some(rng: random.Random, nodes: list, k: int = 3) -> list[str]:
    pool = [n if isinstance(n, str) else n["id"] for n in nodes]
    return rng.sample(pool, rng.randint(1, min(k, len(pool))))


def request(rng: random.Random, nodes: list, edges: list) -> dict:
    op = rng.choice(["reachability", "dependency_impact", "typed_path", "shortest_path", "pattern", "support",
                     "bottleneck_path"])
    limits = {}
    if rng.random() < 0.3:
        limits["max_expansions"] = rng.randint(0, 6)
    if op in ("reachability", "dependency_impact", "pattern", "support") and rng.random() < 0.3:
        limits["max_results"] = rng.randint(0, 5)
    r: dict = {"operation": op, "limits": limits}
    if op in ("reachability", "dependency_impact"):
        r["sources" if op == "reachability" else "changed"] = some(rng, nodes)
        r["scope"] = scope(rng)
        if rng.random() < 0.3:
            r["max_depth"] = rng.randint(0, 3)
    elif op == "typed_path":
        r["sources"] = some(rng, nodes)
        r["pattern"] = [{"scope": scope(rng),
                         "node_types": rng.sample(TYPES, rng.randint(1, 2)) if rng.random() < 0.4 else None}
                        for _ in range(rng.randint(1, 3))]
        if rng.random() < 0.6:
            r["targets"] = some(rng, nodes)
        r["certificate"] = rng.random() < 0.6
    elif op == "shortest_path":
        r.update(sources=some(rng, nodes), targets=some(rng, nodes), scope=scope(rng),
                 costs=numbers(rng, edges, False), certificate=rng.random() < 0.6)
    elif op == "bottleneck_path":
        r.update(sources=some(rng, nodes), targets=some(rng, nodes), scope=scope(rng),
                 weights=numbers(rng, edges, True), certificate=rng.random() < 0.6,
                 objective=rng.choice(["max_min", "min_max"]))
    elif op == "pattern":
        r["match"] = ({"var": "x", "node_types": rng.sample(TYPES, 1)} if rng.random() < 0.5
                      else {"var": "x", "nodes": some(rng, nodes)})
        steps, names = [], ["x"]
        for i in range(rng.randint(0, 3)):
            st = {"from": rng.choice(names), "var": f"v{i}", "scope": scope(rng)}
            if rng.random() < 0.3:
                st["node_types"] = rng.sample(TYPES, 1)
            if rng.random() < 0.2 and edges:
                st["edges"] = [e["id"] for e in rng.sample(edges, rng.randint(1, len(edges)))]
            if rng.random() < 0.3:
                st["edge_var"] = f"e{i}"
            steps.append(st)
            names.append(f"v{i}")
        r["steps"] = steps
        r["answer"] = rng.choice(["rows", "count", "exists"])
    else:
        r.update(failed=some(rng, nodes), scope=scope(rng), all_of=rng.sample(TYPES, 1) if rng.random() < 0.5 else None)
    return r


def workload(graphs: int = 400, per_graph: int = 3, seed: int = 20261026):
    """(graph number, request number, nodes, edges, hierarchies, request) for the fixed workload."""
    rng = random.Random(seed)
    for g in range(graphs):
        nodes, edges, hierarchies = graph(rng)
        for k in range(per_graph):
            yield g, k, nodes, edges, hierarchies, request(rng, nodes, edges)


def results(module) -> dict[str, str]:
    """``{"graph/request": result_id}`` of the workload, run by ``module`` (a graph_runtime package)."""
    out, projection = {}, None
    for g, k, nodes, edges, hierarchies, raw in workload():
        if k == 0:
            projection = module.Projection.build(nodes=nodes, edges=edges, **hierarchies)
        out[f"{g}/{k}"] = module.execute(projection, {**raw, "projection": projection.identity}).result_id
    return out


def test_every_result_is_the_golden_one():
    import graph_runtime
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))["results"]
    got = results(graph_runtime)
    changed = sorted((k for k in want if got.get(k) != want[k]), key=lambda k: tuple(map(int, k.split("/"))))
    assert len(got) == len(want) and not changed, f"{len(changed)} results changed, first: {changed[:5]}"


def test_a_projection_reused_by_later_requests_gives_the_same_results():
    """The engine keeps compiled hop lists on a projection: a request that runs after others on the same projection
    (and reads what they compiled) gives the result a fresh projection gives."""
    import graph_runtime
    fresh = {}
    shared = None
    for g, k, nodes, edges, hierarchies, raw in workload(graphs=60, per_graph=6, seed=7):
        if k == 0:
            shared = graph_runtime.Projection.build(nodes=nodes, edges=edges, **hierarchies)
        alone = graph_runtime.Projection.build(nodes=nodes, edges=edges, **hierarchies)
        req = {**raw, "projection": alone.identity}
        fresh[(g, k)] = graph_runtime.execute(alone, req).result_id
        assert graph_runtime.execute(shared, req).result_id == fresh[(g, k)]
        assert graph_runtime.execute(shared, req).result_id == fresh[(g, k)]


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        raise SystemExit("usage: python tests/test_golden.py --write   (regenerates golden_results.json)")
    sys.path.insert(0, str(HERE.parent / "src"))
    import graph_runtime
    GOLDEN.write_text(json.dumps({"contract": graph_runtime.CONTRACT, "workload": "tests/test_golden.py:workload()",
                                  "results": results(graph_runtime)}, indent=0, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"wrote {GOLDEN}")
