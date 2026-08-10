from pathlib import Path

import pytest

from engine import cli
from engine.config_parser import ConfigError
from engine.test_engine import RunResult


def make_result(return_code=0, pass_count=1, fail_count=0):
    return RunResult(
        return_code=return_code,
        pass_count=pass_count,
        fail_count=fail_count,
        output_xml=Path("results/output.xml"),
        log_html=Path("results/log.html"),
        report_html=Path("results/report.html"),
    )


@pytest.fixture(autouse=True)
def no_ambient_config_env(monkeypatch):
    monkeypatch.delenv(cli.CONFIG_ENV, raising=False)


def test_happy_path_returns_run_suites_code_and_prints_summary(monkeypatch, capsys):
    calls = []

    def fake_run_suite(robot_target, config_path, outdir, elf):
        calls.append((robot_target, config_path, outdir, elf))
        return make_result(return_code=0, pass_count=3, fail_count=1)

    monkeypatch.setattr(cli, "run_suite", fake_run_suite)

    code = cli.main(["--robot", "suite.robot", "--config", "config.yaml"])

    assert code == 0
    assert calls == [("suite.robot", "config.yaml", "results", None)]
    out = capsys.readouterr().out
    assert "3 passed, 1 failed" in out
    assert "report: results/report.html" in out
    assert "log:    results/log.html" in out


def test_config_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv(cli.CONFIG_ENV, "env-config.yaml")
    calls = []
    monkeypatch.setattr(
        cli,
        "run_suite",
        lambda robot_target, config_path, outdir, elf: calls.append(config_path)
        or make_result(),
    )

    cli.main(["--robot", "suite.robot"])

    assert calls == ["env-config.yaml"]


def test_missing_config_and_env_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "run_suite", lambda *a, **k: pytest.fail("run_suite should not be called")
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--robot", "suite.robot"])

    assert excinfo.value.code == 2
    assert "--config" in capsys.readouterr().err


def test_config_error_is_reported_without_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "run_suite",
        lambda *a, **k: (_ for _ in ()).throw(ConfigError("bad config")),
    )

    code = cli.main(["--robot", "suite.robot", "--config", "config.yaml"])

    assert code == cli.PRE_RUN_FAILURE
    assert "error: bad config" in capsys.readouterr().err


def test_not_implemented_backend_is_reported_without_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "run_suite",
        lambda *a, **k: (_ for _ in ()).throw(NotImplementedError("no pyocd")),
    )

    code = cli.main(["--robot", "suite.robot", "--config", "config.yaml"])

    assert code == cli.PRE_RUN_FAILURE
    assert "error: no pyocd" in capsys.readouterr().err


def test_outdir_and_elf_are_passed_through(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "run_suite",
        lambda robot_target, config_path, outdir, elf: calls.append((outdir, elf))
        or make_result(),
    )

    cli.main(
        [
            "--robot",
            "suite.robot",
            "--config",
            "config.yaml",
            "--outdir",
            "out",
            "--elf",
            "build.elf",
        ]
    )

    assert calls == [("out", "build.elf")]
