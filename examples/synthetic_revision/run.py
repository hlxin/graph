"""A synthetic walk through GRAPH: a revised input, what depends on it, and a hypothesis candidate about it.

1. Build a projection from invented dependency records (records.json): an input, a model trained on it, two
   predictions, a ranking of them and a report.
2. Ask which records depend on the revised input (dependency impact), with a witness for each.
3. Record a hypothesis candidate: the revision might change the order of the two predictions in the ranking. Its
   basis is the impact result and the witness of the ranking; its proposed test (recompute the ranking) is a plan,
   not a completed validation; its 0.8 priority is an ordering value, not an 80 % probability.
4. Ask whether the candidate is stale after a later change to the model: it is, with the witnesses that say why;
   that makes it due for re-examination, not false. Its edge and result references are listed as undecided:
   dependency impact follows nodes.

Everything here is synthetic. It exercises transport and provenance and asserts no scientific result.

    python examples/synthetic_revision/run.py           # rewrite out/
    python examples/synthetic_revision/run.py --check   # fail if out/ differs from a fresh run
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from graph_runtime import HypothesisCandidate, Projection, execute, staleness, verify_result, witness_refs

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
AT = "2026-10-25T00:00:00Z"            # fixed, so that every run writes the same records
DEPENDENTS = [{"relation": "DEPENDS_ON", "direction": "reverse"}]    # from a dependency to what depends on it


def outputs() -> dict[str, dict]:
    records = json.loads((HERE / "records.json").read_text(encoding="utf-8"))
    p = Projection.build(nodes=records["nodes"], edges=records["edges"], relations=records["relations"],
                         bindings=records["bindings"])

    impact = execute(p, p.request("dependency_impact", changed=["input:feedstock-table-v1"], scope=DEPENDENTS))
    assert impact.complete and verify_result(impact, nodes=records["nodes"], edges=records["edges"]) == []

    candidate = HypothesisCandidate.create(
        statement="Revising input:feedstock-table-v1 might change the order of prediction:catalyst-A and "
                  "prediction:catalyst-B in ranking:screen-1.",
        scope="Synthetic example: the order within ranking:screen-1 only. No claim about any real catalyst.",
        projection=p,
        producer={"name": "examples/synthetic_revision", "version": "1"},
        basis=witness_refs(impact, "ranking:screen-1"),
        dependencies=[("node", "input:feedstock-table-v1"), ("node", "ranking:screen-1")],
        assumptions=["The revision changes values that model:yield-model uses.",
                     "The ranking is recomputed with the same model version and settings."],
        proposed_tests=[{"id": "recompute-ranking",
                         "description": "Recompute ranking:screen-1 from the revised input with the same model "
                                        "version, and compare the order of the two predictions.",
                         "outcomes": [{"observation": "the two predictions change order", "bearing": "supports"},
                                      {"observation": "the order is unchanged", "bearing": "opposes"},
                                      {"observation": "the recomputation fails, or the predictions tie",
                                       "bearing": "inconclusive"}]}],
        scores=[{"value": 0.8, "method": "synthetic-priority", "method_version": "0",
                 "candidate_set": "synthetic-revision-candidates", "direction": "higher_first",
                 "interpretation": "ordering"}],
        at=AT, reason="formed from the dependency impact of the revised input")

    later = execute(p, p.request("dependency_impact", changed=["model:yield-model"], scope=DEPENDENTS))
    return {"projection.json": p.to_dict(),
            "impact_result.json": impact.to_dict(timing=False),
            "candidate.json": candidate.to_dict(),
            "staleness_after_model_change.json": staleness(candidate, projection=p, impact=later)}


def main(argv: list[str]) -> int:
    fresh = {name: json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
             for name, data in outputs().items()}
    if "--check" in argv:
        stale = [n for n, text in fresh.items()
                 if not (OUT / n).exists() or (OUT / n).read_text(encoding="utf-8") != text]
        if stale:
            print(f"out/ differs from a fresh run: {', '.join(stale)} (run without --check to rewrite)")
            return 1
        print(f"out/ matches a fresh run ({len(fresh)} files)")
        return 0
    OUT.mkdir(exist_ok=True)
    for name, text in fresh.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(fresh)} files to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
