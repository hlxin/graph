"""The SCALE integration tests run in an environment where SCALE is importable (its own virtual environment) and read
its checkout: ``SCALE_REPO`` names it, else the checkout the imported ``scale`` package comes from
(docs/SCALE_INTEGRATION.md). They only read SCALE."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def scale_repo() -> Path:
    try:
        import scale
    except ImportError:
        pytest.skip("SCALE is not importable here: run these tests with SCALE's environment")
    repo = Path(os.environ.get("SCALE_REPO") or Path(scale.__file__).resolve().parents[1])
    if not (repo / "domain" / "competency").is_dir():
        pytest.skip(f"{repo} is not a SCALE checkout (no domain/competency)")
    return repo
