import os
import subprocess
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_fake.yaml"
SUITE = Path(__file__).parent / "test_engine_smoke.robot"
ELF = Path(__file__).parents[1] / "target" / "fixtures" / "f401_usbsim.elf"


def test_cli_runs_the_smoke_suite_against_the_fake_backend(tmp_path):
    outdir = tmp_path / "results"
    env = dict(os.environ)
    env["WCET_ALLOW_FAKE_BACKEND"] = "1"
    env.pop("WCET_TEST_CONFIG", None)
    env.pop("WCET_FIRMWARE_ELF", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "engine.cli",
            "--robot",
            str(SUITE),
            "--config",
            str(FIXTURE),
            "--outdir",
            str(outdir),
            "--elf",
            str(ELF),
        ],
        cwd=Path(__file__).parents[2],
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "1 passed, 0 failed" in completed.stdout
    assert str(outdir / "report.html") in completed.stdout
    assert str(outdir / "log.html") in completed.stdout
    assert (outdir / "report.html").is_file()
    assert (outdir / "log.html").is_file()
