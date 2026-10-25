"""The command line a newcomer meets first: every --help must print, not crash."""
import pytest

from pgdocket import cli

COMMANDS = ["check", "report", "install", "create", "worker", "label", "calibrate", "estimate", "plan", "drift", "status", "drop",
            "revise", "compare", "backfill", "promote", "retire", "versions"]


def test_top_level_help_lists_every_command(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert all(c in out for c in COMMANDS)


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_working_help(command, capsys):
    with pytest.raises(SystemExit) as e:          # argparse reads "%" in help text as a format code
        cli.main([command, "--help"])
    assert e.value.code == 0 and "usage:" in capsys.readouterr().out
