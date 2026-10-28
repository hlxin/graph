"""K shortest paths certificates: with ``certificate: true`` a complete answer proves its ranking. The oracle here
builds, for any claimed list of paths, the strongest certificate there is, from the records alone: exact potentials
(the least (cost, hops) to a target, by relaxation) and, for the start and every prefix of the listed paths, every
node the walks leaving the prefix tree reach while their potentials still put them before the last path, at its
least (cost, hops, side). For a true answer the engine's certificate is exactly that one; for a false claim even
that one is refused."""

import copy
import random
import time
from fractions import Fraction

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import revalidate
from oracle import RELATIONS, TYPES, hops_of
from test_certificates import reseal
from test_k_shortest_paths import every_simple_path, listed, run
from test_revalidate import edited


def written(x, rational):
    if not rational:
        return x
    x = Fraction(x)
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


def strongest_certificate(edges, request, paths, full):
    """The certificate of ``paths`` (as the answer lists them) that proves the most: exact potentials, and labels
    for everything a walk leaving the prefix tree reaches before the last path (all of it when not ``full``)."""
    costs = request["costs"]
    kind, table = next(iter(costs.items())) if costs else (None, None)
    rational = table is not None and any(isinstance(x, float) for x in table.values())
    rel = {e["id"]: e["relation"] for e in edges}

    def cost(eid):
        if table is None:
            return 1
        x = table[rel[eid] if kind == "by_relation" else eid]
        return Fraction(x) if rational else x
    hops = hops_of(edges, request["scope"])
    out_of = {}
    for u, v, eid in hops:
        out_of.setdefault(u, []).append((eid, v))
    targets = set(request["targets"])
    pi = {t: (0, 0) for t in targets}
    changed = True
    while changed:                                        # Bellman-Ford on (cost, hops)
        changed = False
        for u, v, eid in hops:
            if v in pi and (u not in pi or (pi[v][0] + cost(eid), pi[v][1] + 1) < pi[u]):
                pi[u] = (pi[v][0] + cost(eid), pi[v][1] + 1)
                changed = True
    keys = [(sum(cost(h["edge"]) for h in x["hops"]), len(x["hops"]), (x["source"],) +
             tuple(h["edge"] for h in x["hops"])) for x in paths]
    tau = (keys[-1][0], keys[-1][1], 1) if full else None
    last = keys[-1][2] if full else None

    def pruned(val, v):
        return v not in pi or (tau is not None and (val[0] + pi[v][0], val[1] + pi[v][1], val[2]) >= tau)

    def labels(starts, avoid):
        d = {}
        for val, v in starts:
            if v not in avoid and not pruned(val, v) and (v not in d or val < d[v]):
                d[v] = val
        changed = True
        while changed:
            changed = False
            for y in list(d):
                for eid, z in out_of.get(y, ()):
                    val = (d[y][0] + cost(eid), d[y][1] + 1, d[y][2])
                    if z not in avoid and not pruned(val, z) and (z not in d or val < d[z]):
                        d[z] = val
                        changed = True
        side = {0: "before", 1: "after"} if full else {0: None}
        return [{"node": v, "cost": written(c, rational), "hops": h, "side": side[b]}
                for v, (c, h, b) in sorted(d.items())]
    explored = []
    used = {x["source"] for x in paths}
    start = labels([((0, 0, int(full and s > last[0])), s) for s in sorted(set(request["sources"]) - used)], set())
    if start:
        explored.append({"path": None, "at": None, "labels": start})
    taken, seen = {}, set()
    for x in paths:
        seq = (x["source"],) + tuple(h["edge"] for h in x["hops"])
        for j in range(len(seq) - 1):
            taken.setdefault(seq[:j + 1], set()).add(seq[j + 1])
    for i, x in enumerate(paths):
        seq = (x["source"],) + tuple(h["edge"] for h in x["hops"])
        nodes = [x["source"]] + [h["to"] for h in x["hops"]]
        for j in range(len(nodes)):
            if seq[:j + 1] in seen:
                continue
            seen.add(seq[:j + 1])
            c = sum(cost(e) for e in seq[1:j + 1])

            def side(eid):
                if not full:
                    return 0
                return int(seq[:j + 1] + (eid,) > last) if last[:j + 1] == seq[:j + 1] else int(seq[:j + 1] > last)
            got = labels([((c + cost(eid), j + 1, side(eid)), v) for eid, v in out_of.get(nodes[j], ())
                          if eid not in taken.get(seq[:j + 1], ())], set(nodes[:j + 1]))
            if got:
                explored.append({"path": i, "at": j, "labels": got})
    return {"kind": "deviations",
            "potentials": [{"node": v, "cost": written(c, rational), "hops": h} for v, (c, h) in sorted(pi.items())],
            "explored": explored}


def certified(rng, n=7, m=14):
    nodes, edges, p, raw = run(rng, n, m)
    raw["certificate"] = True
    if rng.random() < 0.5:                                # a short list, so that there is a last path to tie with
        raw["k"] = rng.randint(1, 4)
    return nodes, edges, p, raw, execute(p, {**raw, "projection": p.identity})


def test_the_certificate_is_the_strongest_one_and_proves_the_oracle_ranking():
    rng = random.Random("k paths certificate")
    tally = {"full": 0, "exhausted": 0, "labels": 0, "after": 0, "before": 0}
    for _ in range(1200):
        nodes, edges, p, raw, res = certified(rng, 8, 18)
        assert res.status == COMPLETED
        want = every_simple_path(nodes, edges, set(raw["sources"]), set(raw["targets"]), raw["scope"],
                                 raw["costs"])[:raw["k"]]
        assert listed(res) == [steps for _, steps in want]
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        cert = thaw(res.answer["certificate"])
        full = len(want) == raw["k"]
        assert cert == strongest_certificate(edges, thaw(res.request), thaw(res.answer["paths"]), full), raw
        plain = execute(p, {**raw, "certificate": False, "projection": p.identity})
        assert plain.answer["certificate"] is None and listed(plain) == listed(res)
        tally["full" if full else "exhausted"] += 1
        for e in cert["explored"]:
            tally["labels"] += len(e["labels"])
            for x in e["labels"]:
                if x["side"]:
                    tally[x["side"]] += 1
    assert min(tally.values()) >= 60, tally


def forge(res, paths, exhausted):
    data = copy.deepcopy(thaw(res.to_dict()))
    data["answer"]["paths"], data["answer"]["count"], data["answer"]["exhausted"] = paths, len(paths), exhausted
    return data


def test_a_false_ranking_is_refused_even_with_the_strongest_certificate():
    """Three false claims: a path left out of an exhausted list, the last path skipped for the next one, and a full
    list called exhausted. Each is given its own strongest certificate, which must still fail."""
    rng = random.Random("k paths forged")
    tried = {"left out": 0, "skipped": 0, "not all": 0}
    for _ in range(900):
        nodes, edges, p, raw, res = certified(rng)
        paths = thaw(res.answer["paths"])
        more = execute(p, {**raw, "k": raw["k"] + 1, "projection": p.identity})
        claims = []
        if len(paths) >= 2:
            for i in range(len(paths) - (1 if res.answer["exhausted"] else 0)):
                fewer = paths[:i] + paths[i + 1:]
                claims.append(("left out", res, fewer, True))
        if not res.answer["exhausted"] and more.answer["count"] > raw["k"]:
            nxt = thaw(more.answer["paths"])[-1]
            claims.append(("skipped", res, paths[:-1] + [nxt], False))
            claims.append(("not all", more, paths, True))
        for name, base, claim, exhausted in claims:
            data = forge(base, claim, exhausted)
            data["answer"]["certificate"] = strongest_certificate(edges, thaw(base.request), claim, not exhausted)
            assert verify_result(reseal(data), nodes=nodes, edges=edges), (name, raw)
            tried[name] += 1
    assert min(tried.values()) >= 100, tried


def test_broken_certificates_are_refused():
    """Worked by hand. s-a-t costs 1 + 5 and s-c-t 3 + 4; b hangs off a (a-b free, b-a free), so its potential
    (5, 2) runs back through a. With k = 2 the last path is s-c-t at (7, 2). Leaving the prefix s-a for b costs
    (1, 2), and with b's potential (6, 4): before (7, 2), so b is labelled, before the last path on a tie (e1 < e5);
    its one hop returns to a, which the prefix visits. Everything else leaves no prefix: s, a and c each take only
    hops the listed paths take."""
    nodes = ["s", "a", "b", "c", "t"]
    edges = [{"id": eid, "source": u, "target": v, "relation": "R"} for eid, u, v in (
        ("e1", "s", "a"), ("e2", "a", "t"), ("e3", "a", "b"), ("e4", "b", "a"), ("e5", "s", "c"), ("e6", "c", "t"))]
    costs = {"by_edge": {"e1": 1, "e2": 5, "e3": 0, "e4": 0, "e5": 3, "e6": 4}}
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("k_shortest_paths", sources=["s"], targets=["t"], scope=[("R", "forward")],
                               costs=costs, k=2, certificate=True))
    assert listed(res) == [["e1", "e2"], ["e5", "e6"]] and res.answer["exhausted"] is False
    assert verify_result(res, nodes=nodes, edges=edges) == []
    cert = thaw(res.answer["certificate"])
    assert cert == {"kind": "deviations",
                    "potentials": [{"node": "a", "cost": 5, "hops": 1}, {"node": "b", "cost": 5, "hops": 2},
                                   {"node": "c", "cost": 4, "hops": 1}, {"node": "s", "cost": 6, "hops": 2},
                                   {"node": "t", "cost": 0, "hops": 0}],
                    "explored": [{"path": 0, "at": 1, "labels": [
                        {"node": "b", "cost": 1, "hops": 2, "side": "before"}]}]}

    def broken(change):
        data = copy.deepcopy(thaw(res.to_dict()))
        change(data["answer"]["certificate"])
        return verify_result(reseal(data), nodes=nodes, edges=edges)
    for change, problem in (
            (lambda c: c["potentials"].pop(1), "is not listed"),                       # b reaches t through a
            (lambda c: c["potentials"][2].__setitem__("cost", 5), "for less"),         # c above its hop to t
            (lambda c: c["potentials"][4].__setitem__("hops", 1), "(0, 0) or below"),  # a target above 0
            (lambda c: c["explored"].clear(), "neither labelled"),                     # b unexplored
            (lambda c: c["explored"][0]["labels"][0].__setitem__("cost", 3), "neither labelled"),
            (lambda c: c["explored"][0]["labels"][0].__setitem__("side", "after"), "neither labelled"),
            (lambda c: c["explored"][0].__setitem__("at", 0), "neither labelled")):   # explored from s instead
        problems = broken(change)
        assert any(problem in x for x in problems), (problem, problems)


def test_revalidation_by_certificate_agrees_with_fresh_runs():
    rng = random.Random("k paths revalidate")
    tally = {}
    for _ in range(700):
        nodes, edges, p, raw, old = certified(rng, 6, 12)
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES)
        check = revalidate(old, q)
        fresh = execute(q, {**old.request, "projection": q.identity})
        if check.verdict == "holds":
            assert fresh.status == COMPLETED and fresh.answer["paths"] == old.answer["paths"]
            assert fresh.answer["exhausted"] == old.answer["exhausted"]
            assert verify_result(old, nodes=nodes2, edges=edges2) == []
        elif check.verdict == "fails":
            assert fresh.status != COMPLETED or fresh.answer["paths"] != old.answer["paths"], check.reasons
        tally[check.verdict] = tally.get(check.verdict, 0) + 1
    assert tally.get("holds", 0) >= 150 and tally.get("fails", 0) >= 50 and tally.get("unknown", 0) >= 50, tally


def test_a_new_shortcut_leaves_the_ranking_unknown():
    nodes = ["s", "a", "t"]
    edges = [{"id": "e1", "source": "s", "target": "a", "relation": "R"},
             {"id": "e2", "source": "a", "target": "t", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    old = execute(p, p.request("k_shortest_paths", sources=["s"], targets=["t"], scope=[("R", "forward")], k=3,
                               certificate=True))
    assert old.answer["exhausted"] is True
    edges2 = edges + [{"id": "e0", "source": "s", "target": "t", "relation": "R"}]
    check = revalidate(old, Projection.build(nodes=nodes, edges=edges2))
    assert check.verdict == "unknown" and check.reasons[0]["code"] == "certificate_broken"
    gone = revalidate(old, Projection.build(nodes=nodes, edges=edges[:1]))
    assert gone.verdict == "fails" and gone.reasons[0]["code"] == "witness_gone"
    plain = execute(p, p.request("k_shortest_paths", sources=["s"], targets=["t"], scope=[("R", "forward")], k=3))
    assert revalidate(plain, Projection.build(nodes=nodes, edges=edges2)).reasons[0]["code"] == "no_certificate"
    away = edges + [{"id": "e9", "source": "t", "target": "a", "relation": "R"}]
    assert revalidate(old, Projection.build(nodes=nodes, edges=away)).verdict == "holds"


def test_an_interrupted_answer_carries_no_certificate():
    """The certificate's own searches count against the budget: one cut short leaves the answer interrupted, its
    paths a valid prefix of the order."""
    rng = random.Random("k paths certificate limits")
    seen = set()
    for _ in range(300):
        nodes, edges, p, raw = run(rng)
        raw["certificate"] = True
        plain = execute(p, {**raw, "certificate": False, "projection": p.identity})
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 40)}})
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        if res.status == INTERRUPTED:
            assert res.answer["certificate"] is None and res.answer["exhausted"] is None
            want = every_simple_path(nodes, edges, set(raw["sources"]), set(raw["targets"]), raw["scope"],
                                     raw["costs"])
            assert listed(res) == [steps for _, steps in want][:len(listed(res))]
            if listed(res) == listed(plain):
                seen.add("during the certificate")
        else:
            assert res.answer["certificate"] is not None
        seen.add(res.status)
    assert seen == {COMPLETED, INTERRUPTED, "during the certificate"}, seen


def test_a_long_path_is_certified_in_linear_time():
    """A chain with a shortcut every 50 nodes: the first path takes every shortcut, the next two all but one. The
    prefix tree is about three paths long; each prefix's walks are cut off by the potentials at once."""
    n = 2000
    nodes = [f"v{i:05d}" for i in range(n)]
    edges = [{"id": f"e{i:05d}", "source": nodes[i], "target": nodes[i + 1], "relation": "R"} for i in range(n - 1)]
    edges += [{"id": f"x{i:05d}", "source": nodes[i], "target": nodes[i + 2], "relation": "R"}
              for i in range(0, n - 2, 50)]
    p = Projection.build(nodes=nodes, edges=edges)
    took = {}
    for certificate in (False, True):
        start = time.perf_counter()
        res = execute(p, p.request("k_shortest_paths", sources=[nodes[0]], targets=[nodes[-1]],
                                   scope=[("R", "forward")], k=3, certificate=certificate))
        took[certificate] = time.perf_counter() - start
    assert res.status == COMPLETED and [x["length"] for x in res.answer["paths"]] == [n - 41, n - 40, n - 40]
    assert res.answer["certificate"]["explored"] == ()
    start = time.perf_counter()
    assert verify_result(res, nodes=nodes, edges=edges) == []
    assert took[True] - took[False] < 1 and time.perf_counter() - start < 2, took


def test_checking_potentials_is_linear_in_a_scope_of_many_relations():
    """A chain whose every edge has its own relation, all in scope: the potentials' check
    read the hops into each listed node through a reversed scope it rebuilt per node, 8,000 by 8,000 (7 s)."""
    n = 8000
    nodes = [f"v{i:05d}" for i in range(n)]
    rels = [f"R{i:05d}" for i in range(n - 1)]
    edges = [{"id": f"e{i:05d}", "source": nodes[i], "target": nodes[i + 1], "relation": rels[i]} for i in range(n - 1)]
    p = Projection.build(nodes=nodes, edges=edges, relations=rels)
    took = {}
    for certificate in (False, True):
        res = execute(p, p.request("k_shortest_paths", sources=[nodes[0]], targets=[nodes[-1]],
                                   scope=[(r, "forward") for r in rels], k=1, certificate=certificate))
        start = time.perf_counter()
        assert verify_result(res, nodes=nodes, edges=edges) == []
        took[certificate] = time.perf_counter() - start
    assert len(res.answer["certificate"]["potentials"]) == n
    assert took[True] < 3 * took[False] + 1.5, took        # about 0.2 s more; rebuilt per node, 7 s


def test_k_paths_certificates_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from graph_runtime import json_schema
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("k paths certificate schema")
    for _ in range(80):
        *_, res = certified(rng)
        data = json.loads(json.dumps(thaw(res.to_dict())))
        assert [e.message for e in validator.iter_errors(data)] == []
