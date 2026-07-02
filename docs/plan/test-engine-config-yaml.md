# Plan: Layer 2 — Test Engine, Config YAML, and the parser

Status: proposed, not started.

Depends on [`usb-peripheral-restructure.md`](usb-peripheral-restructure.md):
Task 1.4 below resolves peripheral driver paths like
`peripherals.usb.host.cdc.UsbHostCdcDevice`, which only exist once that
plan's Phase 2 has landed. If this plan is executed first or independently,
substitute the current paths (`peripherals.usb_host.UsbHostCdcDevice`) —
the design does not otherwise depend on the restructure.

## Why

This is Layer 2 from the architecture discussion: the layer that parses a
test script and a SoC/mock config, wires up the right backend and peripheral
objects, and runs the test — sitting between Robot Framework `.robot` scripts
(Layer 1) and the existing `peripherals/` + `target/` + `backends/` stack
(Layers 3/4, unchanged by this plan).

Three pieces, matching the original architecture sketch:

- **Configuration Manager** — parses SoC + Mock List YAML into live objects.
- **Test Execution Engine** — runs a `.robot` suite against those objects and
  reports results.
- **Abstract peripheral APIs** — thin Robot Framework keyword libraries over
  the existing peripheral classes; not reimplementing them.

### Non-goals

- No CLI/CI-CD plugin beyond a minimal argument-parsing entry point (Phase 3)
  — the full Layer 1 "User & API Layer" is out of scope.
- No concurrency/locking model for multiple simultaneously-active peripherals
  — tracked in `todo.md`, not resolved here. This plan's Config Manager wires
  up multiple peripherals but test scripts still drive them one at a time.
- No real capability model (which SoC/peripheral supports which features) —
  Phase 4's capability bridge is interface-only, wiring a model that doesn't
  exist yet. Building that model is a follow-on, not part of this plan.
- No Raspberry Pi HIL backend (Layer 4b) — out of scope per earlier
  discussion in this project.

## Key decisions

1. **Config Manager is pure Python, zero Robot Framework dependency.** It
   must be unit-testable as "given this YAML, do I get the right objects"
   without ever invoking Robot. This is the load-bearing design constraint —
   everything in Phase 1 has no `robot` import.
2. **`robot.run()` is invoked programmatically, in-process — never shelled
   out to the `robot` CLI.** Live peripheral/gateway objects (an open J-Link
   session, a connected `TargetGateway`) can't be serialized into a `-v`
   command-line variable. The engine builds those objects, installs them in
   a runtime registry, then calls into Robot Framework's Python API directly.
3. **Keyword library instances take zero constructor arguments** and read
   live peripheral objects from the runtime registry. This is what lets
   Robot Framework construct them at parse time (before the registry is
   necessarily populated in a given call order) while still getting
   per-run, config-resolved objects.
4. **A `"fake"` backend is a first-class, explicitly-labeled option in the
   config schema**, not a hidden test hack — it's what makes every keyword
   library and Config Manager behavior testable in CI with no hardware
   attached. It must be impossible to select by accident (see Task 2.4).
5. **This repo has no dependency manifest today** — `target/`, `backends/`,
   `peripherals/`, `firmware_abi/` import nothing beyond the standard
   library. This plan is the first to need external packages (PyYAML,
   `robotframework`, `robotframework-pythonlibcore`), so establishing a
   manifest is Phase 0, not assumed.

## Target structure

```text
engine/
  __init__.py
  config_schema.py       # SocConfig, ProbeConfig, GatewayConfig, MockConfig dataclasses
  config_parser.py         # parse_config(path) -> ResolvedConfig; validation, ConfigError
  backend_factory.py        # build_backend(soc) -> TargetGateway; "jlink" | "openocd" | "fake"
  peripheral_factory.py      # build_peripherals(mocks, gateway) -> dict[str, object]
  context.py                  # TestContext, build_context(path) -- the Config Manager entry point
  registry.py                   # process-wide runtime registry: install / peripheral / gateway / reset
  test_engine.py                  # run_suite(robot_target, config_path, outdir) -> RunResult
  cli.py                            # thin argparse entry point calling run_suite()
  keywords/
    __init__.py
    usb_host_cdc_keywords.py         # UsbHostCdcKeywords(HybridCore) -- example keyword library
  capabilities_bridge.py               # interface-only stub, Phase 4

tests/
  engine/
    fixtures/
      minimal_fake.yaml                # single fake-backend USB host CDC mock, for parser + engine tests
      overlapping_mocks.yaml             # deliberately colliding MMB addresses, for negative tests
    test_config_parser.py
    test_backend_factory.py
    test_peripheral_factory.py
    test_context.py
    test_registry.py
    test_engine_smoke.robot               # trivial single-Log-keyword suite for Task 2.2
    test_engine_smoke.py                   # drives run_suite() against it
    test_usb_host_cdc_keywords.robot        # exercises the example keyword library end-to-end (fake backend)
    test_usb_host_cdc_keywords.py
```

## Config YAML shape (target for Phase 1)

```yaml
soc:
  part: STM32F407VG
  backend: jlink            # jlink | openocd | fake
  probe:
    serial: "000123456789"
  gateway:
    mmb_base: 0x2001E000
  timeouts:                 # optional; Task 4.1
    default_s: 3.0

mocks:
  - name: usb_host
    driver: peripherals.usb.host.cdc.UsbHostCdcDevice
    abi: peripherals.usb.host.backends.stm32_otgfs.Stm32OtgFsHostAbi
    channels: 8
```

`driver` is the class the keyword library wraps; `abi` is the concrete
per-MCU contract implementation the driver is constructed with. Two-level
resolution because a class driver (`UsbHostCdcDevice`) is reusable across
SoCs, but which ABI wires it to hardware is per-SoC — this is exactly the
split Phase 2/3 of the restructure plan sets up.

## Tasks

### Phase 0 — scaffold

- [ ] **0.1 — Establish a dependency manifest.** Add `pyproject.toml` (or
      `requirements.txt`, whichever matches how the project is actually
      run today — check before choosing) declaring `PyYAML`,
      `robotframework`, and `robotframework-pythonlibcore`. First
      dependencies this repo has ever declared; call that out in the PR/commit
      description so it isn't a surprise.
      Verify: a clean virtualenv install from the manifest succeeds; existing
      tests (which need none of these) still pass without it.
- [ ] **0.2 — Create the `engine/` package** (flat, top-level — matching
      `target/`, `backends/`, `peripherals/` convention, not nested under a
      namespace package). Empty `__init__.py`, no logic yet.
      Verify: `import engine` works from repo root.
- [ ] **0.3 — Write the config schema + example fixtures before the parser.**
      Commit `tests/engine/fixtures/minimal_fake.yaml` (shape above, using
      `backend: fake`) and `overlapping_mocks.yaml`. No parser code yet —
      these are the target the parser is built against.
      Verify: fixtures are valid YAML (`python -c "import yaml; yaml.safe_load(open(...))"`).

### Phase 1 — Config Manager

- [ ] **1.1 — `engine/config_schema.py`.** Dataclasses for `SocConfig`,
      `ProbeConfig`, `GatewayConfig`, `MockConfig`, `TimeoutConfig`. Pure
      data, no parsing or I/O.
      Verify: unit test constructs each dataclass directly, checks defaults.
- [ ] **1.2 — `engine/config_parser.py`: `parse_config(path) -> ResolvedConfig`.**
      Loads YAML, validates required fields, raises `ConfigError` with a
      specific message for each failure mode (missing `soc:`, unknown
      `backend:`, empty `mocks:`, duplicate mock `name:`).
      Verify: table-driven tests — one passing case against
      `minimal_fake.yaml`, one test per failure mode above, each asserting
      the specific `ConfigError` message.
- [ ] **1.3 — `engine/backend_factory.py`: `build_backend(soc) -> TargetGateway`.**
      Registry mapping `backend: "jlink"` → `JLinkBackend(...)`,
      `"openocd"` → `OpenOcdBackend(...)`. Unknown backend name (including
      `"pyocd"`, `"renode"` — not implemented yet) raises `NotImplementedError`
      with a message naming what's missing, not a silent no-op.
      Verify: unit test with a monkeypatched backend constructor (no real
      probe connection) asserts the right class is selected per config.
- [ ] **1.4 — `engine/peripheral_factory.py`: `build_peripherals(mocks, gateway) -> dict[str, object]`.**
      Resolves each mock's `driver:` and `abi:` dotted paths via
      `importlib`, constructs the ABI against the gateway, then the driver
      against the ABI, keyed by `name:`.
      Verify: unit test with a fake gateway and the `minimal_fake.yaml`
      fixture asserts `build_peripherals(...)["usb_host"]` is the expected
      type and its `.abi` is wired to the fake gateway.
- [ ] **1.5 — `engine/context.py`: `TestContext` + `build_context(path) -> TestContext`.**
      Ties 1.1–1.4 together as the single Config Manager entry point.
      Verify: end-to-end unit test, `minimal_fake.yaml` in, a `TestContext`
      out with a working fake-backed peripheral — zero RF, zero real
      hardware.
- [ ] **1.6 — MMB address collision validation.** `build_context` (or
      `peripheral_factory`) rejects configs where two mocks' resolved base
      addresses + sizes overlap. Closes the matching `todo.md` item.
      Verify: `overlapping_mocks.yaml` fixture raises `ConfigError`;
      `minimal_fake.yaml` (non-overlapping) does not.

### Phase 2 — runtime registry + Test Engine

- [ ] **2.1 — `engine/registry.py`.** `install(context)`, `peripheral(name)`,
      `gateway()`, `reset()`. Process-wide, minimal — no threading concerns
      yet (single active suite at a time).
      Verify: unit test installs a fake context, checks lookups, checks
      `reset()` clears it (needed so successive test-engine runs in the same
      process/pytest session don't leak state between them).
- [ ] **2.2 — `engine/test_engine.py`: `run_suite(robot_target, config_path, outdir="results") -> RunResult`.**
      `build_context` → `registry.install` → `robot.run(robot_target, outputdir=outdir)`.
      Verify against `tests/engine/test_engine_smoke.robot` — a single
      suite with one `Log` keyword and no peripheral dependency — proving
      the plumbing works before any keyword-library complexity is added.
- [ ] **2.3 — Example keyword library: `engine/keywords/usb_host_cdc_keywords.py`.**
      `UsbHostCdcKeywords(HybridCore)` wrapping
      `peripherals.usb.host.cdc.UsbHostCdcDevice`, reading from
      `registry.peripheral("usb_host")`. Exposes `Attach Device`,
      `Enumerate Device`, `Echo And Verify` (per the design already agreed —
      see the architecture doc's USB-host section for the underlying calls).
      Verify: `tests/engine/test_usb_host_cdc_keywords.robot` runs these
      three keywords through `run_suite()` against a `backend: fake` config
      and passes.
- [ ] **2.4 — Add the `"fake"` backend to `backend_factory.py`.** Constructs
      the shared `FakeMmbGateway` test double (from the restructure plan's
      Task 4.2) instead of a real probe. Gate it behind an explicit
      `WCET_ALLOW_FAKE_BACKEND=1` environment check in `build_backend`, so a
      config typo can never silently run a "test" against nothing.
      Verify: `minimal_fake.yaml` works with the env var set; a unit test
      asserts `build_backend` raises without it.
- [ ] **2.5 — `RunResult` dataclass.** `run_suite()` returns pass/fail
      counts and the `output.xml`/`log.html`/`report.html` paths instead of
      just Robot's raw exit code, so a future Layer 1 CLI has something
      structured to consume.
      Verify: unit test against the Task 2.2 smoke suite checks the
      returned counts and that the artifact paths exist on disk.

### Phase 3 — CLI entry point

- [ ] **3.1 — `engine/cli.py`: `main()`.** `argparse` with `--robot`,
      `--config`, `--outdir`; calls `run_suite()`, exits non-zero on
      failure. No logic beyond argument plumbing and calling Phase 2's
      function.
      Verify: `python -m engine.cli --robot tests/engine/test_engine_smoke.robot --config tests/engine/fixtures/minimal_fake.yaml` exits 0.
- [ ] **3.2 — End-to-end usage note.** Short doc (new section in the
      architecture doc, or a `docs/plan/` follow-up note) with the exact
      command line and the minimal config + `.robot` pair from Phase 2/3, so
      this is demoable, not just covered by unit tests.
      Verify: copy-pasting the documented command from a clean checkout
      works.

### Phase 4 — follow-through on existing `todo.md` items

- [ ] **4.1 — Config-driven timeouts.** Read the optional `timeouts:` block
      (Phase 1's `TimeoutConfig`) and thread it into peripheral construction
      (e.g. `UsbHostCdcDevice(abi, timeout_s=config.timeouts.default_s)`),
      replacing hardcoded defaults where they matter for cross-backend
      portability. Scoped to plumbing the value through, not auditing every
      timeout in the codebase.
      Verify: a fixture config with a non-default `timeouts.default_s`
      produces a peripheral instance with that timeout set.
- [ ] **4.2 — Capability bridge stub.** `engine/capabilities_bridge.py`:
      the shape of a function translating a (not-yet-defined) per-mock
      capability set into Robot Framework `--include`/`--exclude` tag
      arguments for `robot.run()`. Interface and wiring only — raises
      `NotImplementedError` in the body. The capability model itself
      (`target/capabilities.py` today only covers backend capabilities, not
      peripheral/SoC ones) is explicitly a follow-on, not part of this task.
      Verify: the function exists, is called from `test_engine.py` behind a
      no-op default (empty include/exclude), and existing suites are
      unaffected.

## Order

Phase 0 before everything. Within Phase 1, tasks are sequential (each
depends on the previous). Phase 2 needs all of Phase 1. Phase 3 needs Task
2.2 and 2.5. Phase 4 tasks are independent of each other and of Phase 3 —
either can run first, or in parallel with Phase 3.
