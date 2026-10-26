"""Revalidation: whether a cached result still holds on an edited projection, decided from its answer and
certificate, by checking everything against the new projection or only what a diff touches. "holds" must mean that
a fresh run gives the same answer and the cached one checks out against the new records; "fails" that it does not;
"unknown" claims nothing."""

import random

import pytest

from graph_runtime import COMPLETED, Projection, execute, verify_result
from graph_runtime.revalidate import FAILS, HOLDS, UNKNOWN, diff, revalidate, revalidate_all
from oracle import RELATIONS, TYPES, node_id, random_graph
from test_certificates import answer_of, requests


def edited(rng, nodes, edges):
    """A random edit: edges removed, added or moved, a node removed (with its edges) or retyped."""
    nodes, edges = [dict(n) if isinstance(n, dict) else n for n in nodes], [dict(e) for e in edges]
    ids = [node_id(n) for n in nodes]
    for _ in range(rng.randint(1, 3)):
        kind = rng.choice(["remove", "add", "add", "move", "drop_node", "retype"])
        if kind == "remove" and edges:
            edges.pop(rng.randrange(len(edges)))
        elif kind == "add":
            edges.append({"id": f"new-{rng.randrange(10**6)}", "source": rng.choice(ids), "target": rng.choice(ids),
                          "relation": rng.choice(RELATIONS)})
        elif kind == "move" and edges:
            e = rng.choice(edges)
            e["target"] = rng.choice(ids)
        elif kind == "drop_node" and len(ids) > 1:
            gone = rng.choice(ids)
            nodes = [n for n in nodes if node_id(n) != gone]
            edges = [e for e in edges if gone not in (e["source"], e["target"])]
            ids.remove(gone)
        elif kind == "retype":
            k = rng.randrange(len(nodes))
            nodes[k] = {"id": node_id(nodes[k]), "types": rng.sample(TYPES, rng.randint(0, 2))}
    return nodes, edges


def test_revalidation_agrees_with_fresh_runs_and_with_itself():
    rng = random.Random("revalidate")
    tally = {}
    for _ in range(700):
        nodes, edges = random_graph(rng, max_nodes=8, max_edges=16)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES)
        d = diff(p, q)
        for raw in requests(rng, [node_id(n) for n in nodes]):
            old = execute(p, {**raw, "projection": p.identity})
            full, fast = revalidate(old, q), revalidate(old, q, diff=d)
            assert fast.checked == "diff" and full.checked == "answer"
            assert (fast.verdict, fast.reasons, fast.revalidation_id) == (full.verdict, full.reasons,
                                                                           full.revalidation_id), raw
            fresh = execute(q, {**raw, "projection": q.identity})
            if full.verdict == HOLDS:
                assert fresh.status == COMPLETED and answer_of(fresh) == answer_of(old), (raw, full.reasons)
                assert verify_result(old, nodes=nodes2, edges=edges2) == []
            elif full.verdict == FAILS:
                assert fresh.status != COMPLETED or verify_result(old, nodes=nodes2, edges=edges2), full.reasons
            key = (raw["operation"], full.verdict)
            tally[key] = tally.get(key, 0) + 1
    for op in ("reachability", "dependency_impact", "typed_path", "shortest_path"):
        for verdict in (HOLDS, FAILS, UNKNOWN) if op in ("typed_path", "shortest_path") else (HOLDS, FAILS):
            assert tally.get((op, verdict), 0) >= 10, (op, verdict, tally)


def test_a_complete_reachability_answer_is_never_unknown():
    rng = random.Random("reachability-verdicts")
    for _ in range(300):
        nodes, edges = random_graph(rng)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        q = Projection.build(**dict(zip(("nodes", "edges"), edited(rng, nodes, edges))), relations=RELATIONS,
                             node_types=TYPES)
        raw = requests(rng, [node_id(n) for n in nodes])[0]
        old = execute(p, {**raw, "projection": p.identity})
        fresh = execute(q, {**raw, "projection": q.identity})
        verdict = revalidate(old, q, diff=diff(p, q)).verdict
        assert verdict in (HOLDS, FAILS)
        assert (verdict == HOLDS) == (fresh.status == COMPLETED and answer_of(fresh) == answer_of(old)
                                      and verify_result(old, nodes=q.to_dict()["nodes"],
                                                        edges=q.to_dict()["edges"]) == [])


NODES = ["a", "b", "c", "d", "x", "y"]
EDGES = [{"id": "e1", "source": "a", "target": "b", "relation": "R"},
         {"id": "e2", "source": "b", "target": "c", "relation": "R"},
         {"id": "e3", "source": "x", "target": "y", "relation": "R"}]


def projection(*extra, drop=()):
    return Projection.build(nodes=NODES, edges=[e for e in EDGES if e["id"] not in drop] + list(extra),
                            relations=["R"])


def test_the_reasons_name_the_edit():
    p = projection()
    r = execute(p, p.request("reachability", sources=["a"], scope=[("R", "forward")]))
    far = projection({"id": "f", "source": "y", "target": "x", "relation": "R"})
    out = projection({"id": "o", "source": "c", "target": "d", "relation": "R"})
    short = projection({"id": "s", "source": "a", "target": "c", "relation": "R"})
    cut = projection(drop=("e2",))
    assert revalidate(r, far).verdict == HOLDS
    assert [dict(x) for x in revalidate(r, out).reasons] == [
        {"code": "reaches_unlisted", "edge": "o", "from": "c", "to": "d"}]
    assert [dict(x) for x in revalidate(r, short).reasons] == [
        {"code": "reaches_shallower", "edge": "s", "from": "a", "to": "c"}]
    assert [dict(x) for x in revalidate(r, cut).reasons] == [{"code": "witness_gone", "edge": "e2", "node": "c"}]
    gone = Projection.build(nodes=["b", "c"], edges=[EDGES[1]], relations=["R"])
    assert [dict(x) for x in revalidate(r, gone).reasons] == [{"code": "unanswerable", "error": "unknown_node"}]


def test_negatives_and_optima_need_their_certificates():
    p = projection()
    raw = {"sources": ["a"], "targets": ["d"], "scope": [("R", "forward")], "costs": {"by_relation": {"R": 1}}}
    bare = execute(p, p.request("shortest_path", **raw))
    proved = execute(p, p.request("shortest_path", **raw, certificate=True))
    far = projection({"id": "f", "source": "y", "target": "x", "relation": "R"})
    assert [dict(x) for x in revalidate(bare, far).reasons] == [{"code": "no_certificate"}]
    assert revalidate(proved, far).verdict == HOLDS
    to_d = projection({"id": "o", "source": "c", "target": "d", "relation": "R"})
    broken = revalidate(proved, to_d)
    assert broken.verdict == UNKNOWN and dict(broken.reasons[0])["code"] == "certificate_broken"
    assert execute(to_d, to_d.request("shortest_path", **raw)).found is True     # it was refuted indeed


def test_what_cannot_be_revalidated_is_unknown():
    p = projection()
    q = projection({"id": "f", "source": "y", "target": "x", "relation": "R"})
    cut = execute(p, p.request("reachability", sources=["a"], scope=[("R", "forward")], limits={"max_results": 1}))
    assert dict(revalidate(cut, q).reasons[0]) == {"code": "not_complete", "status": "interrupted"}
    pat = execute(p, p.request("pattern", match={"var": "s", "nodes": ["a"]}))
    assert dict(revalidate(pat, q).reasons[0]) == {"code": "not_certified", "operation": "pattern"}
    sup = execute(p, p.request("support", failed=["c"], scope=[("R", "forward")]))
    assert revalidate(sup, q).verdict == UNKNOWN
    assert revalidate(cut, p).verdict == UNKNOWN                    # an interrupted answer, even on its projection
    whole = execute(p, p.request("reachability", sources=["a"], scope=[("R", "forward")]))
    assert revalidate(whole, p).verdict == HOLDS


def test_a_diff_is_used_only_between_the_projections_it_names():
    p, q = projection(), projection({"id": "o", "source": "c", "target": "d", "relation": "R"})
    r = execute(p, p.request("reachability", sources=["a"], scope=[("R", "forward")]))
    other = diff(projection({"id": "z", "source": "d", "target": "a", "relation": "R"}), q)
    assert revalidate(r, q, diff=other).checked == "answer"      # it names another starting projection
    relabeled = Projection.build(nodes=NODES, edges=EDGES, relations=["R", "S"])
    assert diff(p, relabeled).vocabulary_changed and revalidate(r, relabeled, diff=diff(p, relabeled)).checked \
        == "answer"
    d = diff(p, q)
    assert d.added_edges == frozenset({"o"}) and d.size == 1
    moved = Projection.build(nodes=NODES, edges=[EDGES[0], {**EDGES[1], "target": "d"}, EDGES[2]], relations=["R"])
    assert diff(p, moved).removed_edges == diff(p, moved).added_edges == frozenset({"e2"})


def test_one_diff_serves_every_cached_result():
    rng = random.Random("cache")
    nodes = [f"n{i:03d}" for i in range(300)]
    edges = [{"id": f"e{i:04d}", "source": rng.choice(nodes), "target": rng.choice(nodes), "relation": "R"}
             for i in range(900)]
    p = Projection.build(nodes=nodes, edges=edges, relations=["R"])
    cached = [execute(p, p.request("reachability", sources=[s], scope=[("R", "forward")], max_depth=2))
              for s in nodes[:100]]
    extra = {"id": "new", "source": nodes[0], "target": nodes[1], "relation": "R"}
    q = Projection.build(nodes=nodes, edges=edges + [extra], relations=["R"])
    verdicts = revalidate_all(cached, p, q)
    assert all(v.checked == "diff" for v in verdicts)
    for res, v in zip(cached, verdicts):
        fresh = execute(q, q.request("reachability", sources=list(res.request["sources"]), scope=[("R", "forward")],
                                     max_depth=2))
        assert (v.verdict == HOLDS) == (answer_of(fresh) == answer_of(res)), v.reasons
    assert {v.verdict for v in verdicts} == {HOLDS, FAILS}


def test_a_revalidation_is_a_record_with_an_identity():
    p, q = projection(), projection({"id": "o", "source": "c", "target": "d", "relation": "R"})
    r = execute(p, p.request("reachability", sources=["a"], scope=[("R", "forward")]))
    v = revalidate(r, q)
    data = v.to_dict()
    assert data["kind"] == "graph_runtime.revalidation" and data["result_id"] == r.result_id
    assert data["from_projection"] == p.identity and data["to_projection"] == q.identity
    assert data["verdict"] == FAILS and v.holds is False and data["reason_count"] == 1
    assert revalidate(r.to_dict(), q).revalidation_id == v.revalidation_id
    with pytest.raises(AttributeError):
        v.verdict = HOLDS
