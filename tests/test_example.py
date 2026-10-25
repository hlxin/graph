import json
import os
import subprocess
import sys
from pathlib import Path

from graph_runtime import HypothesisCandidate, Result, json_schema

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "synthetic_revision"


def test_the_example_outputs_are_current():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    done = subprocess.run([sys.executable, str(EXAMPLE / "run.py"), "--check"], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_example_records_read_back():
    impact = Result.from_dict(json.loads((EXAMPLE / "out" / "impact_result.json").read_text()))
    assert impact.complete and impact.affected("ranking:screen-1") is True
    cand = HypothesisCandidate.from_json((EXAMPLE / "out" / "candidate.json").read_text())
    assert cand.epistemic_status == "unverified" and cand.scores[0]["interpretation"] == "ordering"
    assert cand.basis[0] == {"type": "result", "ref": impact.result_id}
    stale = json.loads((EXAMPLE / "out" / "staleness_after_model_change.json").read_text())
    assert stale["stale"] and "does not make the candidate false" in stale["note"]
    try:
        import jsonschema
    except ImportError:
        return
    jsonschema.validate(cand.to_dict(), json_schema("hypothesis_candidate"))
    jsonschema.validate(impact.to_dict(timing=False), json_schema("result"))
