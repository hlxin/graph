"""Reachability between many pairs, from a 2-hop index (pruned landmark labelling). networkx is the oracle, on the
hops the scope allows read from the records; every witness and every closure is checked by the verifier; the same
request gets the same result whether the index is built for it or was kept from an earlier one."""

import copy
import random
import time

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from oracle import RELATIONS, TYPES, hops_of, node_id, random_graph, random_scope
from test_certificates import reseal
from test_revalidate import edited

nx = pytest.importorskip("networkx")


def reaches(nodes, edges, scope):
    """Every node each node reaches (itself included), by networkx over the hops in scope."""
    g = nx.DiGraph()
    g.add_nodes_from(node_id(n) for n in nodes)
    g.add_edges_from((u, v) for u, v, _ in hops_of(edges, scope))
    return {u: nx.descendants(g, u) | {u} for u in g}


def run(rng, n=8, m=18, k=None):
    nodes, edges = random_graph(rng, max_nodes=n, max_edges=m)
    ids = [node_id(x) for x in nodes]
    pairs = [(rng.choice(ids), rng.choice(ids)) for _ in range(k or rng.randint(1, 12))]
    raw = {"operation": "reachability_pairs", "pairs": pairs, "scope": random_scope(rng),
           "witnesses": rng.random() < 0.8, "certificate": rng.random() < 0.6}
    return nodes, edges, raw


def build(nodes, edges):
    return Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)


def decided(res):
    return [(x["source"], x["target"], x["reachable"]) for x in res.answer["pairs"]]


def test_every_pair_agrees_with_networkx_and_proves_itself():
    rng = random.Random("reachability pairs")
    tally = {"reachable": 0, "unreachable": 0, "self": 0}
    for _ in range(600):
        nodes, edges, raw = run(rng)
        p = build(nodes, edges)
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        closure = reaches(nodes, edges, raw["scope"])
        want = sorted(set(raw["pairs"]))
        assert decided(res) == [(s, t, t in closure[s]) for s, t in want]
        for s, t, ok in decided(res):
            tally["self" if s == t else "reachable" if ok else "unreachable"] += 1
    assert min(tally.values()) >= 300, tally


def test_larger_graphs_agree_with_networkx():
    rng = random.Random("reachability pairs, larger")
    for _ in range(12):
        n = rng.choice([200, 500])
        ids = [f"v{i:04d}" for i in range(n)]
        edges = [{"id": f"e{k:05d}", "source": rng.choice(ids), "target": rng.choice(ids),
                  "relation": rng.choice(["R", "S"])} for k in range(rng.choice([n, 2 * n, 3 * n]))]
        scope = rng.choice([[("R", "forward")], [("R", "forward"), ("S", "reverse")], [("R", "forward"), ("S", "forward")]])
        pairs = [(rng.choice(ids), rng.choice(ids)) for _ in range(300)]
        p = Projection.build(nodes=ids, edges=edges)
        res = execute(p, p.request("reachability_pairs", pairs=pairs, scope=scope, certificate=True))
        assert res.status == COMPLETED and verify_result(res, nodes=ids, edges=edges) == []
        closure = reaches(ids, edges, [{"relation": r, "direction": d} for r, d in scope])
        assert decided(res) == [(s, t, t in closure[s]) for s, t in sorted(set(pairs))]


def test_a_kept_index_gives_the_same_result_as_a_new_one():
    """The index is charged in full either way: the same expansions, status and answer, for every budget."""
    rng = random.Random("reachability pairs, cold and warm")
    seen = set()
    for _ in range(150):
        nodes, edges, raw = run(rng)
        budget = rng.randint(0, 120)
        request = {**raw, "limits": {"max_expansions": budget}}
        warm = build(nodes, edges)
        execute(warm, {**raw, "projection": warm.identity})            # builds and keeps the index
        cold = build(nodes, edges)
        assert cold.identity == warm.identity and not cold._memo.get("reach_index")
        a = execute(cold, {**request, "projection": cold.identity})
        b = execute(warm, {**request, "projection": warm.identity})
        assert a.result_id == b.result_id and a.stats == b.stats and a.status == b.status
        assert verify_result(a, nodes=nodes, edges=edges) == [] and contract_problems(a.to_dict()) == []
        seen.add((a.status, a.answer["index"] is None))
    assert seen >= {(COMPLETED, False), (INTERRUPTED, True), (INTERRUPTED, False)}, seen


def test_an_interrupted_answer_lists_decided_pairs_in_order():
    rng = random.Random("reachability pairs, interrupted")
    partial = 0
    for _ in range(300):
        nodes, edges, raw = run(rng)
        p = build(nodes, edges)
        full = execute(p, {**raw, "projection": p.identity})
        res = execute(p, {**raw, "projection": p.identity,
                          "limits": {"max_expansions": rng.randint(0, full.stats["expansions"])}})
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        assert decided(res) == decided(full)[:len(decided(res))]
        if res.status == INTERRUPTED:
            assert res.answer["certificate"] is None
            partial += 0 < res.answer["count"]
    assert partial >= 30


def test_forged_answers_are_refused():
    nodes = ["a", "b", "c", "d", "x"]
    edges = [{"id": "e1", "source": "a", "target": "b", "relation": "R"},
             {"id": "e2", "source": "b", "target": "c", "relation": "R"},
             {"id": "e3", "source": "c", "target": "a", "relation": "R"},
             {"id": "e4", "source": "c", "target": "d", "relation": "R"},
             {"id": "e5", "source": "x", "target": "d", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("reachability_pairs", pairs=[("a", "d"), ("d", "a"), ("x", "c"), ("b", "b")],
                               scope=[("R", "forward")], certificate=True))
    assert decided(res) == [("a", "d", True), ("b", "b", True), ("d", "a", False), ("x", "c", False)]
    assert verify_result(res, nodes=nodes, edges=edges) == []
    assert [list(c["nodes"]) for c in res.answer["certificate"]["closures"]] == [["d"], ["d", "x"]]

    def forged(change):
        data = copy.deepcopy(thaw(res.to_dict()))
        change(data["answer"])
        return verify_result(reseal(data), nodes=nodes, edges=edges)

    def denied(a):                                          # a reachable pair said not to be, with a closure that leaks
        a["pairs"][0].update(reachable=False, witness=None)
        a["reachable"] = 1
        a["certificate"]["closures"].insert(0, {"source": "a", "nodes": ["a", "b"]})
    assert forged(denied)
    assert forged(lambda a: a["certificate"]["closures"][1].__setitem__("nodes", ["x"]))      # not closed
    assert forged(lambda a: a["certificate"]["closures"][1]["nodes"].append("c"))             # holds its target
    assert forged(lambda a: a["certificate"]["closures"].pop())                               # a closure missing
    assert forged(lambda a: a["pairs"][0]["witness"]["hops"][1].__setitem__("edge", "e5"))
    assert forged(lambda a: a["pairs"][1].__setitem__("reachable", False))                    # b does reach b
    assert forged(lambda a: a["pairs"].reverse())


def test_witnesses_are_simple_paths_through_cycles():
    """A ring of five with a chord: a hub's walk out of the source and into the target can meet itself; the loop is
    cut, so every witness visits no node twice."""
    ids = [f"r{i}" for i in range(5)]
    edges = [{"id": f"e{i}", "source": ids[i], "target": ids[(i + 1) % 5], "relation": "R"} for i in range(5)]
    edges.append({"id": "chord", "source": "r3", "target": "r1", "relation": "R"})
    p = Projection.build(nodes=ids, edges=edges)
    pairs = [(a, b) for a in ids for b in ids]
    res = execute(p, p.request("reachability_pairs", pairs=pairs, scope=[("R", "forward")]))
    assert res.answer["reachable"] == 25 and verify_result(res, nodes=ids, edges=edges) == []


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("reachability pairs revalidation")
    tally = {}
    for _ in range(300):
        nodes, edges, raw = run(rng)
        raw["certificate"] = rng.random() < 0.7
        nodes2, edges2 = edited(rng, nodes, edges)
        ends = {x for pair in raw["pairs"] for x in pair}
        if not ends <= {node_id(n) for n in nodes2}:
            continue
        p, q = build(nodes, edges), build(nodes2, edges2)
        old = execute(p, {**raw, "projection": p.identity})
        verdict = revalidate(old, q, diff=diff(p, q)).verdict
        fresh = execute(q, {**raw, "projection": q.identity})
        if verdict == HOLDS:
            assert decided(fresh) == decided(old)
        elif verdict == FAILS:
            assert verify_result(old, nodes=nodes2, edges=edges2)
        tally[verdict] = tally.get(verdict, 0) + 1
    assert tally.get(HOLDS, 0) >= 30 and tally.get(FAILS, 0) >= 20, tally


def test_pairs_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from graph_runtime import json_schema
    from graph_runtime._validation import shape_problems
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("reachability pairs schema")
    for _ in range(80):
        nodes, edges, raw = run(rng)
        p = build(nodes, edges)
        for limits in ({}, {"max_expansions": rng.randint(0, 40)}):
            data = json.loads(json.dumps(thaw(execute(p, {**raw, "projection": p.identity, "limits": limits})
                                              .to_dict())))
            assert [e.message for e in validator.iter_errors(data)] == [] and shape_problems("result", data) == []


def test_many_pairs_on_a_large_graph():
    """20,000 nodes and 60,000 edges, 5,000 pairs: the index answers them all; kept, it answers again faster."""
    rng = random.Random("reachability pairs, large")
    n = 20_000
    ids = [f"v{i:05d}" for i in range(n)]
    edges = [{"id": f"e{k:05d}", "source": ids[a], "target": ids[b], "relation": "R"}
             for k, (a, b) in enumerate((rng.randrange(n), rng.randrange(n)) for _ in range(3 * n))]
    p = Projection.build(nodes=ids, edges=edges)
    request = p.request("reachability_pairs", pairs=[(rng.choice(ids), rng.choice(ids)) for _ in range(5000)],
                        scope=[("R", "forward")], limits={"max_expansions": 10 ** 8})
    start = time.perf_counter()
    first = execute(p, request)
    built = time.perf_counter() - start
    start = time.perf_counter()
    again = execute(p, request)
    kept = time.perf_counter() - start
    assert first.status == COMPLETED and first.result_id == again.result_id
    assert kept < built and built < 60
    assert 0 < first.answer["reachable"] < 5000


def test_a_closure_does_not_prove_a_pair_reachable():
    """Without witnesses, a reachable pair has no proof: deleting its only edge must not let the cached answer hold."""
    nodes = ["a", "b"]
    edges = [{"id": "e", "source": "a", "target": "b", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges, relations=["R"])
    res = execute(p, p.request("reachability_pairs", pairs=[("a", "b")], scope=[("R", "forward")], witnesses=False,
                               certificate=True))
    assert decided(res) == [("a", "b", True)]
    q = Projection.build(nodes=nodes, edges=[], relations=["R"])
    assert revalidate(res, q).verdict != HOLDS and revalidate(res, q, diff=diff(p, q)).verdict != HOLDS
    same = execute(p, p.request("reachability_pairs", pairs=[("a", "b"), ("b", "b")], scope=[("R", "forward")]))
    assert revalidate(same, Projection.build(nodes=nodes, edges=edges + [{"id": "f", "source": "b", "target": "a",
                                                                      "relation": "R"}], relations=["R"])).verdict == HOLDS


def test_no_hop_is_read_before_the_budget_allows_it():
    n = 20_000
    ids = [f"v{i:05d}" for i in range(n)]
    edges = [{"id": f"e{i:05d}", "source": ids[i], "target": ids[(i * 7 + 1) % n], "relation": "R"} for i in range(n)]
    p = Projection.build(nodes=ids, edges=edges)
    request = p.request("reachability_pairs", pairs=[(ids[0], ids[1])], scope=[("R", "forward")],
                        limits={"max_expansions": 0})
    start = time.perf_counter()
    res = execute(p, request)
    assert res.status == INTERRUPTED and res.stats["edges_examined"] == 0 and res.stats["discovered"] == 0
    cancelled = execute(p, p.request("reachability_pairs", pairs=[(ids[0], ids[1])], scope=[("R", "forward")]),
                        cancel=lambda: True)
    assert cancelled.status == INTERRUPTED and cancelled.stats["edges_examined"] == 0
    assert time.perf_counter() - start < 1


def test_a_projection_keeps_a_few_indexes():
    from graph_runtime.reachindex import _KEPT
    ids = [f"v{i}" for i in range(50)]
    relations = [f"R{k}" for k in range(7)]
    p = Projection.build(nodes=ids, edges=[], relations=relations)
    for k in range(1, 1 << 4):
        scope = [(r, "forward") for j, r in enumerate(relations[:4]) if k >> j & 1]
        execute(p, p.request("reachability_pairs", pairs=[(ids[0], ids[1])], scope=scope))
    assert len(p._memo["reach_index"]) == _KEPT


def test_certifying_many_negatives_takes_linear_time():
    """The projection declares R: without it every request failed (unknown_relation), and the timer measured the check
    of an error result."""
    def checked(n):
        ids = [f"v{i:05d}" for i in range(n)]
        p = Projection.build(nodes=ids, edges=[], relations=["R"])
        res = execute(p, p.request("reachability_pairs", pairs=[(ids[i], ids[i + 1]) for i in range(n - 1)],
                                   scope=[("R", "forward")], certificate=True, limits={"max_expansions": 10 ** 7}))
        assert res.status == COMPLETED and len(res.answer["pairs"]) == n - 1
        assert all(x["reachable"] is False for x in res.answer["pairs"]) and res.answer["certificate"] is not None
        start = time.perf_counter()
        assert verify_result(res, nodes=ids, edges=[]) == []
        return time.perf_counter() - start
    small, large = checked(1000), checked(8000)
    assert large < 1 and large < 20 * max(small, 0.005)


def test_a_witness_walk_is_charged_hop_by_hop():
    """A directed ring: the walk from a node to its successor through a distant hub goes around the ring before its
    loop is cut to one hop. Every hop followed is charged first, so a budget that runs out mid-walk stops there, and
    the unfinished pair is not listed."""
    n = 2000
    ids = [f"v{i:04d}" for i in range(n)]
    edges = [{"id": f"e{i:04d}", "source": ids[i], "target": ids[(i + 1) % n], "relation": "R"} for i in range(n)]
    p = Projection.build(nodes=ids, edges=edges)

    def run(budget, witnesses=True):
        return execute(p, p.request("reachability_pairs", pairs=[(ids[1], ids[2])], scope=[("R", "forward")],
                                    witnesses=witnesses, limits={"max_expansions": budget}))
    done = run(10 ** 7)
    assert done.status == COMPLETED and done.answer["pairs"][0]["witness"]["length"] == 1
    plain = run(10 ** 7, witnesses=False)
    walked = done.stats["expansions"] - plain.stats["expansions"]
    assert walked > n // 2                                          # the walk, not the witness, is charged
    short = run(done.stats["expansions"] - 1)
    assert short.status == INTERRUPTED and short.answer["count"] == 0


def test_many_negatives_sharing_a_closure_verify_in_linear_time():
    def checked(n):
        ids = ["hub"] + [f"leaf{i:05d}" for i in range(n)] + [f"lone{i:05d}" for i in range(n)]
        edges = [{"id": f"e{i:05d}", "source": "hub", "target": f"leaf{i:05d}", "relation": "R"} for i in range(n)]
        p = Projection.build(nodes=ids, edges=edges)
        res = execute(p, p.request("reachability_pairs", pairs=[("hub", f"lone{i:05d}") for i in range(n)],
                                   scope=[("R", "forward")], certificate=True, limits={"max_expansions": 10 ** 7}))
        start = time.perf_counter()
        assert verify_result(res, nodes=ids, edges=edges) == [] and contract_problems(res.to_dict()) == []
        return time.perf_counter() - start
    small, large = checked(1000), checked(16000)
    assert large < 2 and large < 40 * max(small, 0.005)
