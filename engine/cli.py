"""Thin argparse entry point: parse args, call run_suite(), report the result."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence

from engine.config_parser import ConfigError
from engine.test_engine import run_suite

CONFIG_ENV = "WCET_TEST_CONFIG"
PRE_RUN_FAILURE = 254


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m engine.cli",
        description="Run a Robot Framework suite against a config-described target.",
    )
    parser.add_argument("--robot", required=True, help="suite file or directory")
    parser.add_argument(
        "--config",
        default=None,
        help=f"config YAML; falls back to {CONFIG_ENV} if omitted",
    )
    parser.add_argument("--outdir", default="results", help="Robot output directory")
    parser.add_argument(
        "--elf",
        default=None,
        help="firmware ELF; falls back to WCET_FIRMWARE_ELF if omitted",
    )
    args = parser.parse_args(argv)

    if args.config is None:
        args.config = os.environ.get(CONFIG_ENV)
    if not args.config:
        parser.error(f"--config is required, or set {CONFIG_ENV}")

    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    try:
        result = run_suite(args.robot, args.config, outdir=args.outdir, elf=args.elf)
    except (ConfigError, NotImplementedError) as error:
        print(f"error: {error}", file=sys.stderr)
        return PRE_RUN_FAILURE

    print(f"{result.pass_count} passed, {result.fail_count} failed")
    print(f"report: {result.report_html}")
    print(f"log:    {result.log_html}")
    return result.return_code


if __name__ == "__main__":
    sys.exit(main())
