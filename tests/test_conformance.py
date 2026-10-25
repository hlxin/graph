from graph_runtime import execute
from graph_runtime.conformance import check_case, load_cases, run_conformance


def test_the_reference_engine_conforms():
    report = run_conformance()
    assert report["failures"] == [] and report["passed"] == report["cases"] >= 30


def test_the_suite_catches_a_wrong_backend():
    """A backend that drops the last reached node, or calls an interrupted search complete, fails."""
    def dropping(p, request, **ctx):
        data = execute(p, request, **ctx).to_dict()
        if data.get("answer") and data["answer"].get("reached"):
            data["answer"]["reached"] = data["answer"]["reached"][:-1]
            data["answer"]["count"] -= 1
        return data

    def overclaiming(p, request, **ctx):
        data = execute(p, request, **ctx).to_dict()
        if data["status"] == "interrupted":
            data["status"], data["complete"], data["interruption"] = "completed", True, None
        return data

    for backend in (dropping, overclaiming):
        report = run_conformance(backend)
        assert report["failures"], backend.__name__


def test_every_case_is_checked_against_its_own_expectation():
    suite = load_cases()
    names = [c["name"] for c in suite["cases"]]
    assert len(names) == len(set(names))
    covered = {x for c in suite["cases"] for x in c.get("covers", [])}
    for need in ("parallel edges", "cycles", "reverse traversal", "repeated nodes in typed patterns",
                 "large integer costs keep their order", "no certified negative under a budget",
                 "no certified negative under an output limit", "deadline", "cancellation", "stale snapshot",
                 "negative costs refused", "missing costs refused", "complete negative"):
        assert need in covered, need
    case = next(c for c in suite["cases"] if c["name"] == "reachability-forward")
    assert check_case(case, {"status": "failed", "error": {"code": "x"}}, suite["graphs"]["G1"])
