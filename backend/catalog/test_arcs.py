"""Every sample course arc in the pack passes tools/check_arcs.py: real readings, assignments, focuses and outcomes."""
import importlib.util
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "tools" / "check_arcs.py"


@pytest.mark.skipif(not CHECKER.exists(), reason="tools/ isn't shipped in the Docker image")
def test_every_arc_passes_the_checker(capsys):
    spec = importlib.util.spec_from_file_location("check_arcs", CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert checker.main([]) == 0, capsys.readouterr().out
