"""Injective patterns (subgraph monomorphism): distinct variables bind distinct nodes and distinct steps link distinct
edges. They are the rows of the same pattern without injectivity that use no node or edge twice, in the same
canonical order: the brute-force oracle's rows, filtered, are the oracle, row for row. On simple directed graphs,
networkx's subgraph monomorphisms count them independently."""

import random

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, UNSUPPORTED, Projection, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.errors import RequestError
from graph_runtime.result import contract_problems
from oracle import RELATIONS, TYPES, node_id, pattern_rows, random_graph
from test_cyclic_pattern import random_cyclic, table


def distinct(rows):
    """The oracle's rows that bind no node twice and link no edge twice."""
    out = []
    for bind, links in rows:
        nodes = [v for v in bind.values() if v is not None]
        used = [x for x in links if x is not None]
        if len(set(nodes)) == len(nodes) and len(set(used)) == len(used):
            out.append((dict(bind), links))
    return out


def required(rng, ids):
    """A random pattern, tree or cyclic, with no optional step."""
    match, steps = random_cyclic(rng, ids)
    if rng.random() < 0.4:                                  # a tree: the closing steps dropped
        steps = [s for s in steps if "to" not in s]
    return match, [{**s, "optional": False} if "var" in s else s for s in steps]


def test_injective_patterns_match_the_filtered_brute_force_row_for_row():
    rng = random.Random("injective-patterns")
    rows = trees = fewer = 0
    for _ in range(400):
        nodes, edges = random_graph(rng, max_nodes=7, max_edges=18)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        match, steps = required(rng, [node_id(n) for n in nodes])
        every = pattern_rows(nodes, edges, match, steps)
        if len(every) > 50_000:
            continue
        want = distinct(every)
        res = execute(p, p.request("pattern", match=match, steps=steps, injective=True,
                                   limits={"max_results": 10**6}))
        assert res.status == COMPLETED, res.error
        assert table(res) == want, (match, steps)
        assert res.answer["total"] == len(want) and res.exists is bool(want)
        for answer in ("count", "exists"):
            other = execute(p, p.request("pattern", match=match, steps=steps, injective=True, answer=answer))
            assert other.answer["total"] == len(want) and other.answer["rows"] == ()
        assert verify_result(res, nodes=nodes, edges=edges) == []
        assert contract_problems(res.to_dict()) == []
        rows += len(want)
        trees += not any("to" in s for s in steps)
        fewer += len(want) < len(every)
    assert rows > 300 and trees > 100 and fewer > 100


def test_limits_list_a_prefix_and_never_deny():
    rng = random.Random("injective-limits")
    unknown = 0
    for _ in range(300):
        nodes, edges = random_graph(rng, max_nodes=7, max_edges=18)
        p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
        match, steps = required(rng, [node_id(n) for n in nodes])
        want = distinct(pattern_rows(nodes, edges, match, steps))
        limits = {"max_expansions": rng.randint(0, 40), "max_results": rng.randint(0, 4)}
        res = execute(p, p.request("pattern", match=match, steps=steps, injective=True, limits=limits))
        assert verify_result(res, nodes=nodes, edges=edges) == []
        got = table(res)
        assert got == want[:len(got)]
        if res.status == INTERRUPTED:
            assert res.exists in (True, None) and (res.exists is None or want)
            assert res.answer["total"] in (None, len(want))
            unknown += res.answer["total"] is None and res.exists is True
        else:
            assert res.answer["total"] == len(want)
    assert unknown >= 10


def test_monomorphisms_agree_with_networkx():
    """Every pattern edge a step (a tree's steps and closing steps), every host edge one relation, no parallel edges:
    the rows are exactly networkx's subgraph monomorphisms of the pattern digraph."""
    nx = pytest.importorskip("networkx")
    from networkx.algorithms.isomorphism import DiGraphMatcher
    rng = random.Random("monomorphisms")
    shapes = {                                              # name: pattern edges (variables v0..)
        "path": [("v0", "v1"), ("v1", "v2")],
        "star": [("v0", "v1"), ("v0", "v2"), ("v0", "v3")],
        "triangle": [("v0", "v1"), ("v1", "v2"), ("v2", "v0")],
        "square with a chord": [("v0", "v1"), ("v1", "v2"), ("v2", "v3"), ("v3", "v0"), ("v0", "v2")],
        "two-cycle": [("v0", "v1"), ("v1", "v0")],
    }
    for name, pedges in shapes.items():
        for _ in range(6):
            n = 18
            pairs = {(f"n{rng.randrange(n):02d}", f"n{rng.randrange(n):02d}") for _ in range(70)}
            pairs = sorted((a, b) for a, b in pairs if a != b)
            nodes = [f"n{i:02d}" for i in range(n)]
            edges = [{"id": f"e{k:03d}", "source": a, "target": b, "relation": "R"} for k, (a, b) in enumerate(pairs)]
            steps, bound = [], {"v0"}
            for a, b in pedges:                              # a tree edge binds a new variable; the rest close
                if b not in bound:
                    steps.append({"from": a, "var": b, "scope": [("R", "forward")]})
                    bound.add(b)
                elif a not in bound:
                    steps.append({"from": b, "var": a, "scope": [("R", "reverse")]})
                    bound.add(a)
                else:
                    steps.append({"from": a, "to": b, "scope": [("R", "forward")]})
            p = Projection.build(nodes=nodes, edges=edges)
            res = execute(p, p.request("pattern", match={"var": "v0", "nodes": nodes}, steps=steps, injective=True,
                                       answer="count"))
            host = nx.DiGraph(pairs)
            host.add_nodes_from(nodes)
            want = sum(1 for _ in DiGraphMatcher(host, nx.DiGraph(pedges)).subgraph_monomorphisms_iter())
            assert res.answer["total"] == want, name


def test_an_injective_answer_with_a_repeated_node_is_rejected():
    nodes = ["a", "b"]
    edges = [{"id": "ab", "source": "a", "target": "b", "relation": "R"},
             {"id": "ba", "source": "b", "target": "a", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    steps = [{"from": "x", "var": "y", "scope": [("R", "forward")]}, {"from": "y", "var": "z", "scope": [("R", "forward")]}]
    plain = execute(p, p.request("pattern", match={"var": "x", "nodes": ["a"]}, steps=steps))
    inj = execute(p, p.request("pattern", match={"var": "x", "nodes": ["a"]}, steps=steps, injective=True))
    assert plain.answer["total"] == 1 and inj.answer["total"] == 0 and inj.exists is False   # a -> b -> a
    forged = thaw(inj.to_dict())
    forged["answer"] = {**thaw(plain.to_dict())["answer"]}
    assert verify_result(forged, nodes=nodes, edges=edges)


def test_injective_patterns_have_no_optional_steps():
    p = Projection.build(nodes=["a"], edges=[])
    with pytest.raises(RequestError) as err:
        p.request("pattern", match={"var": "x", "nodes": ["a"]}, injective=True,
                  steps=[{"from": "x", "var": "y", "scope": [("R", "forward")], "optional": True}])
    assert (err.value.status, err.value.code) == (UNSUPPORTED, "unsupported_mode")


@pytest.mark.parametrize("injective", [True, False])
@pytest.mark.parametrize("answer", ["rows", "count", "exists"])
def test_a_pattern_as_long_as_a_long_chain_is_matched(injective, answer):
    """Rows are found by an explicit stack, not by recursion: a chain of 1,100 steps is no deeper than any other."""
    n = 1101
    nodes = [f"n{i:04d}" for i in range(n)]
    edges = [{"id": f"e{i:04d}", "source": nodes[i], "target": nodes[i + 1], "relation": "R"} for i in range(n - 1)]
    steps = [{"from": f"v{i}", "var": f"v{i + 1}", "scope": [("R", "forward")]} for i in range(n - 1)]
    if not injective:
        steps.append({"from": f"v{n - 1}", "to": f"v{n - 1}", "scope": [("R", "forward")]})   # a loop: no row
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("pattern", match={"var": "v0", "nodes": [nodes[0]]}, steps=steps, injective=injective,
                               answer=answer, limits={"max_expansions": 10**6}))
    assert res.status == COMPLETED, res.error
    assert res.answer["total"] == (1 if injective else 0)
    if answer == "rows" and injective:
        assert list(res.answer["rows"][0]["links"]) == [e["id"] for e in edges]
    tree = execute(p, p.request("pattern", match={"var": "v0", "nodes": [nodes[0]]}, steps=steps[:n - 1],
                                answer=answer))                     # a tree, counted along its kept hops
    assert tree.status == COMPLETED and tree.answer["total"] == 1, tree.error
