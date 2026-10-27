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
    if "component_sets" in exp:                             # components: exact, or what an interrupted search finished
        got = [list(x["nodes"]) for x in answer.get("components") or []]
        if (got != exp["component_sets"]) if done else any(g not in exp["component_sets"] for g in got):
            problems.append(f"components {got}, expected {exp['component_sets']}")
    for key, field in (("component_edges", ("edges",)), ("component_flags", ("cyclic", "initial", "terminal"))):
        if key in exp and done:
            entries = {x["nodes"][0]: x for x in answer.get("components") or []}
            got = {n: (entries[n][field[0]] if len(field) == 1 else [entries[n].get(f) for f in field])
                   if n in entries else None for n in exp[key]}
            if got != exp[key]:
                problems.append(f"{key} {got}, expected {exp[key]}")
    if "articulation" in exp:                               # biconnectivity: exact, or some of them when interrupted
        got = {x["node"]: x["splits"] for x in answer.get("articulation_points") or []}
        if (got != exp["articulation"]) if done else any(exp["articulation"].get(n) != k for n, k in got.items()):
            problems.append(f"articulation points {got}, expected {exp['articulation']}")
    for key, field in (("bridge_edges", "bridges"), ("block_edges", "blocks")):
        if key in exp:
            got = [x["edge"] if key == "bridge_edges" else list(x["edges"]) for x in answer.get(field) or []]
            if (got != exp[key]) if done else any(g not in exp[key] for g in got):
                problems.append(f"{field} {got}, expected {exp[key]}")
    if "flow_value" in exp:                                 # a maximum flow: exact, or bracketed when interrupted
        want = Fraction(exp["flow_value"])
        got, bound = _cost(answer, "value"), _cost(answer, "upper_bound")
        if done and (got is None or Fraction(got) != want):
            problems.append(f"flow value {got}, expected {want}")
        if not done and not (got is not None and bound is not None and Fraction(got) <= want <= Fraction(bound)):
            problems.append(f"an interrupted flow's bounds [{got}, {bound}] miss the maximum {want}")
    if "cut_edges" in exp and done:
        got = [[h["edge"], h["direction"]] for h in answer.get("cut") or []]
        if got != exp["cut_edges"]:
            problems.append(f"cut {got}, expected the cut nearest the sources {exp['cut_edges']}")
    if "cut_node_ids" in exp and done and [x["node"] for x in answer.get("cut_nodes") or []] != exp["cut_node_ids"]:
        problems.append(f"cut nodes {answer.get('cut_nodes')}, expected {exp['cut_node_ids']}")
    for key, field in (("matching_size", "size"), ("left_count", "left_count"), ("deficiency", "deficiency")):
        if key in exp and done and answer.get(field) != exp[key]:
            problems.append(f"{field} {answer.get(field)!r}, expected {exp[key]}")
    if "separated" in exp and done and answer.get("separated") is not exp["separated"]:   # d-separation
        problems.append(f"separated {answer.get('separated')!r}, expected {exp['separated']!r}")
    if "trail_steps" in exp and done:
        got = [[s["edge"], s["travel"]] for s in answer.get("trail") or []]
        if got != exp["trail_steps"]:
            problems.append(f"trail {got}, expected the canonical {exp['trail_steps']}")
    if "collider_nodes" in exp and done:
        got = {c["node"]: [h["edge"] for h in c["to_given"]] for c in answer.get("colliders") or []}
        if got != exp["collider_nodes"]:
            problems.append(f"colliders {got}, expected {exp['collider_nodes']}")
    if "acyclic" in exp and done and answer.get("acyclic") is not exp["acyclic"]:     # critical path
        problems.append(f"acyclic {answer.get('acyclic')!r}, expected {exp['acyclic']!r}")
    if "makespan" in exp and done:
        got = _cost(answer, "cost")
        if got is None or Fraction(got) != Fraction(exp["makespan"]):
            problems.append(f"makespan {got}, expected {exp['makespan']}")
    if "critical_edges" in exp and done:
        got = [h["edge"] for h in (answer.get("path") or {}).get("hops") or []]
        if got != exp["critical_edges"]:
            problems.append(f"critical path {got}, expected the canonical {exp['critical_edges']}")
    if "schedule_times" in exp and done:
        got = {e["node"]: [_cost(e, "earliest"), _cost(e, "latest"), _cost(e, "slack")]
               for e in answer.get("schedule") or []}
        if got != exp["schedule_times"]:
            problems.append(f"schedule {got}, expected {exp['schedule_times']}")
    if "rank" in exp and answer.get("rank") != exp["rank"]:  # incidence: exact, null when interrupted
        problems.append(f"rank {answer.get('rank')!r}, expected {exp['rank']!r}")
    for key in ("left_kernel", "right_kernel"):
        if key in exp and done:
            got = [[[e["node"], e["value"]] for e in vec] for vec in answer.get(key) or []]
            if got != exp[key]:
                problems.append(f"{key} {got}, expected the canonical basis {exp[key]}")
    if "idoms" in exp and done:                             # dominators: each reached node's immediate dominator
        got = {e["node"]: e["idom"] for e in answer.get("tree") or []}
        if got != exp["idoms"]:
            problems.append(f"immediate dominators {got}, expected {exp['idoms']}")
    if "dominator_chains" in exp and done:
        got = {c["target"]: list(c["dominators"]) if c["reachable"] else None for c in answer.get("chains") or []}
        if got != exp["dominator_chains"]:
            problems.append(f"dominator chains {got}, expected {exp['dominator_chains']}")
    if "reached_count" in exp and done and answer.get("reached") != exp["reached_count"]:
        problems.append(f"reached {answer.get('reached')}, expected {exp['reached_count']}")
    if exp.get("claims_nothing") and any(v is not None for v in answer.values()):
        problems.append("an interrupted answer that cannot claim anything lists nothing")
    if "component_count" in exp and done and answer.get("components") != exp["component_count"]:
        problems.append(f"components {answer.get('components')}, expected {exp['component_count']}")
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
        low = _cost(answer, "lower_bound")                  # none with a negative cost: only the witness bounds it
        if not done and answer.get("found") and not ((low is None or low <= want) and want <= got):
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
    if "negative_cycle_edges" in exp:                       # the three walks of a negative cycle, canonical
        cycle = answer.get("negative_cycle") or {}
        got = {part: [h["edge"] for h in (cycle.get(part) or {}).get("hops") or []]
               for part in ("lead_in", "cycle", "lead_out")} if cycle else None
        if got != exp["negative_cycle_edges"]:
            problems.append(f"negative cycle {got}, expected the canonical {exp['negative_cycle_edges']}")
        elif "negative_cycle_cost" in exp and _cost(cycle["cycle"], "cost") != exp["negative_cycle_cost"]:
            problems.append(f"negative cycle cost {_cost(cycle['cycle'], 'cost')!r}, expected "
                            f"{exp['negative_cycle_cost']!r}")
    if "flow_amounts" in exp and done:                      # a minimum-cost flow: what each hop carries, its cost
        got = {f["edge"]: _cost(f, "flow") for f in answer.get("flows") or []}
        if got != exp["flow_amounts"] or (_cost(answer, "value"), _cost(answer, "cost"), answer.get("feasible")) != \
                (exp["flow_value"], exp["flow_cost"], exp["feasible"]):
            problems.append(f"flow {got}, value {answer.get('value')}, cost {answer.get('cost')}, feasible "
                            f"{answer.get('feasible')}; expected {exp['flow_amounts']}, {exp['flow_value']}, "
                            f"{exp['flow_cost']}, {exp['feasible']}")
    if "datalog_rows" in exp:                               # Datalog: the query's facts, ascending, up to max_results
        got = [list(r["values"]) for r in answer.get("rows") or []]
        want = exp["datalog_rows"]
        if (got != want) if (done or exp.get("interruption") == "output_limit") else any(r not in want for r in got):
            problems.append(f"rows {got}, expected {want}")
        if "model_facts" in exp and done and (answer.get("model") or {}).get("facts") != exp["model_facts"]:
            problems.append(f"the model has {(answer.get('model') or {}).get('facts')} facts, expected "
                            f"{exp['model_facts']}")
    if "pairs_reachable" in exp:                            # pairs: each decided; a prefix when interrupted
        got = {f"{x['source']}->{x['target']}": x["reachable"] for x in answer.get("pairs") or []}
        want = exp["pairs_reachable"]
        if any(want.get(k) is not v for k, v in got.items()) or (done and got != want) \
                or (done and answer.get("reachable") != sum(want.values())):
            problems.append(f"pairs {got}, expected {want}")
        for k, edges in (exp.get("pair_witness_edges") or {}).items():
            pair = next((x for x in answer.get("pairs") or [] if f"{x['source']}->{x['target']}" == k), None)
            if pair is not None and [h["edge"] for h in (pair["witness"] or {}).get("hops", [])] != edges:
                problems.append(f"the witness of {k} is not {edges}")
        if "closures" in exp and done:
            cert = answer.get("certificate") or {}
            got_closures = {c["source"]: list(c["nodes"]) for c in cert.get("closures") or []}
            if got_closures != exp["closures"]:
                problems.append(f"closures {got_closures}, expected {exp['closures']}")
    if "path_edges" in exp:                                 # the k shortest paths: exactly these, in this order
        got = [[h["edge"] for h in x["hops"]] for x in answer.get("paths") or []]
        want = exp["path_edges"] if done else exp["path_edges"][:len(got)]
        if got != want or (done and answer.get("exhausted") is not exp["exhausted"]):
            problems.append(f"paths {got} (exhausted {answer.get('exhausted')}), expected {want} "
                            f"(exhausted {exp['exhausted']})")
    if "matching_pairs" in exp and done:                    # a cheapest largest matching: its pairs and cost
        got = {x["left"]: x["edge"] for x in answer.get("pairs") or []}
        if got != exp["matching_pairs"] or _cost(answer, "cost") != exp["matching_cost"]:
            problems.append(f"pairs {got} costing {_cost(answer, 'cost')!r}, expected {exp['matching_pairs']} "
                            f"costing {exp['matching_cost']!r}")
    if "basis_edges" in exp:                                # a cycle basis: de Pina's, in its order
        got = [list(c["edges"]) for c in answer.get("cycles") or []]
        want = exp["basis_edges"] if done else exp["basis_edges"][:len(got)]
        if got != want or answer.get("rank") != exp["rank"]:
            problems.append(f"cycles {got} of rank {answer.get('rank')}, expected {want} of rank {exp['rank']}")
        if done and "basis_nodes" in exp and [list(c["nodes"]) for c in answer["cycles"]] != exp["basis_nodes"]:
            problems.append(f"cycle nodes {[c['nodes'] for c in answer['cycles']]}, expected {exp['basis_nodes']}")
        want_w = exp.get("basis_weight")
        if done and want_w is not None and _cost(answer, "weight") != (Fraction(want_w) if isinstance(want_w, str)
                                                                        else want_w):
            problems.append(f"basis weight {_cost(answer, 'weight')!r}, expected {want_w!r}")
    if "forest_edges" in exp and done:                      # a spanning forest: unique in its order
        if list(answer.get("edges") or []) != exp["forest_edges"] or answer.get("trees") != exp["trees"]:
            problems.append(f"forest {answer.get('edges')} in {answer.get('trees')} trees, expected "
                            f"{exp['forest_edges']} in {exp['trees']}")
        want = exp["forest_weight"]
        if _cost(answer, "weight") != (Fraction(want) if isinstance(want, str) else want):
            problems.append(f"forest weight {_cost(answer, 'weight')!r}, expected {want!r}")
    if "witness_edges" in exp and done:
        got_edges = [h["edge"] for h in (answer.get("witness") or {}).get("hops") or []]
        if got_edges != exp["witness_edges"]:
            problems.append(f"witness {got_edges}, expected the canonical witness {exp['witness_edges']}")
    return problems
