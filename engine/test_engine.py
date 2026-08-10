"""Run a Robot Framework suite against the objects a config describes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import robot
from robot.api import ExecutionResult

from engine import registry
from engine.context import build_context


@dataclass(frozen=True)
class RunResult:
    return_code: int
    pass_count: int
    fail_count: int
    output_xml: Path
    log_html: Path
    report_html: Path

    @property
    def passed(self) -> bool:
        return self.return_code == 0


def run_suite(
    robot_target: Union[str, Path],
    config_path: Union[str, Path],
    outdir: Union[str, Path] = "results",
    elf: Optional[Union[str, Path]] = None,
) -> RunResult:
    """Connect the gateway `config_path` describes and run `robot_target` on it.

    `elf` is the firmware build under test, defaulting to WCET_FIRMWARE_ELF;
    the MMB address is read from it.

    The connection lives exactly as long as the run: nothing above or below
    this function opens or closes the gateway.
    """
    outdir = Path(outdir)
    context = build_context(config_path, elf)
    with context.gateway:
        registry.install(context)
        try:
            return_code = robot.run(str(robot_target), outputdir=str(outdir))
        finally:
            registry.reset()

    output_xml = outdir / "output.xml"
    statistics = ExecutionResult(str(output_xml)).statistics.total
    return RunResult(
        return_code=return_code,
        pass_count=statistics.passed,
        fail_count=statistics.failed,
        output_xml=output_xml,
        log_html=outdir / "log.html",
        report_html=outdir / "report.html",
    )
