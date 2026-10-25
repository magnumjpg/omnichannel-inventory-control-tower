"""CLI surface tests (no repository side effects)."""

from __future__ import annotations

import pytest

from main import _build_parser, main


def test_cli_exposes_the_four_documented_workflows():
    parser = _build_parser()
    options = {action.dest for action in parser._actions}
    assert {"generate_data", "run_pipeline", "export_excel", "run_all"} <= options
    assert {"seed", "quiet"} <= options

    args = parser.parse_args(["--run-all", "--seed", "7", "--quiet"])
    assert args.run_all is True
    assert args.seed == 7
    assert args.quiet is True


def test_no_arguments_prints_help_without_running(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "inventory-control-tower" in out
    assert "--run-all" in out


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "Omnichannel multi-echelon" in capsys.readouterr().out
