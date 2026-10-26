"""Cached answers across curation edits, on SCALE's records.

Every competency question whose plan is one chain of edge steps is answered as a typed path from each matched record,
with its certificate, and the answers are cached. Each document is then edited the way curation edits one: a link
removed, a link added (a copy of a link of a type the chain follows, to another record of the same label), a record
given another label. Every cached answer is revalidated against the edited document's projection with one diff.
"holds" must agree with a fresh run (the same found, and the cached answer checks out against the edited records);
"fails" must not. Everything is read and copied in memory; nothing in SCALE is written.
"""

from __future__ import annotations

import copy
import random
import time
from collections import Counter

import scale_graph_adapter as adapter
from graph_runtime import COMPLETED, execute, verify_result
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from test_scale_parity import scale_env  # noqa: F401 - the fixture


def edits(doc, spec, pattern, rng):
    """Three edited copies of ``doc``: a link of a chain relation removed, one added, a record relabeled."""
    relations = {s["relation"] for pos in pattern for s in pos["scope"]}
    chain_links = [e for e in doc["links"] if e["type"] in relations]
    label = {n["id"]: n["label"] for n in doc["nodes"]}
    out = []
    if chain_links:
        gone = rng.choice(chain_links)["id"]
        removed = copy.deepcopy(doc)
        removed["links"] = [e for e in removed["links"] if e["id"] != gone]
        out.append(("link removed", removed))
        link = rng.choice(chain_links)
        same = sorted(n for n, lab in label.items() if lab == label.get(link["target"]) and n != link["target"])
        if same:
            added = copy.deepcopy(doc)
            added["links"].append({**copy.deepcopy(link), "id": f"{link['id']}#copy",
                                   "target": rng.choice(same)})
            out.append(("link added", added))
    labels = sorted(set(label.values()))
    if len(labels) > 1:
        relabeled = copy.deepcopy(doc)
        node = rng.choice(relabeled["nodes"])
        node["label"] = rng.choice([lab for lab in labels if lab != node["label"]])
        out.append(("record relabeled", relabeled))
    return out


def test_cached_chain_answers_survive_or_fail_curation_edits_as_fresh_runs_say(scale_env):
    tally: Counter = Counter()
    times = Counter()
    for path in sorted((scale_env["repo"] / "domain" / "competency").glob("*.yaml")):
        q = scale_env["load_yaml"](path) or {}
        if not q.get("plan"):
            continue
        for rel in q.get("documents") or []:
            doc, spec = scale_env["document"](rel)
            try:
                pattern = adapter.chain(q["plan"], spec)
            except adapter.Unsupported:
                continue
            p = adapter.projection_from_document(doc, spec)
            cached = [execute(p, p.request("typed_path", sources=[rid], pattern=pattern, certificate=True))
                      for rid in adapter.matched(doc, spec, q["plan"])]
            if not cached or any(r.status != COMPLETED for r in cached):
                continue
            rng = random.Random(f"{path.name}:{rel}")
            for kind, edited in edits(doc, spec, pattern, rng):
                p2 = adapter.projection_from_document(edited, spec)
                records = p2.to_dict()
                t0 = time.perf_counter()
                d = diff(p, p2)
                verdicts = [revalidate(r, p2, diff=d) for r in cached]
                t1 = time.perf_counter()
                fresh = [execute(p2, {**r.request, "projection": p2.identity}) for r in cached]
                t2 = time.perf_counter()
                times["revalidate_s"] += t1 - t0
                times["recompute_s"] += t2 - t1
                for old, v, new in zip(cached, verdicts, fresh):
                    assert v.checked == "diff"
                    if v.verdict == HOLDS:
                        assert new.status == COMPLETED and new.found is old.found, (path.name, kind, v.reasons)
                        assert verify_result(old, nodes=records["nodes"], edges=records["edges"]) == []
                    elif v.verdict == FAILS:
                        assert new.status != COMPLETED or new.found is not old.found or \
                            verify_result(old, nodes=records["nodes"], edges=records["edges"]), v.reasons
                    tally[f"{kind}: {v.verdict}"] += 1
                    tally[v.verdict] += 1
    print({**dict(tally), **{k: round(v, 4) for k, v in times.items()}})
    assert tally["holds"] >= 100 and tally["fails"] >= 5
