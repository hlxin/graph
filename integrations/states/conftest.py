"""The STATES integration tests run in an environment where STATES is importable (its own virtual environment) and
read its checkout: ``STATES_REPO`` names it, else the checkout the imported ``states`` package comes from
(docs/STATES_INTEGRATION.md). They only read STATES."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def states_repo() -> Path:
    try:
        import states
    except ImportError:
        pytest.skip("STATES is not importable here: run these tests with STATES's environment")
    repo = Path(os.environ.get("STATES_REPO") or Path(states.__file__).resolve().parents[2])
    if not (repo / "examples" / "data").is_dir():
        pytest.skip(f"{repo} is not a STATES checkout (no examples/data)")
    return repo
