"""Backend conformance: language-neutral cases (``cases.json``) that every GRAPH backend must pass.

Each case names a graph, a request, an execution context and what the answer must be, written by hand from the
graph. A completed answer must match exactly; ``witness_edges`` checks the canonical choice, including ties.
An interrupted answer may list only what the full answer
holds, at the same depths: what it lists is never wrong, and nothing it leaves out is denied. Every witness is
checked against the graph's records with :func:`graph_runtime.verify_result`.

Run it on another backend by passing that backend's ``execute(projection, request, **context)``:
``run_conformance(my_execute)``; the context keys it may receive are ``timeout``, ``cancel`` and
``current_bindings``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from fractions import Fraction
from importlib import resources
from typing import Any

from ..projection import CONTRACT, Projection
from ..request import normalize_request
from ..errors import RequestError
from ..verify import verify_result


def load_cases() -> dict:
    return json.loads(resources.files(__name__).joinpath("cases.json").read_text(encoding="utf-8"))


def run_conformance(execute: Callable[..., Any] | None = None) -> dict:
    """Run every case; ``{"cases": n, "passed": n, "failures": [{"case", "problems"}]}``."""
    if execute is None:
        from ..engine import execute
    suite = load_cases()
    if suite["contract"] != CONTRACT:
        raise ValueError("conformance cases must name the runtime contract")
    failures = []
    for case in suite["cases"]:
        graph = suite["graphs"][case["graph"]]
        p = _projection(graph)
        ctx = case.get("context") or {}
        request = {**case["request"], "projection": ctx.get("request_projection", p.identity)}
        kwargs: dict[str, Any] = {}
        if "timeout" in ctx:
            kwargs["timeout"] = ctx["timeout"]
        if ctx.get("cancelled"):
            kwargs["cancel"] = lambda: True
        if "current_bindings" in ctx:
            kwargs["current_bindings"] = ctx["current_bindings"]
        try:
            res = execute(p, request, **kwargs)
            data = res.to_dict() if hasattr(res, "to_dict") else dict(res)
            problems = check_case(case, data, graph)
            if not problems and "timeout" not in kwargs and "cancel" not in kwargs:
                again = execute(p, request, **kwargs)
                repeated = again.to_dict() if hasattr(again, "to_dict") else dict(again)
                problems.extend(check_case(case, repeated, graph))
                if data.get("result_id") != repeated.get("result_id"):
                    problems.append("repeating the same projection and request changed result_id")
        except Exception as exc:  # noqa: BLE001 - a backend that raises fails the case
            problems = [f"raised {type(exc).__name__}: {exc}"]
        if problems:
            failures.append({"case": case["name"], "problems": problems})
    return {"cases": len(suite["cases"]), "passed": len(suite["cases"]) - len(failures), "failures": failures}


def _projection(graph: Mapping) -> Any:
    """A case's projection: a periodic projection's cell when the graph states its dimensions."""
    if "dimensions" in graph:
        from ..periodic import PeriodicProjection
        return PeriodicProjection.build(**graph)
    return Projection.build(**graph)


def _cost(answer: Mapping, field: str) -> Any:
    exact = answer.get(f"{field}_exact")
    return Fraction(exact) if exact is not None else answer.get(field)


def check_case(case: Mapping, data: Mapping, graph: Mapping) -> list[str]:
    """Problems of one result against its case."""
    exp = case["expect"]
    statuses = exp["status"] if isinstance(exp["status"], list) else [exp["status"]]
    status = data.get("status")
    if status not in statuses:
        return [f"status {status!r} ({(data.get('error') or {}).get('code')}), expected {statuses}"]
    problems = list(verify_result(data, nodes=graph["nodes"], edges=graph["edges"],
                                  type_parents=graph.get("type_parents"),
                                  relation_parents=graph.get("relation_parents"), dimensions=graph.get("dimensions")))
    p = _projection(graph)
    ctx = case.get("context") or {}
    raw = {**case["request"], "projection": ctx.get("request_projection", p.identity)}
    if data.get("projection") != p.identity:
        problems.append("result is not bound to the case's projection")
    if data.get("operation") != raw.get("operation"):
        problems.append("result does not name the case's operation")
    try:
        request = normalize_request(raw)
    except RequestError:
        if data.get("request") is not None:
            problems.append("an unnormalizable request must not be echoed as normalized")
    else:
        if data.get("request") != request.to_dict() or data.get("request_id") != request.request_id:
            problems.append("result is not bound to the case's normalized request")
    if problems:
        return problems
    if "error_code" in exp and (data.get("error") or {}).get("code") != exp["error_code"]:
        problems.append(f"error {(data.get('error') or {}).get('code')!r}, expected {exp['error_code']!r}")
    if status == "interrupted" and "interruption" in exp and data["interruption"]["reason"] != exp["interruption"]:
        problems.append(f"interrupted by {data['interruption']['reason']!r}, expected {exp['interruption']!r}")
    answer = data.get("answer") or {}
    done = status == "completed"
    for key in ("reached", "affected"):
        if key in exp:
            got = {e["node"]: e["depth"] for e in answer.get(key) or []}
            if done and got != exp[key]:
                problems.append(f"{key} {got}, expected {exp[key]}")
            if not done and any(exp[key].get(n) != d for n, d in got.items()):
                problems.append(f"{key} {got} lists what the full answer {exp[key]} does not hold")
    if "path_reached" in exp:                               # a regular path's reached nodes and fewest hops
        got = {e["node"]: e["length"] for e in answer.get("reached") or []}
        if done and got != exp["path_reached"]:
            problems.append(f"reached {got}, expected {exp['path_reached']}")
        if not done and any(exp["path_reached"].get(n) != d for n, d in got.items()):
            problems.append(f"reached {got} lists what the full answer {exp['path_reached']} does not hold")
    if "derived" in exp:                                    # a derivation's derived targets and best values
        got = {d["node"]: _cost(d, "value") for d in answer.get("derived") or []}
        want = {n: Fraction(v) if isinstance(v, str) else v for n, v in exp["derived"].items()}
        if done and got != want:
            problems.append(f"derived {got}, expected {want}")
        if not done and any(n not in want or want[n] != v for n, v in got.items()):
            problems.append(f"derived {got} lists what the full answer {want} does not hold")
    if "underived" in exp and done and list(answer.get("underived") or []) != exp["underived"]:
        problems.append(f"underived {answer.get('underived')}, expected {exp['underived']}")
    if "derivation_rules" in exp and done:
        got = {e["node"]: [e["rule"], [h["edge"] for h in e["uses"]]] for e in answer.get("derivation") or []}
        if got != exp["derivation_rules"]:
            problems.append(f"derivation {got}, expected the canonical {exp['derivation_rules']}")
    if "via_edges" in exp and done:                         # the canonical witness of chosen nodes
        entries = {e["node"]: e for e in answer.get("reached") or answer.get("affected") or []}
        got = {n: (entries.get(n) or {}).get("via", {}) and entries[n]["via"]["edge"] for n in exp["via_edges"]}
        if got != exp["via_edges"]:
            problems.append(f"witness edges {got}, expected the canonical {exp['via_edges']}")
    if "components" in exp and done:                       # periodicity: seeds, rank, basis and copies
        got = [[list(x["seeds"]), x["rank"], [list(r) for r in x["basis"]], x["copies"]]
               for x in answer.get("components") or []]
        if got != exp["components"]:
            problems.append(f"components {got}, expected {exp['components']}")
    if "component_nodes" in exp and done:
        got = [{n["node"]: list(n["image"]) for n in x["nodes"]} for x in answer.get("components") or []]
        if got != exp["component_nodes"]:
            problems.append(f"component nodes {got}, expected {exp['component_nodes']}")
    if "pagerank" in exp:                                   # each listed interval holds the exact PageRank
        got = {e["node"]: (Fraction(e["lower_exact"]), Fraction(e["upper_exact"])) for e in answer.get("ranked") or []}
        if set(got) != set(exp["pagerank"]) or any(not got[n][0] <= Fraction(v) <= got[n][1]
                                                   for n, v in exp["pagerank"].items()):
            problems.append(f"ranked intervals {got} do not hold the PageRanks {exp['pagerank']}")
    if "certified_top" in exp and answer.get("certified_top") != exp["certified_top"]:
        problems.append(f"certified_top {answer.get('certified_top')}, expected {exp['certified_top']}")
    if "count" in exp and answer.get("count") != exp["count"]:
        problems.append(f"count {answer.get('count')}, expected {exp['count']}")
    if "discovered" in exp and data["stats"]["discovered"] != exp["discovered"]:
        problems.append(f"discovered {data['stats']['discovered']}, expected {exp['discovered']}")
    if "found" in exp:
        found = answer.get("found")
        if done and found is not exp["found"]:
            problems.append(f"found {found!r}, expected {exp['found']!r}")
        if not done and found not in ((None, True) if exp["found"] else (None,)):
            problems.append(f"an interrupted answer says found {found!r}")
    if "cost" in exp or "cost_exact" in exp:
        want = exp["cost"] if "cost" in exp else Fraction(exp["cost_exact"])
        got = _cost(answer, "cost")
        if done and (got != want or ("cost" in exp and type(got) is not type(want))):
            problems.append(f"cost {got!r}, expected {want!r}")
        if not done and answer.get("found") and not (_cost(answer, "lower_bound") <= want <= got):
            problems.append(f"an interrupted answer's bounds [{answer.get('lower_bound')}, {got}] miss the cost {want}")
    if "total" in exp and answer.get("total") != exp["total"]:
        problems.append(f"total {answer.get('total')!r}, expected {exp['total']}")
    if "exists" in exp and answer.get("exists") is not exp["exists"]:
        problems.append(f"exists {answer.get('exists')!r}, expected {exp['exists']!r}")
    if "rows" in exp:                                       # the canonical rows; an interrupted answer lists a prefix
        got_rows = [[row["nodes"], row["links"]] for row in answer.get("rows") or []]
        want_rows = exp["rows"] if done else exp["rows"][:len(got_rows)]
        if got_rows != want_rows:
            problems.append(f"rows {got_rows}, expected {want_rows}")
    if "counts" in exp and answer.get("counts") != exp["counts"]:
        problems.append(f"counts {answer.get('counts')!r}, expected {exp['counts']}")
    if exp.get("unknown") and any(answer.get(k) is not None for k in ("lost", "weakened", "counts")):
        problems.append("an answer interrupted before its end lists no lost or weakened node and no counts")
    for key in ("lost", "weakened"):                        # each node's supports, failing hops and causes
        if key in exp:
            got = {e["node"]: {"supports": e["supports"], "standing": e["standing"],
                               "failing": [h["edge"] for h in e["failing"]], "causes": e["causes"]}
                   for e in answer.get(key) or []}
            want = exp[key] if done else {n: exp[key][n] for n in sorted(exp[key])[:len(got)]}
            if got != want:
                problems.append(f"{key} {got}, expected {want}")
    for key in ("value", "bound"):                          # a bottleneck's value, an interrupted one's bound
        if key in exp and (answer.get(key) != exp[key] or type(answer.get(key)) is not type(exp[key])):
            problems.append(f"{key} {answer.get(key)!r}, expected {exp[key]!r}")
    if "certificate" in exp and answer.get("certificate") != exp["certificate"]:
        problems.append(f"certificate {answer.get('certificate')!r}, expected {exp['certificate']!r}")
    if "witness_edges" in exp and done:
        got_edges = [h["edge"] for h in (answer.get("witness") or {}).get("hops") or []]
        if got_edges != exp["witness_edges"]:
            problems.append(f"witness {got_edges}, expected the canonical witness {exp['witness_edges']}")
    return problems
