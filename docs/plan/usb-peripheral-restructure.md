# Plan: USB / peripheral / firmware-ABI folder restructure

Status: proposed, not started. Companion to
[`test-engine-config-yaml.md`](test-engine-config-yaml.md) (that plan consumes
the module paths this one produces — see its "Depends on" note).

## Why

`peripherals/` and `firmware_abi/` currently hold two USB-related
implementations:

- `peripherals/usb_host.py` + `peripherals/usb_host_otgfs.py` +
  `firmware_abi/usbh_otgfs.py` — the MCU acts as **USB host**; the IO server
  mocks a CDC-ACM **device** through an MMB shim in firmware RAM. This is the
  pattern the architecture doc describes, and the only one that works.
- `peripherals/usb2.py` + `firmware_abi/usb2.py` — an earlier attempt at the
  MCU acting as a USB **device** (classic F1-style USB FS peripheral,
  register/PMA-level), with the IO server mocking a host. **This is
  deprecated and non-functional.** It is being deleted outright, not
  restructured or migrated into the new layout.

**The IO server currently supports USB-host mode only.** This plan reshapes
that implementation so it can grow — more USB classes (MSC, HID, ...), more
SoC backends — without copy-pasting a whole module per addition.

### Non-goals

- No new USB classes (MSC, HID, ...) are implemented here — this plan only
  makes them cheap to add later.
- No new SoC backend is implemented here (no second concrete ABI is written)
  — the structure is proven by moving the existing STM32 one into it.
- **`usb2.py` (device-mode) is eliminated, not integrated.** No abstract
  device-role contract, no `device/` subtree, no migration path for it — it
  is deleted along with its tests and references. If device-mode mocking is
  wanted again later, it gets designed and built fresh against whatever the
  IO server looks like at that time, not resurrected from this code.
- Does not resolve the multi-peripheral concurrency/locking gap or the
  capability→RF-tag bridge tracked in `todo.md` — those are Layer 2 /
  cross-cutting concerns, not folder structure.
- Does not touch `target/` or `backends/` (the J-Link/OpenOCD/TargetGateway
  layer) — only `peripherals/`, `firmware_abi/`, and `tests/`.

## Key decisions

1. **Eliminate `usb2.py` first, before any restructuring.** It's dead code
   with no working path forward; removing it up front shrinks the surface
   the rest of this plan has to reason about (fewer files to grep, no
   "should this duplication-removal also cover usb2" questions).
2. **Keep the `usb/host/` nesting even though there is no `usb/device/`
   sibling today.** Costs one directory level and mirrors naming already in
   the code (`UsbHostSimAbi`, `UsbHostCore`, `UsbHostCdcDevice` all say
   "Host" in their own names) — it documents that this is host-mode-specific
   without inventing new abstraction. If this feels like unneeded nesting,
   the alternative is flattening to `peripherals/usb/` directly; flag if you
   want that instead.
3. **Protocol-generic USB constants get their own module, split out of
   `usb_host.py`.** Not because anything is duplicated against usb2 anymore
   (it's gone) — `usb_host.py` today mixes genuinely class-agnostic
   constants (endpoint types, speeds, chapter-9 request/descriptor codes,
   `bmRequestType` bits) with CDC-specific ones (`CDC_GET_LINE_CODING`,
   descriptor byte blobs) in one file. Splitting them is what lets a future
   MSC/HID class driver reuse the generic half without reaching into CDC's
   file.
4. **No compatibility shims.** Every move is a clean move: update the
   importers in the same task, delete the old file, run the full suite. This
   repo has no external consumers of these modules, so there's nothing a
   shim would protect. Same rule applies to the usb2 deletion — no
   deprecation warnings or re-export stubs, just remove it.

## Target structure

```text
peripherals/usb/
  common/
    __init__.py
    requests.py        # USB 2.0 ch.9 standard request + descriptor-type constants,
                        # endpoint type/direction/speed enums -- class-agnostic
  host/
    __init__.py
    abi.py               # UsbHostSimAbi (ABC) -- pipe-level mechanics contract
    core.py                # UsbHostCore -- generic control-transfer engine, bulk/intr primitives
    cdc.py                  # UsbHostCdcDevice(UsbHostCore) -- CDC class driver + its own protocol constants
    backends/
      __init__.py
      stm32_otgfs.py          # Stm32OtgFsHostAbi(UsbHostSimAbi)

firmware_abi/usb/
  host/
    stm32_otgfs.py       # was firmware_abi/usbh_otgfs.py, moved as-is
```

`peripherals/usb2.py`, `firmware_abi/usb2.py`, and `tests/test_usb2_cdc_jlink.py`
have no replacement in the target structure — they are deleted, not moved.

## Tasks

Each task is meant to be one commit: small, scoped to the files it names, and
green on the full test suite before the next task starts. "Verify" is the
acceptance check for that task, not a suggestion.

### Phase 0 — safety net

- [ ] **0.1 — Add a root `conftest.py`.** Puts the repo root on `sys.path`
      once, centrally, instead of each test file doing its own
      `sys.path.insert` dance. No behavior change.
      Verify: `pytest` from repo root still collects and runs every existing
      test file, including the ones under the hyphenated `tests/usb-host/`.
- [ ] **0.2 — Make hardware tests opt-in and skip by default.** Several
      existing tests (`test_usb_host_enumerate_4_1.py`, ...) connect to a
      real J-Link/target in `setUp()` unconditionally — some have the
      `WCET_*_TEST=1` skip guard already written but commented out.
      Uncomment/standardize it across all hardware-requiring test files
      that remain after Phase 1's deletions.
      Verify: `pytest` with no environment variables set is fully green on a
      machine with no probe or target attached; setting the matching env var
      still runs the hardware test as before. This is the safety net every
      later task's "run the full suite" check depends on.

### Phase 1 — eliminate `usb2` (deprecated, non-functional device-mode code)

- [ ] **1.1 — Delete the implementation files.** Remove
      `peripherals/usb2.py` and `firmware_abi/usb2.py`.
- [ ] **1.2 — Delete the test file.** Remove `tests/test_usb2_cdc_jlink.py`.
- [ ] **1.3 — Remove the `.vscode/launch.json` entry** that runs
      `tests.test_usb2_cdc_jlink` (line 51 today).
- [ ] **1.4 — Clean up the re-exports.** `peripherals/__init__.py` currently
      imports `Usb2CdcMockHost, Usb2Simulator` from `.usb2` and lists them in
      `__all__`; `firmware_abi/__init__.py` currently imports
      `DEFAULT_USB2_SIM_CONFIG, EnumerationResult, Usb2Packet, Usb2SimConfig,
      Usb2SimLayout, Usb2SimStatus, UsbIstr, UsbSetupPacket` from `.usb2` and
      lists them in `__all__`. Remove both.
      Verify: `grep -rIn "usb2\|Usb2\|USB_SIMULATION" .` (excluding
      `docs/plan/` and `.git/`) finds nothing; full suite green.

### Phase 2 — host-mode restructuring

- [ ] **2.1 — `peripherals/usb/common/requests.py`.** Move the
      protocol-generic constants out of `usb_host.py`: `DIR_OUT`/`DIR_IN`,
      `EP_TYPE_*`, `SPEED_*`, `USB_REQ_GET_DESCRIPTOR`/`SET_ADDRESS`/
      `SET_CONFIGURATION`, `DESC_DEVICE`/`CONFIGURATION`/`STRING`,
      `REQ_DIR_DEVICE_TO_HOST`, `REQ_TYPE_MASK`/`STANDARD`/`CLASS`. CDC-only
      constants (`CDC_GET_LINE_CODING` and friends, the descriptor byte
      blobs) stay behind — they move with the CDC driver in Task 2.4.
      Verify: full suite green; constant values unchanged.
- [ ] **2.2 — `peripherals/usb/host/abi.py`.** Move `UsbHostSimAbi` (ABC),
      `UsbOutPacket`, `UsbChannelInfo`, `UsbHostSimStatus`, `UsbHostSimError`
      out of `usb_host.py` verbatim. Update `usb_host_otgfs.py`'s imports.
      Verify: full suite green (pure move).
- [ ] **2.3 — `peripherals/usb/host/core.py`: `UsbHostCore`.** Extract the
      protocol-generic half of `UsbHostCdcDevice`: `attach()`, the
      SETUP-poll-and-dispatch loop (today inlined in `enumerate()`,
      generalized to `service_control_transfers(responder, done, timeout_s)`
      where `responder` answers a decoded SETUP with reply bytes), the
      status-stage helper, and new thin `bulk_out`/`bulk_in` wrappers around
      `ack_out`/`provide_in`/`wait_for_out`.
      Verify: new unit tests (via the existing `FakeMmbGateway` double)
      exercise the control-transfer engine with a stub `responder`, proving
      it needs no CDC knowledge to pass.
- [ ] **2.4 — `peripherals/usb/host/cdc.py`: `UsbHostCdcDevice(UsbHostCore)`.**
      What's left after 2.1/2.3: CDC request constants, profile/descriptor
      defaults, `_respond()`, `wait_and_echo()` rebuilt on
      `Core.bulk_out`/`bulk_in`. Highest-risk task in this plan — behavior
      must be byte-for-byte unchanged.
      Verify: every existing CDC test (`test_usbh_conf_sim.py`,
      `test_usbh_conf_urb_sim.py`, `test_usb_host_abi_3_1.py`,
      `test_usb_host_abi_3_2.py`, `test_usb_host_connection.py`, plus the
      opt-in hardware tests `test_usb_host_enumerate_4_1.py` /
      `test_usb_host_echo_4_2.py`) passes unmodified in behavior.
- [ ] **2.5 — Move `usb_host_otgfs.py` → `peripherals/usb/host/backends/stm32_otgfs.py`,
      and `firmware_abi/usbh_otgfs.py` → `firmware_abi/usb/host/stm32_otgfs.py`.**
      Pure move + import updates.
      Verify: full suite green.
- [ ] **2.6 — Delete `peripherals/usb_host.py`.** Everything in it has moved
      to 2.1–2.4. Update `peripherals/__init__.py` / `firmware_abi/__init__.py`
      re-exports to the new paths.
      Verify: full suite green; `grep -rn "usb_host\.py\|usbh_otgfs\.py"`
      (excluding this plan and git history) finds nothing.

### Phase 3 — tests: reorganize to match

- [ ] **3.1 — Rename `tests/usb-host/` → `tests/usb/host/`.** The hyphen
      made it unimportable, forcing every test file to hand-roll a
      `sys.path` insert; a proper package (`__init__.py`) plus the Phase 0
      `conftest.py` removes that boilerplate.
      Verify: full suite green under the new path.
- [ ] **3.2 — Promote the fake gateway double.** Move
      `FakeMmbGateway`/`MmbView` out of the test-local file into
      `tests/support/fake_gateway.py`. `MmbView`'s layout-driven field
      access is already generic; only the import path changes.
      Verify: full suite green; Phase 2's new unit tests (Task 2.3) import
      from the new shared location.
- [ ] **3.3 — Rename the numbered/staged test files to behavior names**
      matching the new module boundaries — e.g. `test_usb_host_abi_3_1.py` /
      `test_usb_host_abi_3_2.py` → `tests/usb/host/backends/test_stm32_otgfs.py`;
      `test_usb_host_enumerate_4_1.py` → `tests/usb/host/test_cdc_enumerate.py`;
      `test_usb_host_echo_4_2.py` → `tests/usb/host/test_cdc_echo.py`. Split
      unit tests (no hardware) from hardware tests by directory
      (`tests/usb/...` vs `tests/hardware/usb/...`) rather than by marker —
      simpler to reason about with no pytest config file today.
      Verify: full suite green; file names describe behavior, not
      chronology.
- [ ] **3.4 — Add a `UsbHostCore`-only test proving the split worked.** A
      unit test that drives `service_control_transfers` with a stub
      `responder` that knows nothing about CDC. This is the test that
      demonstrates Phase 2 achieved real decoupling, not just "nothing
      broke."

### Phase 4 — enforce the boundary

- [ ] **4.1 — Add an architecture/boundary test**
      (`tests/test_architecture_boundaries.py`, or an `import-linter`
      config) asserting `usb/host/core.py` and `usb/host/cdc.py` never
      import `usb/host/backends/*`, and `usb/common/*` never imports
      `usb/host/*`. Closes the `todo.md` item "CDC/ABC split is
      convention-only, unenforced."
      Verify: the test fails if a deliberate throwaway violation is added,
      then passes once removed (write the test, prove it catches the
      violation, revert the violation).

### Phase 5 — documentation

- [ ] **5.1 — Update `docs/arch/peripheral-simulation-backend-architecture.md`.**
      Refresh the "Package shape" section for the new tree, and state
      plainly that USB-host mode is the only supported USB mechanism today
      — remove or correct anything that could be read as implying
      device-mode mocking exists or is planned.

## Order

Phases are strictly sequential: 0 (safety net) → 1 (delete usb2, shrinks
what later greps/checks have to consider) → 2 (host-mode restructuring,
depends on 1 being done so there's no lingering usb2 duplication question)
→ 3 (tests, depends on 2's new module paths) → 4 (depends on 2 and 3 both
existing) → 5 (documents the end state). Within Phase 2, tasks are ordered
top-to-bottom.
