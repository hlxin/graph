"""SCALE's adapter to GRAPH: scale-catal documents as GRAPH projections, and one read-only workflow on them.

This adapter belongs to SCALE, not to GRAPH: it reads SCALE's vocabulary (labels and what they are, edge types and
what specializes them, evidence) so that the GRAPH runtime never has to. It is staged in the GRAPH repository
until SCALE takes it in under its own review (docs/SCALE_INTEGRATION.md). It reads SCALE documents and schemas and
changes nothing.

The workflow (a bounded evidence traversal): a competency question's chain of edge steps (match a label, then follow
one edge type per step, in its direction, to a label) runs as a GRAPH typed path from each matched record. Each
record gets a witness (the original link IDs, in step order) or a complete negative, and the evidence those links
cite. SCALE keeps everything else a plan does: filters on values, units and contexts, retraction, date and
own-work rules, returns, access, and its scientific answer statuses.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from graph_runtime import Projection, Result, execute

DIRECTION = {"out": "forward", "in": "reverse"}
STEP_KEYS = ("from", "edge", "direction", "to", "as", "optional", "edge_as")   # what a structural step keeps


class Unsupported(ValueError):
    """A plan whose shape a typed path does not express (a branching, optional, entries or field step)."""


def label_closure(spec: Mapping, label: str) -> list[str]:
    """A label and every label it is_a (the schema's ancestors), as SCALE's query engine matches ``to``."""
    return [label] + list((spec["nodes"].get(label) or {}).get("ancestors") or [])


def edge_closure(spec: Mapping, edge: str) -> list[str]:
    """An edge type and every edge type that specializes it, at any depth, as a SCALE step follows it."""
    out, grew = {edge}, True
    while grew:
        grew = False
        for name, d in (spec.get("edges") or {}).items():
            if name not in out and (d or {}).get("specializes") in out:
                out.add(name)
                grew = True
    return sorted(out)


_SPEC_DIGESTS: dict[int, tuple[Mapping, str]] = {}


def spec_digest(spec: Mapping) -> str:
    """SCALE's spec digest of a schema object, computed once per object: SCALE's loaders hand out one object per
    schema version, and treat it as unchanging, as its query engine's reuse of a ``Graph`` does."""
    from scale import pgraph
    hit = _SPEC_DIGESTS.get(id(spec))
    if hit is None or hit[0] is not spec:
        hit = _SPEC_DIGESTS[id(spec)] = (spec, pgraph.spec_sha256(spec))
    return hit[1]


def bindings_of(doc: Mapping, spec: Mapping, as_of: str | None = None) -> dict[str, str]:
    """What a projection of ``doc`` is built from: the document (its digest, as a query result names it), the schema
    (its version and spec digest) and the policy scope (the document's access level on ``as_of``, default today)."""
    from scale import passages, pgraph, pgraph_query
    day = pgraph_query.embargo_day(as_of, pgraph.embargo_today())
    graph = doc.get("graph") or {}
    return {"source": f"{graph.get('graph_id')} sha256:{passages.graph_sha256(doc)}",
            "schema": f"scale-catal {spec.get('version')} sha256:{spec_digest(spec)}",
            "policy": pgraph_query.document_access(doc, day)["level"]}


def projection_from_document(doc: Mapping, spec: Mapping, *, bindings: Mapping[str, str] | None = None) -> Projection:
    """Every node (its types: its label and the labels it is_a) and every link (its relation: its type). The
    declared vocabularies are the schema's, so that a plan naming a declared edge type no link uses gets a complete,
    empty answer, as SCALE answers no_matching_record."""
    nodes = [{"id": n["id"], "types": label_closure(spec, n["label"])} for n in doc["nodes"]]
    edges = [{"id": e["id"], "source": e["source"], "target": e["target"], "relation": e["type"]} for e in doc["links"]]
    return Projection.build(nodes=nodes, edges=edges,
                            relations=set(spec["edges"]) | {e["relation"] for e in edges},
                            node_types=set(spec["nodes"]) | {t for n in nodes for t in n["types"]},
                            bindings=bindings_of(doc, spec) if bindings is None else bindings)


def chain(plan: Mapping, spec: Mapping) -> list[dict]:
    """The typed-path pattern of a plan whose steps are edge steps in one chain from its match: position i follows
    step i's edge type and its specializations, forward for ``out`` and reverse for ``in``, to step i's ``to``
    label (and the labels that are one). Raises :class:`Unsupported` for any other shape."""
    root = (plan.get("match") or {}).get("as", "self")
    prev, pattern = root, []
    for i, st in enumerate(plan.get("steps") or []):
        if "edge" not in st:
            raise Unsupported(f"step {i} binds value entries or follows a field")
        if st.get("optional"):
            raise Unsupported(f"step {i} is optional")
        if st.get("from", root) != prev:
            raise Unsupported(f"step {i} starts from {st.get('from', root)!r}, not the previous step: the steps branch")
        pattern.append({"scope": [{"relation": t, "direction": DIRECTION[st.get("direction", "out")]}
                                  for t in edge_closure(spec, st["edge"])],
                        "node_types": [st["to"]] if st.get("to") else None})
        prev = st["as"]
    if not pattern:
        raise Unsupported("the plan has no steps")
    return pattern


def pattern_request(plan: Mapping, spec: Mapping) -> dict:
    """A plan whose steps are all edge steps, of any tree shape and with optional steps, as a GRAPH pattern request:
    the match's label (or id), each step's edge type and its specializations in its direction, its ``to`` label, its
    ``edge_as`` and ``optional``. Filters are SCALE's to apply (as node or edge sets, or on the rows)."""
    m = plan.get("match") or {}
    root = m.get("as", "self")
    steps = []
    for i, st in enumerate(plan.get("steps") or []):
        if "edge" not in st:
            raise Unsupported(f"step {i} binds value entries or follows a field")
        steps.append({"from": st.get("from", root), "var": st["as"],
                      "scope": [{"relation": t, "direction": DIRECTION[st.get("direction", "out")]}
                                for t in edge_closure(spec, st["edge"])],
                      "node_types": [st["to"]] if st.get("to") else None, "edge_var": st.get("edge_as"),
                      "optional": bool(st.get("optional"))})
    return {"operation": "pattern", "steps": steps,
            "match": {"var": root, "node_types": [m["label"]] if m.get("label") else None,
                      "nodes": [m["id"]] if m.get("id") else None}}


def matched(doc: Mapping, spec: Mapping, plan: Mapping) -> list[str]:
    """The records a plan's match names, in SCALE's order: its id, or every node of its label (or of a label that
    is one), by ascending ID."""
    m = plan.get("match") or {}
    if m.get("id"):
        return [m["id"]] if any(n["id"] == m["id"] for n in doc["nodes"]) else []
    return sorted(n["id"] for n in doc["nodes"] if m.get("label") in label_closure(spec, n["label"]))


def structural(plan: Mapping) -> dict:
    """The plan with its value filters, text search, date, retraction and own-work rules left out: what SCALE's engine
    answers for the pattern alone, every row with its evidence."""
    m = {k: v for k, v in (plan.get("match") or {}).items() if k in ("label", "id", "as")}
    steps = [{k: v for k, v in st.items() if k in STEP_KEYS} for st in plan.get("steps") or []]
    return {"match": m, "steps": steps, "include_retracted": True, "evidence": True, "limit": 10**9}


def evidence_of(doc: Mapping, link_ids: list[str]) -> list[dict]:
    """The evidence the links cite, in link order: ``{record, evidence (its position), source, locator, quote}``."""
    links = {e["id"]: e for e in doc["links"]}
    out = []
    for lid in link_ids:
        for i, ev in enumerate((links[lid].get("props") or {}).get("evidence") or []):
            out.append({"record": lid, "evidence": i, "source": ev.get("source"), "locator": ev.get("locator"),
                        "quote": ev.get("quote")})
    return out


def trace(doc: Mapping, spec: Mapping, plan: Mapping, projection: Projection | None = None,
          **limits: int) -> dict:
    """Run a plan's chain as a GRAPH typed path from each matched record: per record, the result's status, whether a
    witness was found (True, False only when complete, None when unknown), the witness's links and their evidence,
    and the result's id (its provenance)."""
    pattern = chain(plan, spec)
    # a projection handed in may describe another state of the document: its bindings are checked against the
    # document's current ones; one built here is current by construction
    p = projection if projection is not None else projection_from_document(doc, spec)
    current = bindings_of(doc, spec) if projection is not None else None
    rows = []
    for rid in matched(doc, spec, plan):
        res: Result = execute(p, p.request("typed_path", sources=[rid], pattern=pattern,
                                           **({"limits": limits} if limits else {})), current_bindings=current)
        links = [h["edge"] for h in res.witness or []]
        rows.append({"record": rid, "status": res.status, "found": res.found, "links": links,
                     "evidence": evidence_of(doc, links), "result_id": res.result_id,
                     **({"error": dict(res.error)} if res.error else {})})
    return {"projection": p.identity, "bindings": dict(p.bindings), "pattern": json.loads(json.dumps(pattern)),
            "records": rows}


# ------------------------------------------------------------------------------------- what rests on retractions
# The dependency projection of SCALE's source lifecycle (scale.lifecycle.resting): a record depends on each evidence
# item it holds, an item on the source it cites (or, for a partial retraction recorded as a dataset, on that
# dataset's retraction), a record on its inclusion in a retracted dataset whose sources its evidence does not cite,
# and a record on the records it uses (HAS_INPUT and its specializations) and the experiments its values name
# (produced_by). Which evidence a retracted dataset withdraws is SCALE's rule (lifecycle.retracted_by,
# unsourced_inclusions); GRAPH only follows the dependencies, from what was retracted to what depends on it.
HAS_EVIDENCE, CITES, RETRACTED_WITH, INCLUDED, USES = ("HAS_EVIDENCE", "CITES_SOURCE", "WITHDRAWN_WITH",
                                                       "RESTS_ON_INCLUSION", "USES_RECORD")
DEPENDENCIES = (HAS_EVIDENCE, CITES, RETRACTED_WITH, INCLUDED, USES)
RESTING_DEPTH = 3     # retraction -> evidence item or inclusion -> the record -> a record that uses it: one hop further


def _kind(spec: Mapping, label: str) -> str:
    from scale import lifecycle
    return next((a for a in label_closure(spec, label) if a in lifecycle.UNFLAGGED), label)


def _named_producers(obj: Any) -> set[str]:
    """The experiments a props tree's value objects name by produced_by (evidence left out)."""
    out: set[str] = set()
    if isinstance(obj, dict):
        if isinstance(obj.get("produced_by"), str) and "status" in obj:
            out.add(obj["produced_by"])
        for k, v in obj.items():
            if k != "evidence":
                out |= _named_producers(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _named_producers(v)
    return out


def retraction_dependencies(doc: Mapping, spec: Mapping) -> dict:
    """The dependency projection, what was retracted (``changed``), and the records SCALE flags when they rest on a
    retraction (``flaggable``: not a species, person, source, dataset or other record that cites without resting on
    a finding, and not a notice, byline or REPORTED_IN link)."""
    from scale import lifecycle
    st = lifecycle.statuses(doc, spec)
    incl = lifecycle.retracted_inclusions(doc, st, spec)
    kind = {n["id"]: _kind(spec, n["label"]) for n in doc["nodes"]}
    records = [(n["id"], n.get("props") or {}, kind[n["id"]] in lifecycle.UNFLAGGED) for n in doc["nodes"]]
    records += [(e["id"], e.get("props") or {},
                 e["type"] in lifecycle.LIFECYCLE_EDGES or e["type"] == "REPORTED_IN"
                 or (kind.get(e["source"]) in lifecycle.UNFLAGGED and kind.get(e["target"]) in lifecycle.UNFLAGGED))
                for e in doc["links"] if isinstance(e.get("id"), str)]
    ids = [rid for rid, _, _ in records]
    if len(set(ids)) != len(ids):
        raise Unsupported("a node and a link share an ID")
    nodes = {rid: {"id": rid, "types": ["Record"]} for rid in ids}
    edges: list[dict] = []
    changed = {s for s, r in st.items() if r["status"] in lifecycle.RETRACTED and s in kind}

    def retraction_of(d: str) -> str:
        node = f"retraction:{d}"
        nodes.setdefault(node, {"id": node, "types": ["Retraction"]})
        changed.add(node)
        return node

    for rid, props, _ in records:
        cited = set()
        for i, ev in enumerate(props.get("evidence") or []):
            if not isinstance(ev, dict) or not isinstance(ev.get("source"), str):
                continue
            item, src = f"evidence:{rid}#{i}", ev["source"]
            cited.add(src)
            nodes[item] = {"id": item, "types": ["EvidenceItem"]}
            edges.append({"id": f"has:{rid}#{i}", "source": rid, "target": item, "relation": HAS_EVIDENCE})
            if src in kind:
                edges.append({"id": f"cites:{rid}#{i}", "source": item, "target": src, "relation": CITES})
            if lifecycle.status_of(st, src) not in lifecycle.RETRACTED:
                for d in lifecycle.retracted_by(st, incl, rid, src):
                    edges.append({"id": f"withdrawn:{rid}#{i}@{d}", "source": item, "target": retraction_of(d),
                                  "relation": RETRACTED_WITH})
        for d in lifecycle.unsourced_inclusions(incl, rid, cited):
            atom = f"inclusion:{rid}@{d}"
            nodes[atom] = {"id": atom, "types": ["Inclusion"]}
            edges.append({"id": f"rests:{rid}@{d}", "source": rid, "target": atom, "relation": INCLUDED})
            edges.append({"id": f"included:{rid}@{d}", "source": atom, "target": retraction_of(d),
                          "relation": RETRACTED_WITH})
    inputs = lifecycle.input_edges(spec)
    for e in doc["links"]:            # a link to a record of another part (graphs/ parts) is not followed, as SCALE
        if e["type"] in inputs and e["source"] in kind and e["target"] in kind and e["source"] != e["target"] \
                and kind[e["target"]] not in lifecycle.UNFLAGGED:
            edges.append({"id": f"uses:{e['id']}", "source": e["source"], "target": e["target"], "relation": USES})
    for rid, props, _ in records:
        for other in sorted(_named_producers(props)):
            if other in kind and other != rid:
                edges.append({"id": f"produced_by:{rid}->{other}", "source": rid, "target": other, "relation": USES})
    unique = {e["id"]: e for e in edges}                    # one HAS_INPUT link and a produced_by may say the same
    p = Projection.build(nodes=list(nodes.values()), edges=list(unique.values()), relations=DEPENDENCIES,
                         node_types=["Record", "EvidenceItem", "Inclusion", "Retraction"],
                         bindings={**bindings_of(doc, spec), "projection": "scale.lifecycle dependencies v1"})
    return {"projection": p, "changed": sorted(changed),
            "flaggable": sorted(rid for rid, _, skip in records if not skip)}


def resting_records(doc: Mapping, spec: Mapping) -> dict:
    """The records that rest on a retraction, as GRAPH finds them: dependency impact from everything retracted,
    within one hop further than a record's own evidence, each record with its witness and, by one impact per
    retraction, the retracted sources and datasets it rests on. Whether it rests on them only or in part, and what
    that means for an answer, stays with SCALE."""
    dep = retraction_dependencies(doc, spec)
    p, flaggable = dep["projection"], set(dep["flaggable"])
    scope = [{"relation": r, "direction": "reverse"} for r in DEPENDENCIES]
    out: dict[str, dict] = {}
    if not dep["changed"]:
        return {"projection": p.identity, "records": out, "results": []}
    every = execute(p, p.request("dependency_impact", changed=dep["changed"], scope=scope, max_depth=RESTING_DEPTH))
    results = [every.result_id]
    for e in every.answer["affected"]:
        if e["node"] in flaggable:
            out[e["node"]] = {"depth": e["depth"], "witness": every.path_to(e["node"]), "sources": [], "datasets": []}
    for seed in dep["changed"]:
        one = execute(p, p.request("dependency_impact", changed=[seed], scope=scope, max_depth=RESTING_DEPTH))
        results.append(one.result_id)
        for e in one.answer["affected"]:
            if e["node"] in out:
                if seed.startswith("retraction:"):
                    out[e["node"]]["datasets"].append(seed.removeprefix("retraction:"))
                else:
                    out[e["node"]]["sources"].append(seed)
    return {"projection": p.identity, "complete": every.complete, "records": out, "results": results}
