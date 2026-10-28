"""Lightest derivations in AND/OR graphs: the best way to derive each target when a node of an all_of type needs
every input (a reaction, all its reactants) and any other node one (a species, any reaction that makes it), for a
sum of costs, the least highest cost or the greatest lowest. The engine settles values best first (Knuth, 1977); the
tests compare it with an oracle that applies the rules to every node in synchronous rounds until nothing changes, on
random graphs, then check the certificates, the verifier and revalidation, and a small reaction network."""

import random
from fractions import Fraction

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, verify_result
from graph_runtime.revalidate import FAILS, HOLDS, UNKNOWN, diff, revalidate
from oracle import RELATIONS, TYPES, derivation_values, node_id, random_graph, random_scope
from test_revalidate import edited


def value_of(res, entry):
    """An answer's value, exact: a fraction in rational mode."""
    return Fraction(entry["value_exact"]) if "value_exact" in entry and entry["value_exact"] is not None \
        else entry["value"]


def random_costs(rng, edges, objective):
    if rng.random() < 0.25:
        return None
    floats = [0.5, 1.25, 0.1, 3.0, 2.0]
    if objective == "min_sum":
        def num():
            return rng.choice(floats) if rng.random() < 0.3 else rng.randint(0, 5)
    else:
        def num():
            return rng.choice(floats + [-1.5]) if rng.random() < 0.3 else rng.randint(-3, 5)
    if rng.random() < 0.5:
        return {"by_relation": {r: num() for r in RELATIONS}}
    return {"by_edge": {e["id"]: num() for e in edges}}


def cases(rng, n, **graph):
    for _ in range(n):
        nodes, edges = random_graph(rng, **graph)
        hier = {}
        if rng.random() < 0.25:
            hier["relation_parents"] = {"S": ["R"]}
        if rng.random() < 0.25:
            hier["type_parents"] = {"B": ["A"]}
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES, **hier)
        ids = [node_id(x) for x in nodes]
        objective = rng.choice(["min_sum", "min_sum", "min_max", "max_min"])
        raw = {"operation": "derivation", "targets": rng.sample(ids, rng.randint(1, min(3, len(ids)))),
               "scope": random_scope(rng), "all_of": rng.choice([None, ["A"], ["A", "C"]]),
               "grounds": rng.choice([None, None, rng.sample(ids, rng.randint(1, len(ids)))]),
               "costs": random_costs(rng, edges, objective), "objective": objective,
               "certificate": rng.random() < 0.7}
        yield nodes, edges, hier, p, raw


def oracle(nodes, edges, hier, raw):
    return derivation_values(nodes, edges, raw["scope"], raw["all_of"], raw["grounds"], raw["costs"],
                             raw["objective"], hier.get("type_parents"), hier.get("relation_parents"))


def test_random_derivations_match_the_rounds_oracle():
    rng = random.Random("derivations")
    tally = {}
    for nodes, edges, hier, p, raw in cases(rng, 1500):
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges, **hier) == [], raw
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        want = oracle(nodes, edges, hier, raw)
        got = {d["node"]: value_of(res, d) for d in res.answer["derived"]}
        assert got == {t: want[t] for t in raw["targets"] if t in want}, raw
        assert set(res.answer["underived"]) == {t for t in raw["targets"] if t not in want}
        for e in res.answer["derivation"]:                  # every sub-derivation is a best one too
            assert value_of(res, e) == want[e["node"]], (raw, e)
        tally[(raw["objective"], bool(got), bool(res.answer["underived"]))] = tally.get(
            (raw["objective"], bool(got), bool(res.answer["underived"])), 0) + 1
    assert all(tally.get((o, True, False), 0) >= 20 and tally.get((o, False, True), 0) >= 20
               for o in ("min_sum", "min_max", "max_min")), tally


def test_limits_never_certify_and_list_only_best_values():
    rng = random.Random("derivation-limits")
    interrupted = 0
    for nodes, edges, hier, p, raw in cases(rng, 800, max_nodes=10, max_edges=24):
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 12)}})
        assert verify_result(res, nodes=nodes, edges=edges, **hier) == []
        if res.status != INTERRUPTED:
            continue
        interrupted += 1
        want = oracle(nodes, edges, hier, raw)
        assert not res.answer["underived"]
        for d in res.answer["derived"]:
            assert value_of(res, d) == want[d["node"]]
        bound = res.answer.get("bound_exact", res.answer["bound"]) if "bound_exact" in res.answer \
            else res.answer["bound"]
        if bound is not None:
            bound = Fraction(bound) if isinstance(bound, str) else bound
            done = {d["node"] for d in res.answer["derived"]}
            for t in raw["targets"]:
                if t in want and t not in done:          # no unsettled target is better than the bound
                    v = want[t]
                    if raw["objective"] == "min_sum":
                        assert v >= bound
                    elif raw["objective"] == "min_max":
                        assert v is not None and v >= bound
                    else:
                        assert v is not None and v <= bound
    assert interrupted >= 200


def test_a_certificate_without_an_edge_never_passes_where_the_edge_changes_the_answer():
    rng = random.Random("derivation-soundness")
    changed = 0
    for nodes, edges, hier, p, raw in cases(rng, 2500, max_nodes=7, max_edges=14):
        if not edges or not raw["certificate"] or (raw["costs"] and "by_edge" in raw["costs"]):
            continue
        full = execute(p, {**raw, "projection": p.identity})
        used = [h["edge"] for e in full.answer["derivation"] for h in e["uses"]]
        gone = rng.choice(used) if used and rng.random() < 0.7 else rng.choice(edges)["id"]   # often one it used
        less = [e for e in edges if e["id"] != gone]
        q = Projection.build(nodes=nodes, edges=less, relations=RELATIONS, node_types=TYPES, **hier)
        part = execute(q, {**raw, "projection": q.identity})

        def claim(r):
            return ({d["node"]: value_of(r, d) for d in r.answer["derived"]}, r.answer["underived"])
        if claim(full) != claim(part):
            changed += 1
            assert verify_result(part, nodes=nodes, edges=edges, **hier), (raw, claim(full), claim(part))
    assert changed >= 100


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("derivation-revalidate")
    tally = {}
    for nodes, edges, hier, p, raw in cases(rng, 900, max_nodes=8, max_edges=16):
        if raw["costs"] and "by_edge" in raw["costs"]:
            raw = {**raw, "costs": None}
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES, **hier)
        old = execute(p, {**raw, "projection": p.identity})
        full, fast = revalidate(old, q), revalidate(old, q, diff=diff(p, q))
        assert fast.checked == full.checked == "answer"
        assert (full.verdict, full.reasons) == (fast.verdict, fast.reasons)
        if full.verdict == HOLDS:
            fresh = execute(q, {**raw, "projection": q.identity})
            assert fresh.status == COMPLETED
            assert ({d["node"]: value_of(fresh, d) for d in fresh.answer["derived"]}
                    == {d["node"]: value_of(old, d) for d in old.answer["derived"]})
            assert fresh.answer["underived"] == old.answer["underived"]
            assert verify_result(old, nodes=nodes2, edges=edges2, **hier) == []
        elif full.verdict == FAILS:
            fresh = execute(q, {**raw, "projection": q.identity})
            assert fresh.status != COMPLETED or verify_result(old, nodes=nodes2, edges=edges2, **hier)
        tally[full.verdict] = tally.get(full.verdict, 0) + 1
    assert all(tally.get(v, 0) >= 50 for v in (HOLDS, FAILS, UNKNOWN)), tally


# ---------------------------------------------------------------------------------------------- scenarios
def network():
    """CO2 hydrogenation to methanol, three ways: directly, through CO (reverse water-gas shift, then CO
    hydrogenation) and through formic acid. Species need one reaction that makes them (MADE_BY); a reaction needs all
    of its reactants (NEEDS). The numbers are invented for the test."""
    species = ["CO2", "H2", "CO", "H2O", "HCOOH", "CH3OH"]
    reactions = {"rwgs": (["CO2", "H2"], ["CO", "H2O"], 3), "co_hyd": (["CO", "H2"], ["CH3OH"], 2),
                 "co2_hyd": (["CO2", "H2"], ["CH3OH", "H2O"], 7), "fa_form": (["CO2", "H2"], ["HCOOH"], 1),
                 "fa_hyd": (["HCOOH", "H2"], ["CH3OH"], 2)}
    nodes = [{"id": s, "types": ["Species"]} for s in species] + [{"id": r, "types": ["Reaction"]} for r in reactions]
    edges, barrier = [], {}
    for r, (ins, outs, b) in reactions.items():
        edges += [{"id": f"{r}-needs-{x}", "source": r, "target": x, "relation": "NEEDS"} for x in ins]
        for x in outs:
            edges.append({"id": f"{x}-by-{r}", "source": x, "target": r, "relation": "MADE_BY"})
            barrier[f"{x}-by-{r}"] = b
    costs = {"by_edge": {e["id"]: barrier.get(e["id"], 0) for e in edges}}
    return nodes, edges, costs


def test_a_reaction_network_cheapest_and_lowest_barrier_routes():
    nodes, edges, costs = network()
    p = Projection.build(nodes=nodes, edges=edges)
    scope = [("NEEDS", "forward"), ("MADE_BY", "forward")]
    common = {"targets": ["CH3OH", "CO"], "scope": scope, "all_of": ["Reaction"], "grounds": ["CO2", "H2"],
              "costs": costs, "certificate": True}
    total = execute(p, p.request("derivation", **common))
    assert {d["node"]: d["value"] for d in total.answer["derived"]} == {"CH3OH": 3, "CO": 3}
    route = {e["node"]: e for e in total.answer["derivation"]}
    assert route["CH3OH"]["rule"] == "one" and route["CH3OH"]["uses"][0]["to"] == "fa_hyd"
    assert route["fa_hyd"]["rule"] == "all" and {h["to"] for h in route["fa_hyd"]["uses"]} == {"H2", "HCOOH"}
    assert route["CO2"]["rule"] == "given"
    barrier = execute(p, p.request("derivation", **{**common, "objective": "min_max"}))
    assert {d["node"]: d["value"] for d in barrier.answer["derived"]} == {"CH3OH": 2, "CO": 3}
    no_h2 = execute(p, p.request("derivation", **{**common, "grounds": ["CO2"]}))
    assert no_h2.answer["derived"] == () and no_h2.answer["underived"] == ("CH3OH", "CO")
    assert no_h2.answer["certificate"]["bound"] is None
    for r in (total, barrier, no_h2):
        assert verify_result(r, nodes=nodes, edges=edges) == []


def test_support_in_a_circle_derives_nothing():
    nodes = ["a", "b", "g"]
    edges = [{"id": "ab", "source": "a", "target": "b", "relation": "NEEDS"},
             {"id": "ba", "source": "b", "target": "a", "relation": "NEEDS"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("derivation", targets=["a", "g"], scope=[("NEEDS", "forward")], certificate=True))
    assert [d["node"] for d in res.answer["derived"]] == ["g"] and res.answer["underived"] == ("a",)
    assert verify_result(res, nodes=nodes, edges=edges) == []


def test_an_all_of_node_without_inputs_is_derived_by_nothing():
    p = Projection.build(nodes=[{"id": "r", "types": ["R"]}, "s"],
                         edges=[{"id": "e", "source": "s", "target": "r", "relation": "BY"}])
    res = execute(p, p.request("derivation", targets=["s"], scope=[("BY", "forward")], all_of=["R"]))
    assert {d["node"]: d["value"] for d in res.answer["derived"]} == {"s": 1}
    assert {e["node"]: e["rule"] for e in res.answer["derivation"]} == {"r": "all", "s": "one"}
