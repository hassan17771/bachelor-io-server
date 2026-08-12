# TODO

Open design gaps from the Layer 2 design session (see
`docs/arch/io-server-architecture.md`).

## Layer 2 wiring
- [ ] Config Manager: pure-Python, parses SoC + Mock List YAML -> `TestContext`
- [ ] Test Engine: drive `robot.run()` in-process (not CLI) for live-object injection
- [ ] Runtime registry: zero-arg keyword libs read live instances from it
- [ ] Keyword libs must call only abstract peripheral interfaces, never concrete ABIs

## Probe connection
- [ ] Direct probe connection is schema-only. `probe.connection: direct`
      parses (with `serial:` selecting among attached probes) but
      `build_backend` raises `NotImplementedError` — both backends are
      currently clients of an already-running `JLinkGDBServer` / OpenOCD
      daemon, and nothing in this repo launches or drives a probe over USB
      itself. Implementing it means either a native driver backend (pylink,
      pyocd) or having the engine spawn the server with
      `-select USB=<serial>`; that choice is the open question
- [ ] `serial:` therefore has no effect in `connection: server` mode and is
      rejected there rather than ignored — revisit if the engine ever gains
      server-launching, which would make serial meaningful in both modes

## Multi-peripheral concurrency
- [ ] Resolve the MMB structure: how multiple peripherals share the
      memory-mapped block (fixed slots? descriptor table? per-peripheral
      base+size negotiation?) is not designed. Until it is, the Config
      Manager accepts exactly one `mocks:` entry per config and rejects
      more; address-collision validation only becomes meaningful (and
      possible) once this design exists
- [ ] No locking around shared `TargetGateway` for concurrent peripherals
- [ ] No pump/scheduling model for servicing peripherals concurrently

## SoC generality
- [ ] Capabilities not wired to RF skip/tags yet (`target/capabilities.py`)
- [ ] Timeouts should be config-driven per SoC/backend, not hardcoded in scripts
- [ ] No real SoC/peripheral-class → ABI compatibility table; Config Manager's
      `class:` resolution (test-engine-config-yaml.md Task 1.4) currently
      stubs this as "every STM32 `soc.part` supports `cdc` via
      `Stm32OtgFsHostAbi`" — replace with a real per-part lookup once more
      SoCs/ABIs exist

## Modularity
- [x] CDC/ABC split is convention-only, unenforced — add an import-linter/arch test
      (done: tests/test_architecture_boundaries.py)

## USB device composition
- [ ] Mock config assumes one `class:` per mock entry (test-engine-config-yaml.md's
      `mocks:` shape); a single physical USB host/port exposing multiple
      classes at once (composite device, e.g. CDC+HID) isn't representable
      yet — one mock entry can't currently name more than one class
- [ ] `UsbHostCdcKeywords` hardcodes a single `registry.peripheral("usb_host")`
      lookup instead of taking the mock's `name:` as a keyword argument
      (contrast with the GPIO keyword design below). Fine while only one
      usb_host-class mock/device is ever configured; accepted as a known
      inconsistency for now. Retrofit to the name-as-first-argument pattern
      once attaching multiple simultaneous USB-host devices is needed.

## USB host MMB access
- [ ] `Stm32OtgFsHostAbi` spends several single-field GDB round trips per
      transfer where one bulk read would do: `channel_info()`'s three
      `read_u8` calls (peripherals/usb/host/backends/stm32_otgfs.py:129-131)
      are 3 contiguous bytes of the 12-byte channel header, and `validate()`'s
      three reads (same file, lines 76-78) sit within the 16-byte block
      header. Each round trip pays real J-Link/SWD/USB latency (why
      `enumerate()`'s default timeout is 60s, peripherals/usb/host/cdc.py);
      batching these into single `read()` calls and unpacking locally would
      cut round trips per transfer roughly in half on real hardware.

## CLI, docs, and capability bridge (deferred from test-engine-config-yaml.md)
Phases 3 and 4 of `docs/plan/test-engine-config-yaml.md` were taken out of
scope on 2026-08-07; these tasks are the ones they contained, to be picked
up as their own plan once that plan's Phase 2 lands.
- [x] `engine/cli.py`: thin `argparse` entry point (`--robot`, `--config`,
      `--outdir`) calling `run_suite()`
- [x] End-to-end usage note: exact command line for the fake-backend and
      hardware invocations of `run_suite()`
- [ ] Keyword reference docs via `python -m robot.libdoc`, generated from
      real docstrings on `CoreKeywords` and `UsbHostCdcKeywords`
- [ ] YAML config reference doc: field-reference table for
      `SocConfig`/`MockConfig`, pointing at `tests/engine/fixtures/minimal_fake.yaml`
- [ ] Config-driven timeouts: thread the optional `timeouts:` block
      (`TimeoutConfig`) into peripheral construction, replacing hardcoded
      defaults
- [ ] Capability bridge stub: `engine/capabilities_bridge.py`, interface
      translating a (not-yet-defined) per-mock capability set into Robot
      Framework `--include`/`--exclude` args — wiring only, no capability
      model (see "SoC generality" above)

## GPIO simulation
- [ ] Add GPIO digital in/out peripheral simulation. No driver/ABI/keyword-
      library code exists yet — this is Layer 3/4 (`peripherals/`) + Layer 2
      (`engine/keywords/`) work, not scoped into
      test-engine-config-yaml.md. Keyword protocol agreed: two classes,
      `gpio_in` (mock drives -> target reads, `Set Pin` keyword) and
      `gpio_out` (target drives -> mock reads, `Read Pin` keyword); pin
      identity as YAML mock data (`pin: PA0`, optional `initial:`); same
      `class:` -> driver / `soc.part` + `class:` -> ABI resolution pattern
      as USB CDC. Keywords take the mock's registry `name:` as their first
      argument (`Set Pin    <name>    HIGH`) since multiple pins are
      normally active at once — unlike USB CDC's current single-instance
      shortcut above.

## STM32Cube firmware extension tooling
- [ ] Wiring CAN simulation into `f103-cansim` (docs/plan/stm32-can-support.md,
      Phase 0) was entirely manual CubeIDE surgery: hand-write the
      `__wrap_HAL_CAN_*` forwarder file (`Core/Src/can_hal_sim_wrap.c`) against
      the vendor HAL's prototypes, then hand-edit `.cproject` (Eclipse CDT
      XML) to add a new "Simulation" `<cconfiguration>` with its own defines
      (`-DCAN_SIMULATION`), the matching `-Wl,--wrap=HAL_CAN_*` linker flags
      one per wrapped function, a `<scannerConfigBuildInfo>` block, and a
      source-exclusion filter on Debug/Release. None of that generalizes:
      the next peripheral (GPIO, above) or the next STM32Cube SDK/part repeats
      the same hand-editing from scratch, and CDT XML has sharp edges with no
      documented precedent (e.g. `excluding=` paths are project-root-relative,
      not relative to the entry — only discovered empirically this session by
      inspecting a generated `subdir.mk`).
- [ ] Needed: a generalized tool that, given an STM32CubeMX/CubeIDE project
      and a target HAL module (its header's function list), can (a) generate
      the `__wrap_<fn>`/`<fn>_Sim` forwarder skeleton, and (b) create or patch
      the simulation build configuration (`.cproject` defines, `--wrap` flags,
      source exclusions, scanner config) programmatically instead of by hand.
      Goal: enabling sim support for a new peripheral or a new SDK/part is a
      command, not a repeat of this session's manual XML edits. Scope,
      approach (CDT-XML templating vs. driving CubeMX/CubeIDE headlessly vs.
      moving off CubeIDE project files entirely), and how it fits the plan
      process are all open — not yet designed.
t