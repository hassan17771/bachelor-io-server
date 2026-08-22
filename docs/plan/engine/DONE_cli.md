# Plan: engine/cli.py

Status: Done.

Branch: `robot-framework-cli` (already created and checked out — not
`plan/engine-cli`, by explicit instruction, so §1 of plan-driven-dev is
satisfied by staying on it rather than branching again).

## Why

[`test-engine-config-yaml.md`](../test-engine-config-yaml.md) built
`run_suite()` but explicitly scoped the CLI entry point out (its Phase 3).
That task now lives in [`todo.md`](../../../todo.md) under "CLI, docs, and
capability bridge":

> `engine/cli.py`: thin `argparse` entry point (`--robot`, `--config`,
> `--outdir`) calling `run_suite()`

This plan builds exactly that. The other items in that `todo.md` section
(usage note, `libdoc` docs, YAML reference doc, config-driven timeouts,
capability bridge stub) are not touched here.

## Key decisions

1. **Invocation is `python -m engine.cli`, not a packaged command.** The
   repo has no `pyproject.toml`/console-scripts — only `requirements.txt`
   (Phase 0.1 of the parent plan) — so adding a packaging entry point here
   would be new scope, not just a thin wrapper.
2. **`--robot` has no env fallback; `--config` does.** A suite path changes
   per-invocation; a board config is comparatively stable across a session.
   `--config` falls back to `WCET_TEST_CONFIG`, mirroring the existing
   `elf`/`WCET_FIRMWARE_ELF` precedence in `engine/context.py`
   (`resolve_mmb_base`): flag if given, else env var, else error. Missing
   both is an `argparse` usage error (exit 2), not a `ConfigError`.
3. **No `--allow-fake` flag.** `WCET_ALLOW_FAKE_BACKEND` stays a manually-set
   env var with no CLI shortcut — out of scope for this plan (per Key
   decision 4 of the parent plan, selecting the fake backend must never be a
   single accidental flag away).
4. **Exit code 254 for pre-run failures, Robot's own code otherwise.**
   `run_suite()` can raise `ConfigError` or `NotImplementedError` before
   `robot.run()` ever executes (bad YAML, missing ELF, unimplemented
   backend/probe) — these get a clean `error: <message>` on stderr and exit
   **254**, Robot's own unused return-code slot (0–250 = failed-test count,
   251/252/253/255 = Robot's own statuses — see `robot.errors`). Picking an
   unused Robot code, rather than an arbitrary low number like 2, means a
   pre-run failure can never be misread as "254 tests failed." Any other
   exception is a real engine bug, not a user-facing config problem — it
   propagates with its traceback rather than being caught. Once
   `robot.run()` does execute, `RunResult.return_code` is returned as-is
   (unnormalized) — a CI script that wants "how many tests failed" already
   has it.
5. **No new `run_suite()` kwargs in this plan.** Directory targets already
   work (`robot.run()` recursively collects `.robot` files, confirmed with a
   `--dryrun` run against `tests/engine/`), and console output +
   `output.xml`/`log.html`/`report.html` are all produced by `run_suite()`
   today with zero CLI-side code. Tag filtering (`--include`/`--exclude`)
   and console verbosity (`--loglevel`/`--quiet`) would need new pass-through
   kwargs on `run_suite()` itself — deferred, not this plan's scope. The CLI
   adds one summary line + report/log paths printed after the run completes.

## Target structure

```text
engine/
  cli.py                    # main(argv=None) -> int; argparse + run_suite() + error handling
tests/
  engine/
    test_cli.py               # unit tests, run_suite() monkeypatched
    test_cli_subprocess.py     # one end-to-end `python -m engine.cli` invocation, fake backend
```

## Tasks

- [x] **1 — `engine/cli.py`.** `main(argv=None) -> int`: parses `--robot`
      (required), `--config` (optional, falls back to `WCET_TEST_CONFIG`,
      `parser.error()` if neither set), `--outdir` (default `"results"`),
      `--elf` (optional, passed through to `run_suite`, which already falls
      back to `WCET_FIRMWARE_ELF`). Calls `run_suite(...)`; on success prints
      `"{pass_count} passed, {fail_count} failed"` plus the `report.html`/
      `log.html` paths, returns `result.return_code`. Catches `ConfigError`/
      `NotImplementedError`, prints `error: {message}` to stderr, returns
      254. `if __name__ == "__main__": sys.exit(main())`.
      Verify: `tests/engine/test_cli.py`, `run_suite` monkeypatched (no real
      Robot/hardware/fake backend involved) —
      happy path returns `run_suite`'s `return_code` and prints the summary;
      `--config` omitted with `WCET_TEST_CONFIG` set reaches `run_suite`
      with the env value; `--config` omitted and env unset is an argparse
      error (exit 2, `run_suite` never called); a `ConfigError` from
      `run_suite` prints to stderr and returns 254 with no traceback; same
      for `NotImplementedError`; `--outdir`/`--elf` values (and their
      defaults) reach the `run_suite` call unchanged.
      Run: `pytest tests/engine/test_cli.py -v`

- [x] **2 — End-to-end subprocess smoke test.** `tests/engine/
      test_cli_subprocess.py` runs `python -m engine.cli --robot
      tests/engine/test_engine_smoke.robot --config
      tests/engine/fixtures/minimal_fake.yaml --outdir <tmp> --elf <fixture
      elf>` as a real subprocess with `WCET_ALLOW_FAKE_BACKEND=1`, asserting
      exit code 0, the summary line in stdout, and `report.html`/`log.html`
      on disk. This is the only task that exercises real `argv`/env parsing
      through `python -m` — Task 1's tests call `main()` directly and never
      touch process invocation.
      Run: `pytest tests/engine/test_cli_subprocess.py -v`

## Order

Task 1 first (Task 2 needs `engine/cli.py` to exist and be correct). Both
are independent of the rest of `todo.md`'s CLI section — no docs/`libdoc`/
capability-bridge work is needed for either.
