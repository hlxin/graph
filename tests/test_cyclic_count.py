"""Counting a pattern with cycles without listing its rows: variable elimination over a tree decomposition, a generic
join inside each bag. The oracles are independent of the engine: numpy's einsum over hop-count matrices built from the
records (a row is a choice of one hop per step, so the total is a sum of products of those counts), brute-force
enumeration for small graphs, the trace of a power of the adjacency matrix for closed walks, and networkx's triangle
counts."""

import random
import time

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from graph_runtime.result import contract_problems
from oracle import RELATIONS, TYPES, node_id, pattern_rows, random_graph, random_scope

np = pytest.importorskip("numpy")


def hop_counts(ids, edges, scope):
    """M[u][w]: the hops in scope from u to w, one per edge and end (a self-loop in both directions once)."""
    at = {x: k for k, x in enumerate(ids)}
    fwd = {x["relation"] for x in scope if x["direction"] == "forward"}
    rev = {x["relation"] for x in scope if x["direction"] == "reverse"}
    m = np.zeros((len(ids), len(ids)), dtype=np.int64)
    for e in edges:
        a, b = at[e["source"]], at[e["target"]]
        if e["relation"] in fwd:
            m[a, b] += 1
        if e["relation"] in rev and not (a == b and e["relation"] in fwd):
            m[b, a] += 1
    return m


def admits(nodes, spec):
    types = {node_id(n): set(() if isinstance(n, str) else n["types"]) for n in nodes}
    return np.array([int((spec.get("node_types") is None or bool(types[node_id(n)] & set(spec["node_types"])))
                         and (spec.get("nodes") is None or node_id(n) in spec["nodes"])) for n in nodes],
                    dtype=np.int64)


def einsum_total(nodes, edges, match, steps):
    """The number of rows of a pattern of required steps: one index per variable, a 0/1 vector per node constraint,
    a hop-count matrix per step (its diagonal for a loop), summed over every index."""
    ids = [node_id(n) for n in nodes]
    letter = {match["var"]: "a"}
    operands, terms = [admits(nodes, match)], ["a"]
    for s in steps:
        m = hop_counts(ids, edges, s["scope"])
        if "to" in s:
            if s["from"] == s["to"]:
                operands.append(np.diagonal(m).copy())
                terms.append(letter[s["from"]])
            else:
                operands.append(m)
                terms.append(letter[s["from"]] + letter[s["to"]])
            continue
        letter[s["var"]] = chr(ord("a") + len(letter))
        operands += [m, admits(nodes, s)]
        terms += [letter[s["from"]] + letter[s["var"]], letter[s["var"]]]
    return int(np.einsum(",".join(terms) + "->", *operands, optimize=True))


def random_required(rng, ids, n_vars, n_closing):
    """A random pattern of required steps: a tree over n_vars variables, then closing steps (loops and repeats
    included), each after both its variables are bound."""
    names, steps = ["v0"], []
    for i in range(1, n_vars):
        steps.append({"from": rng.choice(names), "var": f"v{i}", "scope": random_scope(rng),
                      "node_types": rng.choice([None, None, None, rng.sample(TYPES, min(2, len(TYPES)))]),
                      "nodes": None if rng.random() < 0.85 else rng.sample(ids, max(1, len(ids) // 2))})
        names.append(f"v{i}")
    for _ in range(n_closing):
        a, b = rng.choice(names), rng.choice(names)
        later = max(names.index(a), names.index(b))
        bound = 0 if later == 0 else 1 + next(k for k, s in enumerate(steps) if s.get("var") == names[later])
        steps.insert(rng.randint(bound, len(steps)), {"from": a, "to": b, "scope": random_scope(rng)})
    match = {"var": "v0", "node_types": None, "nodes": None}
    if rng.random() < 0.5:
        match["node_types"] = rng.sample(TYPES, min(2, len(TYPES)))
    else:
        match["nodes"] = rng.sample(ids, rng.randint(1, len(ids)))
    return match, steps


def test_counts_agree_with_einsum_over_hop_matrices():
    rng = random.Random("count by elimination")
    nonzero = big = 0
    for _ in range(250):
        nodes, edges = random_graph(rng, max_nodes=rng.choice([6, 10, 15]), max_edges=rng.choice([20, 40, 70]))
        ids = [node_id(n) for n in nodes]
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        match, steps = random_required(rng, ids, rng.randint(2, 6), rng.randint(1, 4))
        want = einsum_total(nodes, edges, match, steps)
        for answer in ("count", "exists"):
            res = execute(p, p.request("pattern", match=match, steps=steps, answer=answer))
            assert res.status == COMPLETED, res.error
            assert res.answer["total"] == want and res.answer["exists"] is (want > 0), (match, steps)
            assert contract_problems(res.to_dict()) == []
        nonzero += want > 0
        big += want > 10_000
    assert nonzero > 100 and big > 10, (nonzero, big)


def test_counts_with_optional_branches_agree_with_brute_force():
    from test_cyclic_pattern import random_cyclic
    rng = random.Random("count optional")
    seen = 0
    for _ in range(300):
        nodes, edges = random_graph(rng, max_nodes=6, max_edges=16)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        match, steps = random_cyclic(rng, [node_id(n) for n in nodes])
        want = pattern_rows(nodes, edges, match, steps)
        if len(want) > 50_000:
            continue
        res = execute(p, p.request("pattern", match=match, steps=steps, answer="count"))
        assert res.status == COMPLETED and res.answer["total"] == len(want), (match, steps)
        seen += any(s.get("optional") for s in steps) and bool(want)
    assert seen > 20


def test_rows_stop_at_max_results_and_the_total_is_still_exact():
    rng = random.Random("count rows")
    limited = 0
    for _ in range(300):
        nodes, edges = random_graph(rng, max_nodes=7, max_edges=20)
        ids = [node_id(n) for n in nodes]
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        match, steps = random_required(rng, ids, rng.randint(2, 4), rng.randint(1, 2))
        want = pattern_rows(nodes, edges, match, steps)
        if len(want) > 50_000:
            continue
        cap = rng.randint(0, 6)
        res = execute(p, p.request("pattern", match=match, steps=steps, limits={"max_results": cap}))
        assert [(dict(r["nodes"]), list(r["links"])) for r in res.answer["rows"]] == \
            [(dict(b), links) for b, links in want][:cap]
        assert res.answer["total"] == len(want)
        if len(want) > cap:
            assert res.status == INTERRUPTED and res.interruption["reason"] == "output_limit"
            limited += 1
        else:
            assert res.status == COMPLETED
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
    assert limited > 50


def test_triangles_agree_with_networkx():
    nx = pytest.importorskip("networkx")
    rng = random.Random("triangles")
    for _ in range(10):
        g = nx.gnm_random_graph(200, 1500, seed=rng.randrange(10**6))
        nodes = [f"n{v:03d}" for v in g]
        edges = [{"id": f"e{k:05d}", "source": f"n{a:03d}", "target": f"n{b:03d}", "relation": "R"}
                 for k, (a, b) in enumerate(g.edges())]
        p = Projection.build(nodes=nodes, edges=edges)
        both = [("R", "forward"), ("R", "reverse")]
        res = execute(p, p.request("pattern", match={"var": "a", "nodes": nodes}, answer="count", steps=[
            {"from": "a", "var": "b", "scope": both}, {"from": "b", "var": "c", "scope": both},
            {"from": "c", "to": "a", "scope": both}]))
        assert res.answer["total"] == 2 * sum(nx.triangles(g).values())     # each triangle, from each corner, both ways


def test_closed_walks_on_a_dense_graph_are_counted_not_listed():
    """Closed walks of length four in the complete bipartite graph K(n, n), both directions: trace(A**4) = 2 n**4,
    26 million rows for n = 60, counted in a fraction of a second; and listed only up to max_results."""
    n = 60
    left, right = [f"l{i:02d}" for i in range(n)], [f"r{i:02d}" for i in range(n)]
    edges = [{"id": f"{a}-{b}", "source": a, "target": b, "relation": "R"} for a in left for b in right]
    p = Projection.build(nodes=left + right, edges=edges)
    both = [("R", "forward"), ("R", "reverse")]
    steps = [{"from": "a", "var": "b", "scope": both}, {"from": "b", "var": "c", "scope": both},
             {"from": "c", "var": "d", "scope": both}, {"from": "d", "to": "a", "scope": both}]
    start = time.perf_counter()
    res = execute(p, p.request("pattern", match={"var": "a", "nodes": left + right}, answer="count", steps=steps))
    assert res.status == COMPLETED and res.answer["total"] == 2 * n ** 4
    assert res.stats["expansions"] < 10 ** 6 and time.perf_counter() - start < 5     # listing them took about 53 million
    res = execute(p, p.request("pattern", match={"var": "a", "nodes": left + right}, steps=steps,
                               limits={"max_results": 3}))
    assert res.answer["total"] == 2 * n ** 4 and res.answer["count"] == 3
    assert res.interruption["reason"] == "output_limit" and res.stats["expansions"] < 10 ** 6


def test_a_long_ring_is_counted_by_its_cycle():
    """A ring of 300 variables closed by one step, over a directed ring of 300 nodes: one closed walk of length 300
    from each node; elimination keeps every bag at three variables, and nothing recurses."""
    n = 300
    nodes = [f"x{i:03d}" for i in range(n)]
    edges = [{"id": f"r{i:03d}", "source": nodes[i], "target": nodes[(i + 1) % n], "relation": "R"} for i in range(n)]
    p = Projection.build(nodes=nodes, edges=edges)
    steps = [{"from": f"v{i}", "var": f"v{i + 1}", "scope": [("R", "forward")]} for i in range(n - 1)]
    steps.append({"from": f"v{n - 1}", "to": "v0", "scope": [("R", "forward")]})
    start = time.perf_counter()
    res = execute(p, p.request("pattern", match={"var": "v0", "nodes": nodes}, answer="count", steps=steps,
                               limits={"max_expansions": 10 ** 7}))
    assert res.status == COMPLETED and res.answer["total"] == n
    assert time.perf_counter() - start < 10


def test_an_interrupted_count_claims_nothing():
    rng = random.Random("count interrupted")
    seen = set()
    for _ in range(300):
        nodes, edges = random_graph(rng, max_nodes=8, max_edges=24)
        ids = [node_id(n) for n in nodes]
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        match, steps = random_required(rng, ids, rng.randint(2, 5), rng.randint(1, 3))
        want = einsum_total(nodes, edges, match, steps)
        res = execute(p, p.request("pattern", match=match, steps=steps, answer=rng.choice(["count", "exists"]),
                                   limits={"max_expansions": rng.randint(0, 60)}))
        assert contract_problems(res.to_dict()) == []
        if res.status == INTERRUPTED:
            assert res.answer["exists"] is None and res.answer["total"] is None
        else:
            assert res.answer["total"] == want
        seen.add(res.status)
    assert seen == {COMPLETED, INTERRUPTED}


def hub_pattern(shape, n):
    """Patterns over one node with one loop, every one with one row: a hub variable with n spokes (a star: the spokes
    are leaves; a bouquet: each also closes back to the hub; a fan: each also closes to the next), or K(m, n): m hubs,
    every spoke closing to every one of them."""
    fwd = [("R", "forward")]
    if shape.startswith("K"):
        m = int(shape[1:])
        steps = [{"from": "h0", "var": f"s{i:04d}", "scope": fwd} for i in range(n)]
        steps += [{"from": "s0000", "var": f"h{j}", "scope": fwd} for j in range(1, m)]
        steps += [{"from": f"s{i:04d}", "to": f"h{j}", "scope": fwd} for i in range(1, n) for j in range(1, m)]
        return "h0", steps
    steps = [{"from": "hub", "var": f"s{i:06d}", "scope": fwd} for i in range(n)]
    if shape == "bouquet":
        steps += [{"from": f"s{i:06d}", "to": "hub", "scope": fwd} for i in range(n)]
    if shape == "fan":
        steps += [{"from": f"s{i:06d}", "to": f"s{i + 1:06d}", "scope": fwd} for i in range(n - 1)]
    steps.append({"from": "hub", "to": "hub", "scope": fwd})
    return "hub", steps


@pytest.mark.parametrize("shape,n", [("star", 3200), ("bouquet", 3200), ("fan", 3200), ("K2", 800), ("K17", 400)])
def test_planning_a_hub_costs_its_spokes_and_checks_the_controls(shape, n):
    """Choosing the elimination order must not weigh every pair of a hub's neighbours each time, nor work long between
    two checks of the deadline and the cancellation: every step of the planning is charged to the budget first."""
    p = Projection.build(nodes=["n"], edges=[{"id": "loop", "source": "n", "target": "n", "relation": "R"}])
    var, steps = hub_pattern(shape, n)
    request = p.request("pattern", match={"var": var, "nodes": ["n"]}, answer="count", steps=steps)
    stamps = []
    start = time.perf_counter()
    res = execute(p, request, check_interval=1, cancel=lambda: stamps.append(time.perf_counter()) and False)
    assert res.status == COMPLETED and res.answer["total"] == 1
    assert time.perf_counter() - start < 10
    assert max(b - a for a, b in zip(stamps, stamps[1:])) < 1.0          # about 0.3 s at most, on a loaded machine
    small = execute(p, p.request("pattern", match={"var": var, "nodes": ["n"]}, answer="count", steps=steps,
                                 limits={"max_expansions": res.stats["expansions"] // 2}))
    assert small.status == INTERRUPTED and small.answer["total"] is None


def test_a_wide_bag_spends_its_budget_before_ordering_it():
    from graph_runtime.engine import _Control, _Stop, _sum_join
    wide = tuple(range(8000))
    ctl = _Control(0, None, time.monotonic, None, 64)
    start = time.perf_counter()
    with pytest.raises(_Stop):
        _sum_join([(wide, {tuple(range(8000)): 1})], set(wide), wide[1:], dict.fromkeys(wide, 1), ctl)
    assert time.perf_counter() - start < 0.5 and ctl.expansions == 0


def test_a_batch_of_expansions_checks_like_one_at_a_time():
    """take(k) is k calls of stop(): the same budget stop, the same checks of the deadline and the cancellation at
    every interval the batch crosses, the same expansions counted when one stops it."""
    from graph_runtime.engine import _Control
    rng = random.Random("take")
    for _ in range(3000):
        budget, interval, fire = rng.randint(0, 60), rng.randint(1, 7), rng.randint(1, 40)
        batches = [rng.randint(0, 25) for _ in range(rng.randint(1, 8))]
        runs = []
        for batched in (True, False):
            calls = []
            ctl = _Control(budget, None, time.monotonic, lambda: calls.append(1) or len(calls) >= fire, interval)
            why = None
            for k in batches:
                if batched:
                    why = ctl.take(k)
                else:
                    for _ in range(k):
                        why = ctl.stop()
                        if why:
                            break
                if why:
                    break
            runs.append((why, ctl.expansions, len(calls)))
        assert runs[0] == runs[1], (budget, interval, fire, batches)


def test_a_cancellation_inside_a_batch_interrupts_the_count():
    n = 1000
    nodes = [f"x{i:04d}" for i in range(n)]
    edges = [{"id": f"l{i:04d}", "source": x, "target": x, "relation": "R"} for i, x in enumerate(nodes)]
    p = Projection.build(nodes=nodes, edges=edges)
    request = p.request("pattern", match={"var": "x", "nodes": nodes}, answer="count", steps=[
        {"from": "x", "to": "x", "scope": [("R", "forward")]}])
    done = execute(p, request)
    assert done.status == COMPLETED and done.answer["total"] == n
    calls = []
    limit = done.stats["expansions"] - 3                          # the cancellation fires before the last checks
    res = execute(p, request, check_interval=1, cancel=lambda: calls.append(1) or len(calls) > limit)
    assert res.status == INTERRUPTED and res.interruption["reason"] == "cancelled"
    assert res.answer["total"] is None and res.answer["exists"] is None


def test_the_setup_is_charged_before_it_is_built():
    """30,000 spokes over one node: a budget spent by the discovery and the index stops the count before its factors
    are built, so it takes no longer than a budget spent before the index (both build the same result)."""
    n = 30_000
    p = Projection.build(nodes=["n"], edges=[{"id": "loop", "source": "n", "target": "n", "relation": "R"}])
    var, steps = hub_pattern("star", n)

    def timed(budget):
        request = p.request("pattern", match={"var": var, "nodes": ["n"]}, answer="count", steps=steps,
                            limits={"max_expansions": budget})
        best = None
        for _ in range(2):
            start = time.perf_counter()
            res = execute(p, request)
            took = time.perf_counter() - start
            best = took if best is None else min(best, took)
        assert res.status == INTERRUPTED and res.answer["total"] is None
        return best
    assert timed(n + 1) - timed(n) < 0.5                    # n expansions discover the spokes, one indexes the loop


def test_a_pattern_too_dense_to_decompose_is_counted_by_listing():
    """A clique of 34 variables over one node with a loop: every bag would hold 33 neighbours, so the count lists the
    one row instead; listed one past max_results, it is counted the same way."""
    m = 34
    p = Projection.build(nodes=["n"], edges=[{"id": "loop", "source": "n", "target": "n", "relation": "R"}])
    fwd = [("R", "forward")]
    steps = [{"from": "v00", "var": f"v{i:02d}", "scope": fwd} for i in range(1, m)]
    steps += [{"from": f"v{i:02d}", "to": f"v{j:02d}", "scope": fwd} for i in range(1, m) for j in range(i + 1, m)]
    for answer, limits in (("count", {}), ("rows", {"max_results": 0}), ("rows", {})):
        res = execute(p, p.request("pattern", match={"var": "v00", "nodes": ["n"]}, answer=answer, steps=steps,
                                   limits=limits))
        assert res.answer["total"] == 1 and res.answer["exists"] is True
        assert res.status == (INTERRUPTED if limits else COMPLETED)
        assert contract_problems(res.to_dict()) == []


def test_a_missed_loop_ends_the_count_before_any_product():
    """One node with two parallel loops, and a pattern of one unmatched loop and 50,000 matched ones: the count is
    zero, found before 2**50,000 is built; when 10,000 loops all match, it is 2**10,000 (3,011 digits, within what
    JSON writes), and the controls are checked between the products."""
    n = 50_000
    p = Projection.build(nodes=["n"], edges=[{"id": "a", "source": "n", "target": "n", "relation": "R"},
                                             {"id": "b", "source": "n", "target": "n", "relation": "R"}],
                         relations=["R", "S"])
    loops = [{"from": "x", "to": "x", "scope": [("R", "forward")]}] * n
    missed = [{"from": "x", "to": "x", "scope": [("S", "forward")]}]
    for steps, want in ((missed + loops, 0), (loops[:10_000], 2 ** 10_000)):
        stamps = []
        start = time.perf_counter()
        res = execute(p, p.request("pattern", match={"var": "x", "nodes": ["n"]}, answer="count", steps=steps,
                                   limits={"max_expansions": 10 ** 7}),
                      check_interval=1, cancel=lambda: stamps.append(time.perf_counter()) and False)
        assert res.status == COMPLETED and res.answer["total"] == want
        assert max(b - a for a, b in zip(stamps, stamps[1:])) < 0.5
        if not want:
            assert time.perf_counter() - start < 5
