"""Regular path queries: walks whose hops match a regular expression of steps (sequence, alternation, repetition).
The engine searches the product of the projection and the expression's Glushkov automaton; the tests compare it with
an independent oracle that rewrites the expression by Brzozowski derivatives, hop by hop (no automaton), on random
graphs and random expressions, with hierarchies and limits; then the certificates, the verifier and revalidation."""

import random

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, Result, execute, normalize_request, verify_result
from graph_runtime.errors import RequestError
from graph_runtime.paths import automaton, normalize, parse, to_text
from graph_runtime.revalidate import FAILS, HOLDS, UNKNOWN, diff, revalidate
from oracle import RELATIONS, TYPES, node_id, random_graph, regular_ends
from test_revalidate import edited

P0 = "sha256:" + "0" * 64


def norm(path):
    return normalize_request({"operation": "regular_path", "projection": P0, "sources": ["a"],
                              "path": path}).to_dict()["path"]


# ---------------------------------------------------------------------------------------------- expressions
def test_the_text_syntax_reads_as_the_tree():
    assert norm("A") == {"op": "step", "scope": [{"relation": "A", "direction": "forward"}], "node_types": None}
    assert norm("^A[T2,T1]") == {"op": "step", "scope": [{"relation": "A", "direction": "reverse"}],
                                 "node_types": ["T1", "T2"]}
    assert norm("A/B/C") == norm(["A", ["B", "C"]]) == norm({"op": "seq", "items": ["A", "B/C"]})
    assert norm("A|B") == norm("B|A") == norm("(B|A)|A")              # alternation is a set
    assert norm("A*") == {"op": "repeat", "item": norm("A"), "min": 0, "max": None}
    assert norm("A+") == norm("A{1,}") and norm("A?") == norm("A{0,1}") and norm("A{1}") == norm("A")
    assert norm("`has part`/x:y-z.1") == {"op": "seq", "items": [
        {"op": "step", "scope": [{"relation": "has part", "direction": "forward"}], "node_types": None},
        {"op": "step", "scope": [{"relation": "x:y-z.1", "direction": "forward"}], "node_types": None}]}
    same = [normalize_request({"operation": "regular_path", "projection": P0, "sources": ["a"], "path": t})
            for t in ("A|B/C", "(C/B)|A".replace("C/B", "B/C"))]
    assert same[0].request_id == same[1].request_id


@pytest.mark.parametrize("text", ["", "A|", "(A", "A)", "A{3,1}", "A{x}", "A[", "A[]", "^", "A B", "*A", "A{0}",
                                  "A{0,0}"])
def test_malformed_expressions_are_refused(text):
    with pytest.raises(RequestError) as err:
        norm(text)
    assert err.value.code == "invalid_request"


def test_written_out_expressions_are_bounded():
    with pytest.raises(RequestError):
        norm("(A/B/C/D){2000}")
    assert len(automaton(norm("(A/B){1000}")).steps) == 2001


def test_text_round_trips():
    for text in ["A", "^A[T]", "A/B", "A|B", "(A|B)*", "A+/^B?", "(A/B){2,3}|C{2,}", "((A|^B)/C)*/D[X,Y]"]:
        assert norm(to_text(norm(text))) == norm(text), text


def test_glushkov_states_follow_the_written_order():
    a = automaton(norm("(A|B){2,3}|C+/^D[T]"))
    # (A|B){2,3} sorts first: A=1, B=2 (copy 1), A=3, B=4 (copy 2), A=5, B=6 (optional copy 3); C=7, ^D=8
    assert a.follow == ((1, 2, 7), (3, 4), (3, 4), (5, 6), (5, 6), (), (), (7, 8), ())
    assert a.accepting == {3, 4, 5, 6, 8} and not a.nullable
    star = automaton(norm("(A/B)*"))
    assert star.follow == ((1,), (2,), (1,)) and star.accepting == {0, 2} and star.nullable


# ---------------------------------------------------------------------------------------------- parity
def random_path(rng, depth=0):
    roll = rng.random()
    if depth >= 2 or roll < 0.35:
        return {"op": "step", "scope": [{"relation": r, "direction": rng.choice(["forward", "reverse"])}
                                        for r in rng.sample(RELATIONS, rng.choice([1, 1, 2]))],
                "node_types": rng.choice([None, None, None, rng.sample(TYPES, 1)])}
    if roll < 0.6:
        return {"op": "seq", "items": [random_path(rng, depth + 1) for _ in range(rng.randint(2, 3))]}
    if roll < 0.8:
        return {"op": "alt", "items": [random_path(rng, depth + 1) for _ in range(2)]}
    lo = rng.choice([0, 0, 1, 2])
    hi = rng.choice([None, None, max(lo, 1), max(lo, 1) + 1])
    return {"op": "repeat", "item": random_path(rng, depth + 1), "min": lo, "max": hi}


def cases(rng, n, **graph):
    for _ in range(n):
        nodes, edges = random_graph(rng, **graph)
        hier = {}
        if rng.random() < 0.25:
            hier["relation_parents"] = {"S": ["R"]}
        if rng.random() < 0.25:
            hier["type_parents"] = {"B": ["A"]}
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES, **hier)
        ids = [node_id(n) for n in nodes]
        raw = {"operation": "regular_path", "sources": rng.sample(ids, rng.randint(1, min(2, len(ids)))),
               "path": random_path(rng), "targets": rng.choice([None, rng.sample(ids, rng.randint(1, len(ids)))]),
               "answer": rng.choice(["witness", "reached"]), "certificate": rng.random() < 0.7}
        yield nodes, edges, hier, p, raw


def expected(nodes, edges, hier, res):
    req = res.request
    return regular_ends(nodes, edges, req["sources"], res.to_dict()["request"]["path"], req["targets"],
                        hier.get("type_parents"), hier.get("relation_parents"))


def test_random_paths_match_the_derivative_oracle():
    rng = random.Random("regular-paths")
    tally = {}
    for nodes, edges, hier, p, raw in cases(rng, 1500):
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        assert verify_result(res, nodes=nodes, edges=edges, **hier) == [], raw
        want = expected(nodes, edges, hier, res)
        if raw["answer"] == "witness":
            assert res.found is bool(want), raw
            if want:
                assert res.answer["witness"]["length"] == min(want.values())
            key = ("witness", res.found, res.answer["certificate"] is not None)
        else:
            assert {e["node"]: e["length"] for e in res.answer["reached"]} == want, raw
            for e in res.answer["reached"]:
                hops = res.path_to(e["node"])
                assert len(hops) == e["length"] and (not hops or hops[-1]["to"] == e["node"])
            key = ("reached", bool(want), res.answer["certificate"] is not None)
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        tally[key] = tally.get(key, 0) + 1
    assert all(tally.get((m, f, c), 0) >= 30 for m in ("witness", "reached") for f in (True, False)
               for c in (True, False)), tally


def test_limits_never_certify_and_list_only_what_is_true():
    rng = random.Random("regular-limits")
    interrupted = 0
    for nodes, edges, hier, p, raw in cases(rng, 800, max_nodes=8, max_edges=24):
        limits = {"max_expansions": rng.randint(0, 5), "max_results": rng.randint(0, 3)}
        res = execute(p, {**raw, "projection": p.identity, "limits": limits})
        assert verify_result(res, nodes=nodes, edges=edges, **hier) == []
        want = expected(nodes, edges, hier, res)
        if res.status == INTERRUPTED:
            interrupted += 1
            assert res.answer["certificate"] is None
            if raw["answer"] == "witness":
                assert res.found in (True, None)
            else:
                got = {e["node"]: e["length"] for e in res.answer["reached"]}
                assert all(want.get(n) == d for n, d in got.items())
                if res.interruption["reason"] == "output_limit":
                    assert len(got) == limits["max_results"] < len(want)
        else:
            assert res.status == COMPLETED
    assert interrupted >= 200


def test_a_certificate_without_an_edge_never_passes_where_the_edge_changes_the_answer():
    rng = random.Random("regular-soundness")
    changed = 0
    for nodes, edges, hier, p, raw in cases(rng, 1500, max_nodes=6, max_edges=14):
        if not edges or not raw["certificate"]:
            continue
        full = execute(p, {**raw, "projection": p.identity})
        used = ([h["edge"] for h in full.witness or []] if raw["answer"] == "witness"
                else [t["via"]["edge"] for t in full.answer["tree"] if t["via"]])
        gone = rng.choice(used) if used and rng.random() < 0.7 else rng.choice(edges)["id"]   # often one it used
        less = [e for e in edges if e["id"] != gone]
        q = Projection.build(nodes=nodes, edges=less, relations=RELATIONS, node_types=TYPES, **hier)
        part = execute(q, {**raw, "projection": q.identity})

        def claim(r):
            a = r.answer
            if raw["answer"] == "witness":
                return a["found"], a["witness"]["length"] if a["found"] else None
            return {e["node"]: e["length"] for e in a["reached"]}
        if claim(full) != claim(part):
            changed += 1
            assert verify_result(part, nodes=nodes, edges=edges, **hier), (raw, claim(full), claim(part))
    assert changed >= 100


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("regular-revalidate")
    tally = {}
    for nodes, edges, hier, p, raw in cases(rng, 700, max_nodes=8, max_edges=16):
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES, **hier)
        old = execute(p, {**raw, "projection": p.identity})
        full, fast = revalidate(old, q), revalidate(old, q, diff=diff(p, q))
        assert (full.verdict, full.reasons) == (fast.verdict, fast.reasons), raw
        if full.verdict == HOLDS:
            fresh = execute(q, {**raw, "projection": q.identity})
            assert fresh.status == COMPLETED
            assert verify_result(old, nodes=nodes2, edges=edges2, **hier) == []
            if raw["answer"] == "witness":
                assert fresh.found == old.found
                if old.found:
                    assert fresh.answer["witness"]["length"] == old.answer["witness"]["length"]
            else:
                assert ({e["node"]: e["length"] for e in fresh.answer["reached"]}
                        == {e["node"]: e["length"] for e in old.answer["reached"]})
        elif full.verdict == FAILS:
            fresh = execute(q, {**raw, "projection": q.identity})
            assert fresh.status != COMPLETED or verify_result(old, nodes=nodes2, edges=edges2, **hier)
        tally[full.verdict] = tally.get(full.verdict, 0) + 1
    assert all(tally.get(v, 0) >= 50 for v in (HOLDS, FAILS, UNKNOWN)), tally


# ---------------------------------------------------------------------------------------------- scenarios
NODES = [{"id": "c1", "types": ["Claim"]}, {"id": "c2", "types": ["Claim"]}, {"id": "m1", "types": ["Measurement"]},
         {"id": "m2", "types": ["Measurement"]}, {"id": "d1", "types": ["Dataset"]}, {"id": "s1", "types": ["Source"]}]
EDGES = [{"id": "x1", "source": "c1", "target": "c2", "relation": "DERIVED_FROM"},
         {"id": "x2", "source": "c2", "target": "m1", "relation": "SUPPORTED_BY"},
         {"id": "x3", "source": "c1", "target": "m2", "relation": "SUPPORTED_BY"},
         {"id": "x4", "source": "m1", "target": "d1", "relation": "IN"},
         {"id": "x5", "source": "m2", "target": "d1", "relation": "IN"},
         {"id": "x6", "source": "d1", "target": "s1", "relation": "FROM"}]


def test_provenance_chains_in_one_query():
    p = Projection.build(nodes=NODES, edges=EDGES)
    chain = "DERIVED_FROM*/SUPPORTED_BY[Measurement]/IN/FROM[Source]"
    res = execute(p, p.request("regular_path", sources=["c1"], path=chain, targets=["s1"], certificate=True))
    assert res.found and [h["edge"] for h in res.witness] == ["x3", "x5", "x6"]          # the shorter of two
    assert [h["state"] for h in res.witness] == [2, 3, 4]
    reached = execute(p, p.request("regular_path", sources=["c1"], path="DERIVED_FROM*/SUPPORTED_BY",
                                   answer="reached", certificate=True))
    assert {e["node"]: e["length"] for e in reached.answer["reached"]} == {"m1": 2, "m2": 1}
    assert [h["edge"] for h in reached.path_to("m1")] == ["x1", "x2"]
    assert reached.reachable("m2") is True and reached.reachable("d1") is False
    back = execute(p, p.request("regular_path", sources=["s1"], path="^FROM/^IN/^SUPPORTED_BY/^DERIVED_FROM*",
                                answer="reached"))
    assert {e["node"] for e in back.answer["reached"]} == {"c1", "c2"}
    for r in (res, reached, back):
        assert verify_result(r, nodes=NODES, edges=EDGES) == []


def test_the_empty_walk_matches_a_nullable_path():
    p = Projection.build(nodes=NODES, edges=EDGES)
    res = execute(p, p.request("regular_path", sources=["c1"], path="DERIVED_FROM*", targets=["c1"],
                               certificate=True))
    assert res.found and res.answer["witness"]["length"] == 0 and res.answer["certificate"] is None
    assert res.stats["expansions"] == 0
    none = execute(p, p.request("regular_path", sources=["c1"], path="DERIVED_FROM+", targets=["c1"],
                                certificate=True))
    assert none.found is False and none.answer["certificate"]["bound"] is None


def test_unknown_names_fail_as_in_every_scope():
    p = Projection.build(nodes=NODES, edges=EDGES)
    bad = execute(p, p.request("regular_path", sources=["c1"], path="NOPE*"))
    assert bad.status == "failed" and bad.error["code"] == "unknown_relation"
    bad = execute(p, p.request("regular_path", sources=["c1"], path="IN[Nothing]"))
    assert bad.error["code"] == "unknown_node_type"
