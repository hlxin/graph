"""Positive Datalog: the least model by semi-naive evaluation, each fact with a derivation. The oracle is a naive
fixpoint written separately (every rule over every fact, again and again, until nothing changes); networkx checks
transitive closure; every derivation and every certificate is checked by the verifier, and forgeries are refused."""

import copy
import json
import os
import random
import subprocess
import sys
import time

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, normalize_request, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.errors import RequestError
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from oracle import RELATIONS, TYPES, node_id, random_graph
from test_certificates import reseal
from test_revalidate import edited


def var(x):
    return isinstance(x, str) and x.startswith("?")


def naive_model(nodes, edges, program, relation_parents=None):
    """The least model, naively: the inputs from the records, then every rule over every fact until a pass adds
    nothing."""
    up = {}
    for child, parents in (relation_parents or {}).items():
        todo, seen = list(parents), set()
        while todo:
            x = todo.pop()
            if x not in seen:
                seen.add(x)
                todo.extend((relation_parents or {}).get(x, ()))
        up[child] = seen
    model = {"node": {(node_id(n),) for n in nodes},
             "type": {(node_id(n), t) for n in nodes if not isinstance(n, str) for t in n["types"]},
             "edge": {(e["id"], e["source"], e["target"], r) for e in edges for r in {e["relation"]} | up.get(e["relation"], set())}}
    for r in program:
        model.setdefault(r["head"]["predicate"], set())
    changed = True
    while changed:
        changed = False
        for r in program:
            subs = [{}]
            for atom in r["body"]:
                nxt = []
                for s in subs:
                    for fact in model.get(atom["predicate"], ()):
                        s2, ok = dict(s), True
                        for t, v in zip(atom["args"], fact):
                            if var(t):
                                ok = s2.setdefault(t, v) == v
                            else:
                                ok = (t["const"] if isinstance(t, dict) else t) == v
                            if not ok:
                                break
                        if ok:
                            nxt.append(s2)
                subs = nxt
            for s in subs:
                head = tuple(s[t] if var(t) else t for t in r["head"]["args"])
                if head not in model[r["head"]["predicate"]]:
                    model[r["head"]["predicate"]].add(head)
                    changed = True
    return model


def matches(model, query):
    out = []
    for fact in model[query["predicate"]]:
        s, ok = {}, True
        for t, v in zip(query["args"], fact):
            ok = s.setdefault(t, v) == v if var(t) else t == v
            if not ok:
                break
        if ok:
            out.append(list(fact))
    return sorted(out)


def atom(pred, *args):
    return {"predicate": pred, "args": list(args)}


def random_program(rng, ids):
    """A few derived predicates p0..p2 of arity 1 to 3, each rule's body one to three atoms over the inputs and the
    derived predicates, its head's variables drawn from its body (range-restricted), constants now and then."""
    arity = {f"p{k}": rng.randint(1, 3) for k in range(rng.randint(1, 3))}
    inputs = {"node": 1, "type": 2, "edge": 4}
    program = []
    for k in range(rng.randint(1, 5)):
        head = rng.choice(sorted(arity))
        body, names = [], []
        for _ in range(rng.randint(1, 3)):
            pred = rng.choice(sorted(inputs) + sorted(arity) * 2) if program else rng.choice(sorted(inputs))
            n = inputs.get(pred) or arity[pred]
            args = []
            for i in range(n):
                roll = rng.random()
                if roll < 0.15:
                    args.append(rng.choice(ids + list(RELATIONS) + list(TYPES)))
                elif names and roll < 0.55:
                    args.append(rng.choice(names))
                else:
                    names.append(f"?v{len(names)}")
                    args.append(names[-1])
            body.append(atom(pred, *args))
        if not names:
            names = ["?v0"]
            body.append(atom("node", "?v0"))
        program.append({"id": f"r{k}", "head": atom(head, *[rng.choice(names) for _ in range(arity[head])]),
                        "body": body})
    defined = sorted({r["head"]["predicate"] for r in program})
    for r in program:                                        # a body predicate no rule defines becomes node(...)
        r["body"] = [b if b["predicate"] in inputs or b["predicate"] in defined else atom("node", b["args"][0])
                     for b in r["body"]]
        bodyvars = {t for b in r["body"] for t in b["args"] if var(t)}
        if not bodyvars:
            r["body"].append(atom("node", "?v0"))
            bodyvars = {"?v0"}
        r["head"]["args"] = [t if not var(t) or t in bodyvars else sorted(bodyvars)[0] for t in r["head"]["args"]]
    q = rng.choice(defined)
    query = atom(q, *[rng.choice(["?a", "?b", rng.choice(ids)]) for _ in range(arity[q])])
    return program, query


def run(rng, n=6, m=12):
    nodes, edges = random_graph(rng, max_nodes=n, max_edges=m)
    ids = [node_id(x) for x in nodes]
    program, query = random_program(rng, ids)
    return nodes, edges, program, query


def build(nodes, edges):
    return Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)


def rows(res):
    return [list(r["values"]) for r in res.answer["rows"]]


def test_the_least_model_agrees_with_a_naive_fixpoint():
    rng = random.Random("datalog")
    tally = {"empty": 0, "some": 0, "recursive": 0}
    for _ in range(400):
        nodes, edges, program, query = run(rng)
        p = build(nodes, edges)
        want = matches(naive_model(nodes, edges, program), query)
        if len(want) > 5000:
            continue
        for extra in ({}, {"certificate": True}, {"derivations": False}):
            res = execute(p, p.request("datalog", program=program, query=query, limits={"max_results": 10 ** 6},
                                       **extra))
            assert res.status == COMPLETED, res.error
            assert rows(res) == want and res.answer["total"] == len(want), (program, query)
            assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        tally["some" if want else "empty"] += 1
        tally["recursive"] += any(b["predicate"] == r["head"]["predicate"] for r in program for b in r["body"])
    assert min(tally.values()) >= 60, tally


def test_transitive_closure_agrees_with_networkx():
    nx = pytest.importorskip("networkx")
    rng = random.Random("datalog closure")
    program = [{"id": "base", "head": atom("reach", "?x", "?y"), "body": [atom("edge", "?e", "?x", "?y", "R")]},
               {"id": "step", "head": atom("reach", "?x", "?z"),
                "body": [atom("reach", "?x", "?y"), atom("edge", "?e", "?y", "?z", "R")]}]
    for _ in range(8):
        n = 120
        ids = [f"v{i:03d}" for i in range(n)]
        edges = [{"id": f"e{k:04d}", "source": rng.choice(ids), "target": rng.choice(ids), "relation": "R"}
                 for k in range(rng.choice([150, 300]))]
        p = Projection.build(nodes=ids, edges=edges)
        res = execute(p, p.request("datalog", program=program, query=atom("reach", "?x", "?y"), certificate=True,
                                   limits={"max_results": 10 ** 6, "max_expansions": 10 ** 8}))
        g = nx.DiGraph()
        g.add_nodes_from(ids)
        g.add_edges_from((e["source"], e["target"]) for e in edges)
        below = {u: nx.descendants(g, u) for u in ids}       # a walk of one hop or more: u itself only on a cycle
        closure = sorted([u, v] for u in ids for v in ids
                         if (v in below[u] if u != v else any(w == u or u in below[w] for w in g.successors(u))))
        assert rows(res) == closure
        assert verify_result(res, nodes=ids, edges=edges) == []


def test_the_relation_hierarchy_reaches_edges_of_every_relation_below():
    nodes = ["a", "b", "c"]
    edges = [{"id": "e1", "source": "a", "target": "b", "relation": "CITES"},
             {"id": "e2", "source": "b", "target": "c", "relation": "USES"}]
    parents = {"CITES": ["LINKS"], "USES": ["LINKS"]}
    p = Projection.build(nodes=nodes, edges=edges, relation_parents=parents)
    program = [{"id": "r", "head": atom("linked", "?x", "?y"), "body": [atom("edge", "?e", "?x", "?y", "LINKS")]}]
    res = execute(p, p.request("datalog", program=program, query=atom("linked", "?x", "?y"), certificate=True))
    assert rows(res) == [["a", "b"], ["b", "c"]]
    assert verify_result(res, nodes=nodes, edges=edges, relation_parents=parents) == []
    assert rows(res) == matches(naive_model(nodes, edges, program, parents), atom("linked", "?x", "?y"))


def tc_result(certificate=True):
    nodes = ["a", "b", "c", "d"]
    edges = [{"id": "e1", "source": "a", "target": "b", "relation": "R"},
             {"id": "e2", "source": "b", "target": "c", "relation": "R"},
             {"id": "e3", "source": "c", "target": "d", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    program = [{"id": "base", "head": atom("reach", "?x", "?y"), "body": [atom("edge", "?e", "?x", "?y", "R")]},
               {"id": "step", "head": atom("reach", "?x", "?z"),
                "body": [atom("reach", "?x", "?y"), atom("edge", "?e", "?y", "?z", "R")]}]
    res = execute(p, p.request("datalog", program=program, query=atom("reach", "a", "?y"), certificate=certificate))
    return nodes, edges, res


def test_forged_derivations_and_models_are_refused():
    nodes, edges, res = tc_result()
    assert rows(res) == [["a", "b"], ["a", "c"], ["a", "d"]] and verify_result(res, nodes=nodes, edges=edges) == []

    def forged(change):
        data = copy.deepcopy(thaw(res.to_dict()))
        change(data["answer"])
        return verify_result(reseal(data), nodes=nodes, edges=edges)

    def find(a, args):
        return next(k for k, f in enumerate(a["facts"]) if f["args"] == args)

    def dropped(a):                                         # the model loses reach(b, d): no longer closed
        k = find(a, ["b", "d"])
        a["facts"] = [f for i, f in enumerate(a["facts"]) if i != k]
        for f in a["facts"]:
            for prem in f["premises"]:
                if "fact" in prem and prem["fact"] > k:
                    prem["fact"] -= 1
        for r in a["rows"]:
            r["fact"] = r["fact"] - (r["fact"] > k)
        a["model"]["facts"] -= 1

    def invented(a):                                        # reach(d, a), "derived" from an edge that is not there
        a["facts"].append({"predicate": "reach", "args": ["d", "a"], "rule": "base", "rank": 1,
                           "premises": [{"input": "edge", "args": ["e9", "d", "a", "R"]}]})
        a["facts"].sort(key=lambda f: (f["rank"], f["predicate"], f["args"]))
    assert forged(dropped)
    assert forged(invented)
    assert forged(lambda a: a["facts"][find(a, ["a", "c"])]["premises"][1].__setitem__(
        "args", ["e3", "c", "d", "R"]))                     # a premise that does not match the rule's body
    assert forged(lambda a: a["rows"].pop())                # a complete answer leaving a match out
    assert forged(lambda a: a["facts"][find(a, ["a", "d"])].__setitem__("rank", 1))


def test_an_interrupted_evaluation_claims_only_what_it_derived():
    rng = random.Random("datalog interrupted")
    seen = set()
    for _ in range(300):
        nodes, edges, program, query = run(rng)
        p = build(nodes, edges)
        want = matches(naive_model(nodes, edges, program), query)
        res = execute(p, p.request("datalog", program=program, query=query,
                                   limits={"max_expansions": rng.randint(0, 60), "max_results": 10 ** 6}))
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        assert all(r in want for r in rows(res))
        if res.status == INTERRUPTED:
            assert res.answer["total"] is None and res.answer["exists"] in (True, None)
        else:
            assert rows(res) == want
        seen.add(res.status)
    assert seen == {COMPLETED, INTERRUPTED}


def test_the_same_request_gives_the_same_result_under_any_hash_seed():
    script = """
import json, sys
sys.path.insert(0, 'src'); sys.path.insert(0, 'tests')
from test_datalog import tc_result
from graph_runtime import Projection, execute
nodes = [f"n{i}" for i in range(30)]
edges = [{"id": f"e{k}", "source": f"n{(k * 7) % 30}", "target": f"n{(k * 11 + 3) % 30}", "relation": "R"} for k in range(60)]
p = Projection.build(nodes=nodes, edges=edges)
program = [{"id": "base", "head": {"predicate": "reach", "args": ["?x", "?y"]}, "body": [{"predicate": "edge", "args": ["?e", "?x", "?y", "R"]}]},
           {"id": "step", "head": {"predicate": "reach", "args": ["?x", "?z"]}, "body": [{"predicate": "reach", "args": ["?x", "?y"]}, {"predicate": "edge", "args": ["?e", "?y", "?z", "R"]}]}]
res = execute(p, p.request("datalog", program=program, query={"predicate": "reach", "args": ["?x", "?y"]}, certificate=True, limits={"max_results": 10**6}))
print(res.result_id)
"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ids = {subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True, check=True,
                          env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("0", "1", "12345")}
    assert len(ids) == 1, ids


def test_requests_are_checked():
    base = {"operation": "datalog", "projection": "sha256:" + "0" * 64, "query": atom("p", "?x")}
    bad = [
        [{"id": "r", "head": atom("p", "?x"), "body": [atom("node", "?y")]}],                     # unsafe head
        [{"id": "r", "head": atom("p", "?x"), "body": [atom("edge", "?x", "?y")]}],               # edge takes 4
        [{"id": "r", "head": atom("node", "?x"), "body": [atom("node", "?x")]}],                  # defines an input
        [{"id": "r", "head": atom("p", "?x"), "body": [atom("q", "?x")]}],                        # q undefined
        [{"id": "r", "head": atom("p", "?x"), "body": [atom("node", "?x")]},
         {"id": "r", "head": atom("p", "?x"), "body": [atom("node", "?x")]}],                     # two ids alike
        [{"id": "r", "head": atom("p", "?x"), "body": [atom("node", "?x")]},
         {"id": "s", "head": atom("p", "?x", "?x"), "body": [atom("node", "?x")]}],               # two arities
        [],
    ]
    for program in bad:
        with pytest.raises(RequestError) as err:
            normalize_request({**base, "program": program})
        assert err.value.code == "invalid_request"
    with pytest.raises(RequestError):
        normalize_request({**base, "program": [{"id": "r", "head": atom("p", "?x"), "body": [atom("node", "?x")]}],
                           "query": atom("node", "?x")})                                         # query an input
    a = normalize_request({**base, "program": [{"id": "b", "head": atom("p", "?x"), "body": [atom("node", "?x")]},
                                               {"id": "a", "head": atom("p", "x"), "body": []}]})
    b = normalize_request({**base, "program": [{"id": "a", "head": atom("p", "x"), "body": []},
                                               {"id": "b", "head": atom("p", "?x"), "body": [atom("node", "?x")]}]})
    assert a.request_id == b.request_id                    # rules in any order: one request


def test_a_long_rule_needs_no_recursion():
    n = 1500
    nodes = ["a"]
    edges = [{"id": "loop", "source": "a", "target": "a", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    body = [atom("edge", f"?e{i}", f"?x{i}", f"?x{i + 1}", "R") for i in range(n)]
    program = [{"id": "chain", "head": atom("ends", "?x0", f"?x{n}"), "body": body}]
    res = execute(p, p.request("datalog", program=program, query=atom("ends", "?x", "?y")))
    assert res.status == COMPLETED and rows(res) == [["a", "a"]]
    assert verify_result(res, nodes=nodes, edges=edges) == []


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("datalog revalidation")
    tally = {}
    for _ in range(300):
        nodes, edges, program, query = run(rng)
        nodes2, edges2 = edited(rng, nodes, edges)
        p, q = build(nodes, edges), build(nodes2, edges2)
        request = {"operation": "datalog", "program": program, "query": query, "certificate": rng.random() < 0.7,
                   "limits": {"max_results": 10 ** 6}}
        old = execute(p, {**request, "projection": p.identity})
        verdict = revalidate(old, q, diff=diff(p, q)).verdict
        fresh = execute(q, {**request, "projection": q.identity})
        if verdict == HOLDS:
            assert rows(fresh) == rows(old) and fresh.answer["total"] == old.answer["total"]
        elif verdict == FAILS:
            assert verify_result(old, nodes=nodes2, edges=edges2)
        tally[verdict] = tally.get(verdict, 0) + 1
    assert tally.get(HOLDS, 0) >= 30 and tally.get(FAILS, 0) >= 20, tally


def test_datalog_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from graph_runtime import json_schema
    from graph_runtime._validation import shape_problems
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("datalog schema")
    for _ in range(80):
        nodes, edges, program, query = run(rng)
        p = build(nodes, edges)
        for limits in ({}, {"max_expansions": rng.randint(0, 30)}, {"max_results": 1}):
            data = json.loads(json.dumps(thaw(execute(p, p.request("datalog", program=program, query=query,
                                                                   certificate=rng.random() < 0.5,
                                                                   limits=limits)).to_dict())))
            assert [e.message for e in validator.iter_errors(data)] == [] and shape_problems("result", data) == []


def test_transitive_closure_of_a_few_thousand_edges():
    rng = random.Random("datalog large")
    n = 2000
    ids = [f"v{i:04d}" for i in range(n)]
    edges = [{"id": f"e{k:05d}", "source": ids[i], "target": ids[j], "relation": "R"}
             for k, (i, j) in enumerate(sorted((rng.randrange(n), rng.randrange(n)) for _ in range(n)))]
    p = Projection.build(nodes=ids, edges=edges)
    program = [{"id": "base", "head": atom("reach", "?x", "?y"), "body": [atom("edge", "?e", "?x", "?y", "R")]},
               {"id": "step", "head": atom("reach", "?x", "?z"),
                "body": [atom("reach", "?x", "?y"), atom("edge", "?e", "?y", "?z", "R")]}]
    start = time.perf_counter()
    res = execute(p, p.request("datalog", program=program, query=atom("reach", ids[0], "?y"), answer="count",
                               limits={"max_expansions": 10 ** 8}))
    assert res.status == COMPLETED and time.perf_counter() - start < 60


def test_certificate_options_and_limits_give_readable_results():
    from graph_runtime import Result
    nodes = ["a", "b"]
    p = Projection.build(nodes=nodes, edges=[])
    program = [{"id": "r", "head": atom("p", "?x"), "body": [atom("node", "?x")]}]
    for extra in ({"certificate": True, "limits": {"max_results": 1}}, {"certificate": True, "derivations": False},
                  {"certificate": True, "derivations": False, "limits": {"max_results": 1}}):
        res = execute(p, p.request("datalog", program=program, query=atom("p", "?x"), **extra))
        assert verify_result(res, nodes=nodes, edges=[]) == [] and contract_problems(res.to_dict()) == []
        assert Result.from_dict(res.to_dict()).result_id == res.result_id
        assert res.answer["total"] == 2
        if "max_results" in extra.get("limits", {}):
            assert res.status == INTERRUPTED and res.answer["certificate"] is None


def test_verifying_an_empty_model_streams():
    import tracemalloc
    nodes = [f"n{i:03d}" for i in range(400)]
    p = Projection.build(nodes=nodes, edges=[])
    program = [{"id": "r", "head": atom("q"), "body": [atom("node", "?x"), atom("node", "?y"), atom("node", "missing")]}]
    res = execute(p, p.request("datalog", program=program, query=atom("q"), certificate=True))
    assert res.answer["total"] == 0
    tracemalloc.start()
    assert verify_result(res, nodes=nodes, edges=[]) == []
    assert tracemalloc.get_traced_memory()[1] < 5_000_000
    tracemalloc.stop()


def test_index_building_is_charged():
    """Fifteen rules probe a 200-fact relation, each on another set of bound arguments, so each builds its own index:
    every index reads the whole relation, and those reads are expansions even when no probe matches."""
    nodes = [f"n{i:03d}" for i in range(200)]
    p = Projection.build(nodes=nodes, edges=[])
    program = [{"id": "w", "head": atom("w", "?a", "?a", "?a", "?a"), "body": [atom("node", "?a")]}]
    for mask in range(1, 16):
        args = ["missing" if mask >> i & 1 else f"?f{i}" for i in range(4)]
        program.append({"id": f"probe{mask:02d}", "head": atom("hit", "?h"),
                        "body": [atom("node", "?h"), atom("w", *args)]})
    res = execute(p, p.request("datalog", program=program, query=atom("hit", "?h"), answer="count",
                               limits={"max_expansions": 10 ** 8}))
    assert res.status == COMPLETED and res.answer["total"] == 0
    assert res.stats["expansions"] >= 15 * 200


def test_type_loading_reads_only_the_types_a_node_has():
    types = [f"T{k:04d}" for k in range(3000)]
    nodes = [f"n{i:05d}" for i in range(10_000)]
    p = Projection.build(nodes=nodes, edges=[], node_types=types)
    program = [{"id": "r", "head": atom("typed", "?n"), "body": [atom("type", "?n", "?t")]}]
    start = time.perf_counter()
    res = execute(p, p.request("datalog", program=program, query=atom("typed", "?n"), answer="count"))
    assert res.status == COMPLETED and res.answer["total"] == 0 and time.perf_counter() - start < 2


def test_verifying_a_long_rule_keeps_one_substitution():
    import tracemalloc
    n = 2400
    nodes = ["a", "b"]
    edges = [{"id": "aa", "source": "a", "target": "a", "relation": "R"},
             {"id": "ab", "source": "a", "target": "b", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    body = [atom("edge", f"?e{i}", f"?x{i}", f"?x{i + 1}", "R") for i in range(n)]
    res = execute(p, p.request("datalog", program=[{"id": "chain", "head": atom("ends", "?x0", f"?x{n}"),
                                                    "body": body}],
                               query=atom("ends", "?x", "?y"), certificate=True, limits={"max_expansions": 10 ** 7}))
    assert rows(res) == [["a", "a"], ["a", "b"]]
    tracemalloc.start()
    assert verify_result(res, nodes=nodes, edges=edges) == []
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 20_000_000, peak


def test_interrupted_results_do_not_depend_on_the_hash_seed():
    script = """
import json, random, sys
sys.path.insert(0, 'src'); sys.path.insert(0, 'tests')
from graph_runtime import Projection, execute
from test_datalog import run, build
A = lambda pred, *args: {"predicate": pred, "args": list(args)}
p = Projection.build(nodes=[], edges=[])
program = [{"id": "f1", "head": A("p", "a"), "body": []}, {"id": "f2", "head": A("q", "b"), "body": []},
           {"id": "r1", "head": A("p", "?x"), "body": [A("q", "?x")]}, {"id": "r2", "head": A("q", "?x"), "body": [A("p", "?x")]}]
out = [execute(p, p.request("datalog", program=program, query=A("p", "?x"), limits={"max_expansions": b})).result_id
       for b in range(0, 12)]
rng = random.Random("seeds")
for _ in range(60):
    nodes, edges, prog, query = run(rng)
    q = build(nodes, edges)
    out.append(execute(q, q.request("datalog", program=prog, query=query,
                                    limits={"max_expansions": rng.randint(0, 40)})).result_id)
print(json.dumps(out))
"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runs = {subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True, check=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("0", "1", "7")}
    assert len(runs) == 1


def test_a_deep_relation_hierarchy_is_not_walked_before_the_budget():
    import tracemalloc
    names = [f"R{k:04d}" for k in range(3000)]
    parents = {names[k]: [names[k + 1]] for k in range(len(names) - 1)}
    p = Projection.build(nodes=["a", "b"], edges=[{"id": "e", "source": "a", "target": "b", "relation": names[-1]}],
                         relation_parents=parents)
    program = [{"id": "r", "head": atom("linked", "?x"), "body": [atom("edge", "?e", "?x", "?y", "?r")]}]
    tracemalloc.start()
    res = execute(p, p.request("datalog", program=program, query=atom("linked", "?x"), limits={"max_expansions": 0}))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert res.status == INTERRUPTED and peak < 5_000_000, peak
    done = execute(p, p.request("datalog", program=program, query=atom("linked", "?x")))
    assert rows(done) == [["a"]]


def test_premises_are_kept_only_when_shown_and_charged():
    import tracemalloc
    n = 2000
    nodes = [f"n{i:04d}" for i in range(n)]
    p = Projection.build(nodes=nodes, edges=[])
    body = [atom("node", x) for x in nodes] + [atom("node", "?x")]
    program = [{"id": "wide", "head": atom("all", "?x"), "body": body}]
    tracemalloc.start()
    res = execute(p, p.request("datalog", program=program, query=atom("all", "?x"), answer="count",
                               derivations=False, limits={"max_expansions": 12000}))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert res.status == COMPLETED and res.answer["total"] == n and peak < 15_000_000, peak
    shown = execute(p, p.request("datalog", program=program, query=atom("all", "?x"), answer="count",
                                 limits={"max_expansions": 12000}))
    assert shown.status == INTERRUPTED                     # n derivations of n + 1 premises each: charged


def test_a_dense_relation_hierarchy_is_walked_within_the_budget():
    parents = {"leaf": [f"mid{k:04d}" for k in range(1000)]}
    parents.update({f"mid{k:04d}": [f"top{j:04d}" for j in range(1000)] for k in range(1000)})
    p = Projection.build(nodes=["a", "b"], edges=[{"id": "e", "source": "a", "target": "b", "relation": "leaf"}],
                         relation_parents=parents)
    program = [{"id": "r", "head": atom("linked", "?x"), "body": [atom("edge", "?e", "?x", "?y", "?r")]}]
    start = time.perf_counter()
    res = execute(p, p.request("datalog", program=program, query=atom("linked", "?x"), limits={"max_expansions": 1}))
    assert res.status == INTERRUPTED and time.perf_counter() - start < 0.5


def test_join_orders_are_charged():
    """A recursive rule repeating one atom 400 times has 400 join orders of 400 steps: each is charged before it is
    built, so a small budget stops the evaluation instead of planning them all."""
    p = Projection.build(nodes=[], edges=[])
    body = [atom("p", "absent")] * 400 + [atom("p", "?x")]
    program = [{"id": "seed", "head": atom("p", "present"), "body": []},
               {"id": "loop", "head": atom("p", "?x"), "body": body}]
    res = execute(p, p.request("datalog", program=program, query=atom("p", "?x"), answer="count", derivations=False,
                               limits={"max_expansions": 401}))
    assert res.status == INTERRUPTED and res.stats["expansions"] == 401
    done = execute(p, p.request("datalog", program=program, query=atom("p", "?x"), answer="count",
                                derivations=False, limits={"max_expansions": 10 ** 7}))
    assert done.status == COMPLETED and done.answer["total"] == 1


def test_wide_predicates_are_refused():
    base = {"operation": "datalog", "projection": "sha256:" + "0" * 64}
    with pytest.raises(RequestError):
        normalize_request({**base, "query": atom("p", *["?x"] * 33),
                           "program": [{"id": "r", "head": atom("p", *["?x"] * 33), "body": [atom("node", "?x")]}]})


def test_a_closure_check_too_large_says_so(monkeypatch):
    import graph_runtime.verify as verify
    p = Projection.build(nodes=[], edges=[])
    program = [{"id": "fact", "head": atom("q"), "body": []},
               {"id": "wide", "head": atom("q"), "body": [atom("node", f"?x{i}") for i in range(24)]}]
    res = execute(p, p.request("datalog", program=program, query=atom("q"), certificate=True))
    assert res.answer["total"] == 1 and verify_result(res, nodes=[], edges=[]) == []
    monkeypatch.setattr(verify, "DATALOG_CHECK_READS", 10_000)
    start = time.perf_counter()
    problems = verify_result(res, nodes=["a", "b"], edges=[])        # 2 ** 24 bindings over two nodes
    assert any("not checked" in x for x in problems) and time.perf_counter() - start < 2


def test_checking_a_derivation_reads_only_its_premises():
    import tracemalloc
    parents = {"leaf": [f"up{k:04d}" for k in range(1000)]}
    nodes = ["a", "b"]
    edges = [{"id": f"e{i:04d}", "source": "a", "target": "b", "relation": "leaf"} for i in range(1000)]
    p = Projection.build(nodes=nodes, edges=edges, relation_parents=parents)
    res = execute(p, p.request("datalog", program=[{"id": "r", "head": atom("q"), "body": []}], query=atom("q")))
    tracemalloc.start()
    assert verify_result(res, nodes=nodes, edges=edges, relation_parents=parents) == []
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 10_000_000, peak


def test_types_are_counted_before_they_are_read():
    """A node of 5,000 types: their facts are charged as one batch before any is read, so a budget one short stops
    the loading with nothing read."""
    types = [f"T{k:05d}" for k in range(5000)]
    p = Projection.build(nodes=[{"id": "n", "types": types}], edges=[], node_types=types)
    program = [{"id": "r", "head": atom("typed", "?n"), "body": [atom("type", "?n", "?t")]}]
    short = execute(p, p.request("datalog", program=program, query=atom("typed", "?n"),
                                 limits={"max_expansions": 4999}))
    assert short.status == INTERRUPTED and short.stats["expansions"] == 4999 and short.answer["total"] is None
    done = execute(p, p.request("datalog", program=program, query=atom("typed", "?n")))
    assert done.status == COMPLETED and rows(done) == [["n"]]
