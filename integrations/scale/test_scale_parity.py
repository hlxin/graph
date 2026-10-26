"""Parity with SCALE's own query engine, on SCALE's competency questions and public example documents.

For every plan whose steps are one chain of edge steps, SCALE runs its structural form (``adapter.structural``: the
pattern alone, every row with its evidence) and GRAPH runs the chain as a typed path from each matched record. They
must agree: GRAPH finds a witness for a record exactly when SCALE returns a row for it, the witness's links are the
links of one of SCALE's rows, in step order, and the evidence the adapter maps to those links is the link evidence
SCALE's row carries. Everything is read; nothing in SCALE is written.
"""

from __future__ import annotations

import copy
from collections import Counter

import pytest

import scale_graph_adapter as adapter
from graph_runtime import COMPLETED, STALE, execute, verify_result


@pytest.fixture(scope="session")
def scale_env(scale_repo):
    from scale import layout, pgraph, pgraph_query
    from scale.util import load_doc, load_yaml
    base = layout.domain_layers(scale_repo)["catalysis"]
    manifest = layout.graph_manifests(scale_repo, experimental=True)["catalysis"]
    specs: dict = {}

    def document(rel):
        doc = load_doc(base / rel)
        path = pgraph.pinned(manifest, (doc.get("graph") or {}).get("schema_version"), doc)
        if str(path) not in specs:
            specs[str(path)] = pgraph.load_spec(path)
        return doc, specs[str(path)]

    return {"repo": scale_repo, "base": base, "document": document, "load_yaml": load_yaml, "run": pgraph_query.run}


def _ev(items, links):
    keep = set(links)
    return sorted((x["record"], x["evidence"], x.get("source"), x.get("locator"), x.get("quote"))
                  for x in items if x["record"] in keep)


def check_parity(env, doc, spec, plan, p, tally):
    """One plan on one document: GRAPH's typed paths against SCALE's rows."""
    root = (plan.get("match") or {}).get("as", "self")
    answer = env["run"](doc, spec, adapter.structural(plan))
    traced = adapter.trace(doc, spec, plan, p)
    if answer["status"] == "schema_gap":     # SCALE: the schema cannot express it; GRAPH: it names nothing declared
        assert all(r["status"] == "failed" and r["error"]["code"] in ("unknown_relation", "unknown_node_type")
                   for r in traced["records"]), answer["schema_gaps"]
        tally["schema_gap"] += 1
        return
    rows: dict = {}
    for row in answer["rows"]:
        rows.setdefault(row["bound"][root], {})[tuple(row["links"])] = _ev(row["evidence"], row["links"])
    assert set(rows) <= set(adapter.matched(doc, spec, plan))
    for r in traced["records"]:
        assert r["status"] == COMPLETED, r
        assert r["found"] is (r["record"] in rows), (plan, r)
        if r["found"]:
            links = tuple(r["links"])
            assert links in rows[r["record"]], (plan, r["record"], links, list(rows[r["record"]]))
            assert _ev(r["evidence"], links) == rows[r["record"]][links]
            tally["witnesses"] += 1
            tally["evidence_items"] += len(r["evidence"])
        else:
            tally["negatives"] += 1


def test_competency_question_chains_match_scale(scale_env):
    tally: Counter = Counter()
    for path in sorted((scale_env["repo"] / "domain" / "competency").glob("*.yaml")):
        q = scale_env["load_yaml"](path) or {}
        if not q.get("plan"):
            continue
        for rel in q.get("documents") or []:
            doc, spec = scale_env["document"](rel)
            try:
                adapter.chain(q["plan"], spec)
            except adapter.Unsupported as why:
                tally[f"unsupported: {str(why).split(':')[0]}"] += 1
                break
            check_parity(scale_env, doc, spec, q["plan"], adapter.projection_from_document(doc, spec), tally)
            tally["question_documents"] += 1
        else:
            if q.get("plan") and q.get("documents"):
                tally["questions"] += 1
    print(dict(tally))
    assert tally["questions"] >= 12 and tally["witnesses"] >= 20 and tally["evidence_items"] > 0


def generated_plans(doc, spec, limit=60):
    """One- and two-step chains over the link types a document uses, in both directions and through the edge type
    each one specializes, matched by the labels at their ends: SCALE's engine and GRAPH must agree on all of them."""
    label = {n["id"]: n["label"] for n in doc["nodes"]}
    seen, plans = set(), []
    out_of: dict = {}
    for e in doc["links"]:
        out_of.setdefault(label[e["source"]], set()).add((e["type"], label[e["target"]]))
    for e in sorted(doc["links"], key=lambda e: e["id"]):
        parent = (spec["edges"].get(e["type"]) or {}).get("specializes")
        for edge in [e["type"]] + ([parent] if parent else []):
            for direction, start, end in (("out", e["source"], e["target"]), ("in", e["target"], e["source"])):
                key = (edge, direction, label[start], label[end])
                if key in seen:
                    continue
                seen.add(key)
                step = {"from": "x", "edge": edge, "direction": direction, "to": label[end], "as": "y"}
                plans.append({"match": {"label": label[start], "as": "x"}, "steps": [step]})
                if direction == "out":
                    for edge2, to2 in sorted(out_of.get(label[end], ())):
                        plans.append({"match": {"label": label[start], "as": "x"},
                                      "steps": [step, {"from": "y", "edge": edge2, "to": to2, "as": "z"}]})
    return plans[:limit]


def test_generated_chains_on_every_public_example_match_scale(scale_env):
    from scale.util import load_yaml
    from scale import layout
    m = load_yaml(scale_env["base"] / layout.DOMAIN_MANIFEST)
    globs = (m.get("example_documents") or {}).get("graph") or []
    rels = sorted({p.relative_to(scale_env["base"]).as_posix() for g in globs for p in scale_env["base"].glob(g)})
    tally: Counter = Counter()
    for rel in rels:
        doc, spec = scale_env["document"](rel)
        if ((doc.get("graph") or {}).get("stewardship") or {}).get("access") != "public":
            continue
        p = adapter.projection_from_document(doc, spec)
        for plan in generated_plans(doc, spec):
            check_parity(scale_env, doc, spec, plan, p, tally)
            tally["plans"] += 1
        tally["documents"] += 1
    print(dict(tally))
    assert tally["documents"] >= 10 and tally["plans"] >= 200 and tally["witnesses"] >= 200


def test_witnesses_check_against_the_document_records(scale_env):
    doc, spec = scale_env["document"]("examples/scale-catal/example.json")
    p = adapter.projection_from_document(doc, spec)
    nodes = [{"id": n["id"], "types": adapter.label_closure(spec, n["label"])} for n in doc["nodes"]]
    edges = [{"id": e["id"], "source": e["source"], "target": e["target"], "relation": e["type"]} for e in doc["links"]]
    checked = 0
    for plan in generated_plans(doc, spec):
        for rid in adapter.matched(doc, spec, plan):
            res = execute(p, p.request("typed_path", sources=[rid], pattern=adapter.chain(plan, spec)))
            assert verify_result(res, nodes=nodes, edges=edges) == []
            checked += 1
    assert checked > 20


def test_a_changed_document_makes_its_projection_stale(scale_env):
    doc, spec = scale_env["document"]("examples/scale-catal/example.json")
    p = adapter.projection_from_document(doc, spec)
    assert set(p.bindings) == {"source", "schema", "policy"} and p.bindings["policy"] == "public"
    assert p.bindings["schema"].startswith(f"scale-catal {spec['version']} sha256:")
    edited = copy.deepcopy(doc)
    edited["links"][0]["props"].setdefault("evidence", []).append(
        {"source": edited["links"][0]["props"]["evidence"][0]["source"] if edited["links"][0]["props"].get("evidence")
         else "ex:src", "locator": "p. 1", "quote": "added"})
    now = adapter.bindings_of(edited, spec)
    assert now["source"] != p.bindings["source"]
    req = p.request("reachability", sources=[doc["nodes"][0]["id"]], scope=[{"relation": doc["links"][0]["type"],
                                                                            "direction": "forward"}])
    assert execute(p, req).status == COMPLETED
    res = execute(p, req, current_bindings=now)
    assert (res.status, res.error["code"]) == (STALE, "bindings_changed")
    assert adapter.bindings_of(doc, spec) == dict(p.bindings)      # the document itself was not changed


@pytest.mark.parametrize("binding", ["source", "schema", "policy"])
def test_trace_checks_reused_projection_bindings(scale_env, binding):
    doc, spec = scale_env["document"]("examples/scale-catal/example.json")
    bindings = adapter.bindings_of(doc, spec)
    old = adapter.projection_from_document(doc, spec, bindings={**bindings, binding: "an older binding"})
    plan = generated_plans(doc, spec)[0]
    rows = adapter.trace(doc, spec, plan, old)["records"]
    assert rows
    assert all(r["status"] == STALE and r["error"]["code"] == "bindings_changed" and r["found"] is None
               and not r["links"] and not r["evidence"] for r in rows)


# ------------------------------------------------------------------------------------- what rests on retractions
def compare_resting(doc, spec, tally):
    """GRAPH's dependency impact against scale.lifecycle.resting on one document: the same records, each resting on
    the same retracted sources and datasets."""
    from scale import lifecycle
    want = lifecycle.resting(doc, spec)
    got = adapter.resting_records(doc, spec)
    assert set(got["records"]) == set(want), (sorted(set(got["records"]) ^ set(want)))
    for rid, w in want.items():
        g = got["records"][rid]
        assert sorted(g["sources"]) == sorted(w["sources"]), rid
        assert sorted(g["datasets"]) == sorted(w["datasets"]), rid
        assert g["depth"] in (2, adapter.RESTING_DEPTH) and g["witness"]
    if want:
        assert got["complete"] is True
    # support analysis: whether each record rests on retractions entirely or in part, in one request
    support = adapter.resting_support(doc, spec)
    assert support["complete"] is True
    assert set(support["records"]) == set(want), sorted(set(support["records"]) ^ set(want))
    for rid, w in want.items():
        g = support["records"][rid]
        assert (g["rests"], sorted(g["sources"]), sorted(g["datasets"])) == \
            (w["rests"], sorted(w["sources"]), sorted(w["datasets"])), rid
        tally[f"rests_{w['rests']}"] += 1
    tally["resting_records"] += len(want)
    tally["datasets_named"] += sum(len(w["datasets"]) for w in want.values())


def test_the_retracted_source_example_matches_scale(scale_env):
    doc, spec = scale_env["document"]("examples/scale-catal/source-lifecycle/retracted-source.json")
    tally: Counter = Counter()
    compare_resting(doc, spec, tally)
    got = adapter.resting_records(doc, spec)
    test = next(r for r in got["records"] if "/test/" in r)
    hops = got["records"][test]["witness"]
    assert hops[0]["relation"] == adapter.CITES and hops[0]["direction"] == "reverse"   # from the retracted paper
    assert tally["resting_records"] == 4


def loadable_documents(scale_env):
    """Every SCALE document the tools read: the domain's examples, the curated graphs and the kernel's examples."""
    repo = scale_env["repo"]
    out = []
    for pattern in ("domain/examples/**/*.json", "graphs/**/*.json"):
        for path in sorted(repo.glob(pattern)):
            try:
                doc, spec = scale_env["document"](path.relative_to(scale_env["base"]).as_posix()) \
                    if path.is_relative_to(scale_env["base"]) else (None, None)
                if doc is None:
                    from scale.util import load_doc
                    from scale import layout, pgraph
                    doc = load_doc(path)
                    manifest = layout.graph_manifests(repo, experimental=True)["catalysis"]
                    version = (doc.get("graph") or {}).get("schema_version")
                    spec = pgraph.load_spec(pgraph.pinned(manifest, version, doc))
            except Exception:              # noqa: BLE001 - not a document the tools read (a fixture, a part)
                continue
            if isinstance(doc, dict) and isinstance(doc.get("nodes"), list) and isinstance(doc.get("links"), list):
                out.append((path.relative_to(repo).as_posix(), doc, spec))
    return out


NOTICE = {"id": "ex:graph-parity/notice", "label": "Source", "props": {"title": "a notice added by the parity test"}}


def cited_sources(doc):
    label = {n["id"]: n["label"] for n in doc["nodes"]}
    cites = set()
    for r in doc["nodes"] + doc["links"]:
        for ev in (r.get("props") or {}).get("evidence") or []:
            if isinstance(ev, dict) and label.get(ev.get("source")) == "Source":
                cites.add(ev["source"])
    return sorted(cites)


def with_retractions(doc, sources):
    """A copy of ``doc`` in which a notice retracts (or withdraws, alternately) each of ``sources``."""
    out = copy.deepcopy(doc)
    out["nodes"].append(copy.deepcopy(NOTICE))
    for k, s in enumerate(sources):
        link = {"id": f"ex:graph-parity/notice-{k}", "source": NOTICE["id"], "target": s}
        link.update({"type": "RETRACTS", "props": {"scope": "full"}} if k % 2 == 0 else
                    {"type": "UPDATES", "props": {"update_type": "withdrawal"}})
        out["links"].append(link)
    return out


def with_retracted_dataset(doc, source, included):
    """A copy of ``doc`` with a partial retraction recorded as a dataset: the dataset is REPORTED_IN ``source``,
    INCLUDES the ``included`` records, and the notice retracts it."""
    out = copy.deepcopy(doc)
    d = "ex:graph-parity/dataset"
    out["nodes"] += [copy.deepcopy(NOTICE), {"id": d, "label": "Dataset", "props": {"title": "a retracted table"}}]
    out["links"] += [{"id": "ex:graph-parity/reported", "source": d, "target": source, "type": "REPORTED_IN",
                      "props": {}},
                     {"id": "ex:graph-parity/retracts", "source": NOTICE["id"], "target": d, "type": "RETRACTS",
                      "props": {"scope": "full"}}]
    out["links"] += [{"id": f"ex:graph-parity/includes-{k}", "source": d, "target": r, "type": "INCLUDES", "props": {}}
                     for k, r in enumerate(included)]
    return out


def test_retractions_injected_into_every_document_match_scale(scale_env):
    """Differential: every document SCALE reads, with notices retracting a third of the sources it cites, and with a
    partial retraction recorded as a dataset that includes records citing its source and records that do not; each
    copy is made in memory, and GRAPH must flag what scale.lifecycle.resting flags."""
    tally: Counter = Counter()
    for rel, doc, spec in loadable_documents(scale_env):
        sources = cited_sources(doc)
        if not sources:
            continue
        compare_resting(with_retractions(doc, sources[::3]), spec, tally)
        label = {n["id"]: n["label"] for n in doc["nodes"]}
        citing = sorted(n["id"] for n in doc["nodes"] if any(
            isinstance(ev, dict) and ev.get("source") == sources[0]
            for ev in (n.get("props") or {}).get("evidence") or []))
        others = sorted(n["id"] for n in doc["nodes"] if n["id"] not in citing and label[n["id"]] != "Source")
        compare_resting(with_retracted_dataset(doc, sources[0], citing[::2] + others[:2]), spec, tally)
        tally["documents"] += 1
    print(dict(tally))
    assert tally["documents"] >= 50 and tally["resting_records"] >= 500 and tally["datasets_named"] >= 50
    assert tally["rests_all"] >= 100 and tally["rests_some"] >= 100


# ---------------------------------------------------------------------------------------------- tree patterns
def test_every_edge_step_plan_matches_scale_row_for_row(scale_env):
    """Chains, trees and optional steps: GRAPH's pattern gives SCALE's structural rows, in SCALE's order."""
    tally: Counter = Counter()
    for path in sorted((scale_env["repo"] / "domain" / "competency").glob("*.yaml")):
        q = scale_env["load_yaml"](path) or {}
        steps = (q.get("plan") or {}).get("steps") or []
        if not steps or any("edge" not in st for st in steps):
            continue
        root = (q["plan"].get("match") or {}).get("as", "self")
        for rel in q.get("documents") or []:
            doc, spec = scale_env["document"](rel)
            answer = scale_env["run"](doc, spec, adapter.structural(q["plan"]))
            if answer["status"] == "schema_gap":
                continue
            want = [(dict(row["bound"]), list(row["links"])) for row in answer["rows"]]
            p = adapter.projection_from_document(doc, spec)
            res = execute(p, {**adapter.pattern_request(q["plan"], spec), "projection": p.identity,
                              "limits": {"max_results": 10**6}})
            assert res.status == COMPLETED, (path.name, res.error)
            got = [(dict(row["nodes"]), [x for x in row["links"] if x is not None]) for row in res.answer["rows"]]
            assert got == want, path.name
            assert res.answer["total"] == len(want)
            tally["rows"] += len(want)
            tally["plans"] += 1
            tally["trees"] += any(st.get("from", root) != (steps[i - 1]["as"] if i else root)
                                  for i, st in enumerate(steps))
            tally["optional"] += any(st.get("optional") for st in steps)
    print(dict(tally))
    assert tally["plans"] >= 35 and tally["trees"] >= 8 and tally["optional"] >= 2 and tally["rows"] >= 150
