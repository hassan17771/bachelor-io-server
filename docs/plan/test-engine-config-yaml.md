# Plan: Layer 2 — Test Engine, Config YAML, and the parser

Status: in progress — Phase 0 complete.

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
- No multi-peripheral configs at all — the MMB structure (how multiple
  peripherals share the memory-mapped block) isn't designed yet, so the
  Config Manager accepts exactly one `mocks:` entry and rejects more.
  Resolving the MMB structure, and the concurrency/locking model that
  follows from it, is tracked in `todo.md`, not here.
- No real capability model (which SoC/peripheral supports which features) —
  Phase 4's capability bridge is interface-only, wiring a model that doesn't
  exist yet. Building that model is a follow-on, not part of this plan. The
  same gap applies to ABI resolution (Task 1.4): for now it's a hardcoded
  stub assuming every STM32 `soc.part` supports USB-host CDC via
  `Stm32OtgFsHostAbi`; the real SoC/peripheral-class → ABI compatibility
  table is a `todo.md` item, not built here.
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
   attached. It must be impossible to select by accident (see Task 2.5).
5. **This repo has no dependency manifest today** — `target/`, `backends/`,
   `peripherals/`, `firmware_abi/` import nothing beyond the standard
   library. This plan is the first to need external packages (PyYAML,
   `robotframework`, `robotframework-pythonlibcore`), so establishing a
   manifest is Phase 0, not assumed.
6. **Mock config names a peripheral `class:`, never a Python import path —
   resolution to driver/ABI is a two-step lookup, and only the first step
   is real.** `class:` → driver is a fixed, SoC-independent registry (safe
   to build now: a class driver is reusable across SoCs by design).
   `soc.part` + `class:` → ABI is a genuine compatibility question this
   plan does not solve; Task 1.4 ships a hardcoded stub instead
   (`STM32* supports cdc via Stm32OtgFsHostAbi`, everything else
   `ConfigError`s) so the YAML stays abstract today without pretending a
   capability model exists. Replacing the stub with a real table is
   `todo.md`'s job.

## Target structure

```text
engine/
  __init__.py
  config_schema.py       # SocConfig, ProbeConfig, GatewayConfig, MockConfig dataclasses
  config_parser.py         # parse_config(path) -> ResolvedConfig; validation, ConfigError
  backend_factory.py        # build_backend(soc) -> TargetGateway; "jlink" | "openocd" | "fake"
  peripheral_factory.py      # build_peripherals(mocks, soc, gateway) -> dict[str, object]
  context.py                  # TestContext, build_context(path) -- the Config Manager entry point
  registry.py                   # process-wide runtime registry: install / peripheral / gateway / reset
  test_engine.py                  # run_suite(robot_target, config_path, outdir) -> RunResult
  cli.py                            # thin argparse entry point calling run_suite()
  keywords/
    __init__.py
    core_keywords.py                # CoreKeywords(HybridCore) -- peripheral-independent: Delay, Reset
    usb_host_cdc_keywords.py         # UsbHostCdcKeywords(HybridCore) -- example keyword library
  capabilities_bridge.py               # interface-only stub, Phase 4

tests/
  engine/
    fixtures/
      minimal_fake.yaml                # single fake-backend USB host CDC mock, for parser + engine tests
    test_config_parser.py
    test_backend_factory.py
    test_peripheral_factory.py
    test_context.py
    test_registry.py
    test_engine_smoke.robot               # trivial single-Log-keyword suite for Task 2.2
    test_engine_smoke.py                   # drives run_suite() against it
    test_core_keywords.robot                # exercises Delay/Reset with zero mocks configured
    test_core_keywords.py
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
    class: cdc
    channels: 8
    device:
      vid: 0x0483
      pid: 0x5740
```

`class` names the peripheral abstractly — never a Python import path.
`peripheral_factory` resolves it in two steps:

1. `class:` → driver class, via a fixed `{"cdc": UsbHostCdcDevice, ...}`
   registry. SoC-independent: a class driver is reusable across SoCs by
   design, so this mapping never depends on `soc.part`.
2. `soc.part` + `class:` → ABI implementation. **This step is a stub, not
   a capability model.** For now it assumes every STM32 `soc.part`
   supports the USB-host CDC peripheral and always resolves to
   `Stm32OtgFsHostAbi`, raising `ConfigError` for any non-STM32 `soc.part`
   or any `class:` other than `cdc`. The real per-part compatibility
   table is a `todo.md` item — see Key decision 6 and the Non-goals
   section above.

`device:` is optional per-mock identity data the class driver presents
during enumeration (VID/PID today; descriptors and line-coding defaults
later) — kept as data, not arguments to a keyword, so it can vary per
config without touching `.robot` scripts.

### USB CDC device-mock keyword design

Despite the `usb_host` naming, `UsbHostCdcDevice` mocks the *device* side
(CDC-ACM) presented to the target's USB-host middleware — "host" describes
the target's role, not the mock's. This is the design worked out for its
keyword library, and the pattern future device classes (HID, MSC, ...)
should follow:

- **One keyword library per device class, never a generic USB library.**
  `UsbHostCdcKeywords` is what a `.robot` suite imports; USB itself is
  never a directly-importable Robot Framework library, only the
  driver/ABI resolution triggered by the `class:` field in the YAML
  config. A future HID mock gets its
  own `usb_host_hid_keywords.py`, not a shared "USB" library — the class
  is more abstract than the transport, from the keyword surface's point
  of view.
- **Four keywords, no more:** `Attach Device`, `Detach Device`,
  `Send Packet`, `Receive Packet`. Attach/Detach are external lifecycle
  events (plugging/unplugging the mock); Send/Receive are the two things
  a normal CDC application does (`bulk_in`/`bulk_out` under the hood).
- **Enumeration is not a keyword.** It isn't an independent event from
  the application's point of view — it's a mandatory, automatic
  consequence of attaching. `Attach Device` runs `UsbHostCore.attach()`
  (physical connect + port reset) and `UsbHostCdcDevice.enumerate()`
  (the SET_ADDRESS/GET_DESCRIPTOR/SET_CONFIGURATION control-transfer
  sequence) back to back; by the time the keyword returns, the mock is
  host-configured and ready for traffic. Same reasoning applies to CDC
  line-coding negotiation (GET/SET_LINE_CODING): it's a control transfer
  the host driver issues automatically, not application logic, so its
  default value is config data (alongside VID/PID above), never a
  keyword.
- **No dedicated assertion keyword.** `Receive Packet` returns the bytes
  it read; a test verifies them with Robot's own `Should Be Equal`
  rather than a peripheral-specific "verify" keyword.

## Tasks

### Phase 0 — scaffold

- [x] **0.1 — Establish a dependency manifest.** Add `pyproject.toml` (or
      `requirements.txt`, whichever matches how the project is actually
      run today — check before choosing) declaring `PyYAML`,
      `robotframework`, and `robotframework-pythonlibcore`. First
      dependencies this repo has ever declared; call that out in the PR/commit
      description so it isn't a surprise.
      Verify: a clean virtualenv install from the manifest succeeds; existing
      tests (which need none of these) still pass without it.
- [x] **0.2 — Create the `engine/` package** (flat, top-level — matching
      `target/`, `backends/`, `peripherals/` convention, not nested under a
      namespace package). Empty `__init__.py`, no logic yet.
      Verify: `import engine` works from repo root.
- [x] **0.3 — Write the config schema + example fixtures before the parser.**
      Commit `tests/engine/fixtures/minimal_fake.yaml` (shape above, using
      `backend: fake`). No parser code yet — it is the target the parser is
      built against.
      Verify: the fixture is valid YAML (`python -c "import yaml; yaml.safe_load(open(...))"`).

### Phase 1 — Config Manager

- [ ] **1.1 — `engine/config_schema.py`.** Dataclasses for `SocConfig`,
      `ProbeConfig`, `GatewayConfig`, `MockConfig`, `TimeoutConfig`.
      `MockConfig` carries `peripheral_class` (parsed from the YAML
      `class:` key — `class` is a Python keyword, so the dataclass field
      is named differently) and an optional nested `device` mapping (e.g.
      CDC's `vid`/`pid`) — untyped/passthrough at this layer so each
      driver defines its own device-identity fields without schema
      changes; see "USB CDC device-mock keyword design" above. No
      `driver`/`abi` fields — those are resolved, not configured (Task
      1.4). Pure data, no parsing or I/O.
      Verify: unit test constructs each dataclass directly, checks defaults.
- [ ] **1.2 — `engine/config_parser.py`: `parse_config(path) -> ResolvedConfig`.**
      Loads YAML, validates required fields, raises `ConfigError` with a
      specific message for each failure mode (missing `soc:`, unknown
      `backend:`, empty `mocks:`, more than one `mocks:` entry (the MMB
      structure for multiple peripherals isn't designed yet — see
      `todo.md`), unknown `class:`).
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
- [ ] **1.4 — `engine/peripheral_factory.py`: `build_peripherals(mocks, soc, gateway) -> dict[str, object]`.**
      Two-step resolution per mock: (a) `peripheral_class` → driver class
      via a fixed `{class_name: driver_path}` registry, resolved via
      `importlib` — SoC-independent; (b) `soc.part` + `peripheral_class` →
      ABI implementation. Step (b) is a stub for this plan, not a
      capability model: it assumes every STM32 `soc.part` supports the
      peripheral and always resolves `cdc` to `Stm32OtgFsHostAbi`, raising
      `ConfigError` for any other class or non-STM32 part. Constructs the
      ABI against the gateway, then the driver against the ABI, keyed by
      `name:`. Real per-part compatibility data replaces this stub later —
      tracked in `todo.md`, not this task.
      Verify: unit test with a fake gateway and the `minimal_fake.yaml`
      fixture (using `class: cdc`) asserts `build_peripherals(...)["usb_host"]`
      is the expected type and its `.abi` is wired to the fake gateway; a
      second test asserts a non-STM32 `soc.part` or unknown `class:` raises
      `ConfigError`.
- [ ] **1.5 — `engine/context.py`: `TestContext` + `build_context(path) -> TestContext`.**
      Ties 1.1–1.4 together as the single Config Manager entry point.
      Verify: end-to-end unit test, `minimal_fake.yaml` in, a `TestContext`
      out with a working fake-backed peripheral — zero RF, zero real
      hardware.
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
- [ ] **2.3 — General keyword library: `engine/keywords/core_keywords.py`.**
      `CoreKeywords(HybridCore)`, always loaded regardless of what's in
      `mocks:` — the peripheral-independent tier from the keyword-protocol
      discussion (as opposed to per-class libraries like
      `UsbHostCdcKeywords`). Exposes `Delay` (thin wrapper over
      `time.sleep`, touches no registry state) and `Reset` (calls
      `registry.reset()`). Independent of Task 2.4 — neither needs a mock
      configured or a specific peripheral driver.
      Verify: `tests/engine/test_core_keywords.robot` calls `Delay` and
      `Reset` through `run_suite()` against a config with an empty
      `mocks:` list and passes.
- [ ] **2.4 — Example keyword library: `engine/keywords/usb_host_cdc_keywords.py`.**
      `UsbHostCdcKeywords(HybridCore)` wrapping
      `peripherals.usb.host.cdc.UsbHostCdcDevice`, reading from
      `registry.peripheral("usb_host")`. Exposes exactly `Attach Device`,
      `Detach Device`, `Send Packet`, `Receive Packet` — per "USB CDC
      device-mock keyword design" above. No `Enumerate Device` keyword:
      `Attach Device` runs `attach()` + `enumerate()` internally.
      Verify: `tests/engine/test_usb_host_cdc_keywords.robot` runs these
      four keywords through `run_suite()` against a `backend: fake` config
      and passes.
- [ ] **2.5 — Add the `"fake"` backend to `backend_factory.py`.** Constructs
      the shared `FakeMmbGateway` test double (from the restructure plan's
      Task 4.2) instead of a real probe. Gate it behind an explicit
      `WCET_ALLOW_FAKE_BACKEND=1` environment check in `build_backend`, so a
      config typo can never silently run a "test" against nothing.
      Verify: `minimal_fake.yaml` works with the env var set; a unit test
      asserts `build_backend` raises without it.
- [ ] **2.6 — `RunResult` dataclass.** `run_suite()` returns pass/fail
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
- [ ] **3.3 — Keyword reference docs via `libdoc`.** Write real docstrings
      on every keyword in the general library and each per-class library
      (`UsbHostCdcKeywords`'s four keywords), then generate the HTML
      reference with `python -m robot.libdoc`. Source of truth stays the
      code — no hand-maintained keyword reference to fall out of sync.
      Verify: generated doc lists `Attach Device`, `Detach Device`,
      `Send Packet`, `Receive Packet` with their argument descriptions.
- [ ] **3.4 — YAML config reference doc.** A short field-reference table
      (name/type/required/default/meaning) for `SocConfig`/`MockConfig`,
      including the `device` block, pointing at
      `tests/engine/fixtures/minimal_fake.yaml` as the canonical example
      instead of duplicating it inline — so if the schema changes and the
      fixture doesn't, `test_config_parser.py` fails before the doc goes
      stale.
      Verify: doc references the fixture path by name; the fixture still
      parses under `parse_config` (already covered by 1.2's tests).

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
depends on the previous). Phase 2 needs all of Phase 1, except Task 2.3
(general keywords) only needs 2.1/2.2 and is independent of 2.4 (CDC
keywords) — either can be built first, or in parallel. Phase 3 needs Task
2.2 and 2.6 for 3.1/3.2; Task 3.3 additionally needs 2.3 and 2.4 (the
keyword docstrings it generates from); Task 3.4 only needs Phase 1 (the
fixture and schema) and can run any time after that, in parallel with
everything else in Phase 3. Phase 4 tasks are independent of each other and
of Phase 3 — either
can run first, or in parallel with Phase 3.
