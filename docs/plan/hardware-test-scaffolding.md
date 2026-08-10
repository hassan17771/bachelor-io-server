# Plan: refactor the hardware-test scaffolding

One entry in [`refactor_plan.md`](refactor_plan.md).

Status: **proposed — not started.** This document states a problem and a
direction; the task list at the end is a sketch, not an approved design.

## Why

`tests/support/hardware.py` is the only shared entry point for running tests
against real hardware. Its name, its location and its docstring all present it
as general infrastructure — "shared bring-up for the opt-in hardware tests" —
but it is not general. It is a single hard-coded path to one USB-host
application, on one SoC family, over one debug probe, resolved out of one
firmware build tree that is not even part of this repository.

The result is that "run a test against hardware" is not a capability this repo
offers. What it offers is "run *these* USB-host tests against *that* board".
Anything else — a different peripheral class, a different SoC, OpenOCD instead
of J-Link, a firmware build laid out differently — has to bypass the module
completely and re-implement bring-up from scratch. That is the opposite of
what shared test support is for, and it will only get more expensive as
`peripherals/` grows the extra classes and SoC backends it was restructured to
accommodate.

## What is actually wrong

Everything below is in `tests/support/hardware.py` today.

1. **It is USB-host-on-STM32-OTG_FS specific, under a generic name.**
   `UsbHostHardwareTest.setUp` imports and constructs
   `Stm32OtgFsHostAbi`, `DEFAULT_USBH_SIM_LAYOUT`, `resolve_usbh_sim_base()`,
   `USBH_SIM_MAGIC` and `OTG_FS_IRQn` directly. The peripheral, the SoC family
   and the memory layout are baked into the base class, so there is no
   inheritable "connect to the board" step that a non-USB test could reuse.

2. **It is hard-wired to J-Link.** `setUp` calls
   `JLinkBackend(jlink_config_from_env()).connect()`. `backends/` supports
   OpenOCD and `engine/backend_factory.py` exists precisely to pick a backend
   from config, but no hardware test can reach either. `monitor_command("go")`
   in `_ensure_running` is a J-Link-only escape hatch on top of that.

3. **It is coupled to one exact firmware build of one exact application.**
   `resolve_appli_state` searches `blackUSB.elf` / `blackUSB.map` under
   `Release/` and `Debug/` directories of the *parent of this repository*
   (`_PROJECT`), and its failure message hardcodes two literal addresses
   (`0x200000f4`, `0x200004cc`) for those two builds. The
   `APPLICATION_IDLE/START/READY/DISCONNECT` constants are copies of an enum in
   the application's `usb_host.c`. A test-support module in this repo should
   not be doing filesystem archaeology on a sibling project.

4. **It compensates in test code for a firmware build flag.** `_enable_irq`
   pokes `NVIC_ISER` because, as its comment says, the simulation build skips
   `HAL_HCD_MspInit` and so never enables `OTG_FS_IRQn`. `_wait_for_magic`
   fails with "is the firmware built with `-DUSB_HOST_SIMULATION` and
   flashed?". The scaffolding silently depends on a firmware variant it cannot
   check for.

5. **Configuration is env-var soup that duplicates the config schema.** Ten
   `WCET_*` variables are decoded by three ad-hoc helpers (`env_truthy`,
   `env_int`, `env_float`). `hil_config_yaml()` then *re-emits some of those
   same variables as YAML by string concatenation* so the engine tests can
   consume them, which means the board's coordinates now have two independent
   representations that must be kept in agreement by hand. The YAML schema in
   `engine/config_schema.py` already exists to describe a board; this predates
   it and was never folded in.

6. **It is `unittest` in a pytest tree, with class flags instead of
   fixtures.** `RESET_ON_SETUP`, `WAIT_FOR_MAGIC` and `RESOLVE_APPLI_STATE`
   are a fixed, ordered bring-up sequence with boolean switches. Composable
   fixtures express the same thing without a base class that every hardware
   test must inherit from.

7. **`MmbView` is imported from `tests/support/fake_gateway.py` to drive real
   hardware.** `MmbView` is a layout-aware accessor over any gateway, not a
   fake; it is simply filed in the wrong module, and that misfiling forces the
   hardware path to import from a module named for the opposite purpose.

8. **`env_truthy` is trapped in `tests/`.** Production code that wants the same
   "is this opt-in flag set" semantics cannot import it without `engine/`
   depending on the test tree, so the semantics get re-implemented slightly
   differently each time.

## Direction

Not a design, a direction — the shape the replacement should have:

- **Hardware tests should acquire a target the way production does.** A
  hardware test names a config file and calls the engine's `build_context()`;
  it does not choose or construct a backend itself. That deletes items 2 and 5
  together, because the config file *is* the board description.
- **Separate the three things the module currently fuses**: (a) generic
  session bring-up — opt in, connect, tear down; (b) peripheral-class bring-up
  — MMB layout, magic, IRQ enable; (c) application/firmware knowledge —
  `Appli_state`, application state enum, build locations. Only (a) belongs in
  a module called `hardware`. (c) should be data supplied by whoever runs the
  test, not code committed here.
- **Make the firmware contract explicit rather than assumed.** The
  simulation-build requirement, the symbol it needs and where the build lives
  are inputs to be declared and checked, not defaults to be guessed.
- **Move the pieces that are already general to where they are usable**:
  `MmbView` out of `fake_gateway.py`, `env_truthy`/`env_int`/`env_float` out
  of `tests/`.

### Non-goals

- Not a rewrite of the existing USB-host hardware tests' *content*. They keep
  testing what they test; only how they reach the board changes.
- Does not add a second SoC backend or a second peripheral class. The point is
  to stop blocking them, not to write them.
- Does not change the firmware.

## Tasks (sketch)

- [ ] **1 — Relocate the genuinely general pieces.** `MmbView` out of
      `tests/support/fake_gateway.py`; the `env_*` decoders out of
      `tests/support/hardware.py` into a module importable from `engine/`.
      Verify: existing test suites pass unchanged after the moves.
- [ ] **2 — Config-driven target acquisition for hardware tests.** A fixture
      that yields a connected gateway built from a config file, replacing
      `jlink_config_from_env()` and the direct `JLinkBackend` construction.
      Verify: an existing hardware test runs through it against the board, and
      the same test skips cleanly with no board attached.
- [ ] **3 — Delete `hil_config_yaml()`.** With task 2 in place the YAML is an
      input, not something generated from env vars at runtime.
      Verify: `tests/hardware/engine/` passes with a checked-in config file.
- [ ] **4 — Split peripheral bring-up out of session bring-up.** USB-host
      specifics (layout, magic wait, `OTG_FS_IRQn`) become a separate opt-in
      fixture layered on task 2, not `setUp` steps in a base class.
      Verify: the USB-host hardware tests pass using it; a test that needs
      only a connected target can be written without touching any of it.
- [ ] **5 — Push application/firmware knowledge out of the repo.**
      `Appli_state` resolution, the application state enum and the build-tree
      search become declared inputs.
      Verify: the tests that need `Appli_state` pass when it is declared, and
      fail with a clear message naming what is missing when it is not.
- [ ] **6 — Re-verify the bring-up now that the MMB base comes from the ELF.**
      `tests/support/hardware.py` and the `diag_*` scripts read the address out
      of `WCET_FIRMWARE_ELF` instead of a compiled-in default, and nothing has
      run against a board since. Note the F411 firmware still publishes ABI
      version 1, so those tests fail the version check until it is rebuilt.
      Verify: the USB-host hardware tests reach the board with
      `WCET_FIRMWARE_ELF` pointing at its build.
