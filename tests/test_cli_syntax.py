"""Every entry-point script must at least compile (they are not imported by other tests)."""
import py_compile
from pathlib import Path

import pytest

SCRIPTS = sorted(Path(__file__).parent.parent.glob("*.py"))


@pytest.mark.parametrize("script", SCRIPTS, ids=[s.name for s in SCRIPTS])
def test_script_compiles(script):
    py_compile.compile(str(script), doraise=True)
