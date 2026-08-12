# Plan: CAN (bxCAN) peripheral simulation

Status: not started.

## Why

The IO server currently mocks one peripheral family: USB-host (CDC-ACM), via
an MMB in firmware RAM that the host drives over `TargetGateway`
([io-server-architecture.md](../arch/io-server-architecture.md)). This plan
adds a second: CAN, using the same MMB/gateway model, verified against
`f103-cansim` (STM32F103 "Blue Pill", see that project's `CAN_DRIVER.md` and
`README.md` for the real two-board bring-up scenario it already implements).

The seam differs from USB's. USB's host middleware ships its own
board-specific porting layer (`usbh_conf.c`'s `USBH_LL_*` functions), so
`f401-USBsim` swaps that layer's implementation via a link-time `--wrap`
(see `usbh_ll_sim.c` / `usbh_ll_sim_wrap.c` there — not `usbh_conf.c`
dispatch, despite that file's own header comment; confirmed by reading the
actual `.cproject` linker flags). bxCAN's HAL has no such porting layer —
application code calls `HAL_CAN_Init/ConfigFilter/Start/ActivateNotification/
AddTxMessage/GetRxMessage` and `HAL_CAN_IRQHandler` directly. This plan
applies the same `--wrap` mechanism straight to those seven functions.

## Non-goals

- **Register-level peripheral emulation** (repoint `CAN1` at the MMB so
  unmodified vendor `stm32f1xx_hal_can.c` runs against it directly).
  Investigated and rejected: `HAL_CAN_Init`/`Start`/`Stop` poll `MSR.INAK`/
  `SLAK` with a hardcoded 10 ms timeout (`stm32f1xx_hal_can.c:237`,
  `:335-374` and others) immediately after setting the request bit, with no
  signal the IO server can synchronize a J-Link round trip against — a race
  against a vendor-controlled deadline it cannot reliably win. Tracked as a
  TODO (Task 4.3) for a future peripheral whose registers are mostly passive
  rather than reactive (e.g. the GPIO mock already sketched in `todo.md`),
  not for bxCAN.
- **More than one CAN mock/node per run.** Same limit USB already has —
  `todo.md`'s "Resolve the MMB structure" item covers multi-peripheral
  sharing generally; this plan doesn't touch it.
- **A second SoC/board actually implemented.** Only STM32F103 (`f103-cansim`)
  is built here. The architecture is kept part-agnostic (Key decision 5) so
  adding one later is additive, but doing so is out of scope.
- **Extended (29-bit) IDs and remote frames (RTR).** The wire format reserves
  room for both so they're additive later, but this plan only exercises
  standard data frames, matching `f103-cansim`'s current application code.
- Does not touch `peripherals/usb/` or `firmware_abi/usb/` beyond the two
  shared `engine/` files both peripheral families depend on.

## Key decisions

1. **Seam: GNU ld `--wrap` on the seven `HAL_CAN_*` functions application
   code already calls, not a new indirection layer and not vendor-file
   replacement.** `-Wl,--wrap=<name>` redirects every *caller* of `<name>`
   to `__wrap_<name>`; the vendor `.c` file and every `USER CODE` block in
   `f103-cansim` stay untouched. Mirrors `f401-USBsim`'s
   `usbh_ll_sim.c`/`usbh_ll_sim_wrap.c` split exactly.
2. **`HAL_CAN_MspInit` must be invoked explicitly from the wrap.** Once
   `HAL_CAN_Init` is wrapped, the real `HAL_CAN_Init` body — which calls
   `HAL_CAN_MspInit` on real hardware, and is what enables the
   `USB_LP_CAN1_RX0_IRQn` NVIC line the Rx path depends on — is unreferenced
   dead code. `__wrap_HAL_CAN_Init` calls `HAL_CAN_MspInit` itself, after
   `HAL_CAN_Init_Sim`. Same fix `usbh_ll_sim_wrap.c` needed for `OTG_FS_IRQn`.
3. **New CubeIDE build configuration, "Simulation"; Debug/Release untouched.**
   The real two-board firmware keeps building exactly as it does today;
   simulation sources and `--wrap` flags apply to one opt-in configuration.
4. **Flatter Python package than USB — no Core/ClassDriver split.** USB
   separates `core.py` (protocol-generic control-transfer engine) from
   `cdc.py` (class driver) because USB has host/device asymmetry and several
   device classes. CAN has neither: one bus, one frame shape, no
   enumeration. `peripherals/can/bus.py` carries the whole intent layer.
5. **Part-agnostic by construction.** `CanSimAbi` and `CanBus` contain no
   STM32 detail; `Stm32BxcanAbi` takes its IRQ number as a constructor
   argument (mirrors `Stm32OtgFsHostAbi`'s existing `irqn` parameter) instead
   of hardcoding `USB_LP_CAN1_RX0_IRQn`, and `peripheral_factory`'s `can`
   branch resolves it from a small `part -> irqn` table. Adding a second
   board later is one table row (plus a new concrete ABI only if its MMB
   offsets differ) — zero changes to `abi.py`, `bus.py`, or the keywords.
   Enforced the same way USB enforces its own boundary: extend
   `tests/test_architecture_boundaries.py` so `peripherals/can/{abi,bus}.py`
   never import `peripherals/can/backends/*`.
6. **`resolve_mmb_base()` becomes class-keyed.** It currently hardcodes
   `USBH_SIM_SYMBOL` ([engine/context.py:45](../../engine/context.py#L45)) —
   the only MMB symbol that has ever existed. With two peripheral families it
   resolves the symbol for whichever class the run's `mocks:` entry names,
   via a small `class -> symbol` map next to `peripheral_factory._DRIVERS`.
   Required by this plan, not new scope.
7. **Frame slots are ID-agnostic.** The MMB moves raw frames (ID/IDE/RTR/
   DLC/data); it has no notion of `CAN_ID_COMMAND`/`CAN_ID_STATUS` or of
   `ROLE_DEVICE`/`ROLE_TESTER` — those stay in `f103-cansim`'s application
   code. This is what makes the same mock usable by a future
   application/board without touching the protocol.
8. **Filter fidelity via replay, not reimplementation.**
   `HAL_CAN_ConfigFilter_Sim` stores the real `CAN_FilterTypeDef` fields the
   app already computed and applies the same mask arithmetic real silicon
   does before delivering an injected frame — a wrong filter drops a frame
   in simulation the same as on the real bus.
9. **3 Tx mailboxes, 1 pending Rx slot.** Matches real bxCAN's mailbox count
   (`HAL_CAN_AddTxMessage`'s "no mailbox free" path behaves the same in both
   builds); Rx starts single-slot since nothing in `f103-cansim` bursts
   multiple frames before consuming one — revisit if a later scenario needs
   depth.

## Target structure

```text
f103-cansim/
  Core/Inc/can_sim.h                 # CanSimBlock layout, magic/version, CAN_SIM_SYMBOL name
  Core/Src/can_sim.c                  # can_sim instance (.bss), can_sim_init()
  Core/Inc/can_hal_sim.h                # HAL_CAN_*_Sim prototypes, mirrors usbh_ll_sim.h's pairing
  Core/Src/can_hal_sim.c               # HAL_CAN_*_Sim -- MMB mechanics, no NVIC/IRQ concerns
  Core/Src/can_hal_sim_wrap.c           # __wrap_HAL_CAN_* -- link-time glue, Simulation config only
  Tests/test_can_hal_sim.c              # host-only unit test for can_hal_sim.c, no target/CubeIDE build

wcet-io-server/
  firmware_abi/can/
    stm32_bxcan.py                    # CanSimBlock offsets/magic/CAN_SIM_SYMBOL, mirrors can_sim.h
  peripherals/can/
    abi.py                            # CanFrame, CanSimAbi (ABC)
    bus.py                            # CanBus -- send_frame() / receive_frame()
    backends/
      stm32_bxcan.py                   # Stm32BxcanAbi(CanSimAbi)
  engine/
    keywords/can_keywords.py           # Send CAN Frame / Receive CAN Frame
    peripheral_factory.py              # + "can" -> CanBus / Stm32BxcanAbi, part->irqn table  [extended]
    context.py                         # resolve_mmb_base() becomes class-keyed                [extended]
  tests/
    can/...                           # unit tests, FakeMmbGateway, no hardware
    hardware/can/...                   # opt-in J-Link tests (WCET_CAN_TEST=1)
    hardware/fixtures/f103_can_jlink.yaml
    test_architecture_boundaries.py    # + can/{abi,bus}.py must not import can/backends/*      [extended]
  docs/arch/
    can-bxcan-simulation.md            # peer to usb-host-simulation.md
```

## Tasks

### Phase 0 — firmware: the MMB and the wrap

Firmware first: the `--wrap` mechanism is unproven in this repo (USB uses a
different seam), so it needs validating against real linker/build behavior
before any Python is written against an assumed contract.

- [x] **0.1 — `can_sim.h`/`can_sim.c`.** `CanSimBlock`: header
      (magic/version/block_size) + `tx[3]` mailbox slots (firmware-owned) +
      one `rx` slot (server-owned) + the stored filter config. `_Static_assert`
      the size, matching `usbh_sim.h`'s pattern.
      Verify: builds standalone; `sizeof(CanSimBlock)` matches the header's
      own doc comment.
- [x] **0.2 — `can_hal_sim.c`/`can_hal_sim.h`: the seven `HAL_CAN_*_Sim`
      functions.** `Init_Sim`/`ConfigFilter_Sim` (store filter, bank
      bounds-checked against `CAN_SIM_NUM_FILTERS`)/`Start_Sim`/
      `ActivateNotification_Sim`/`AddTxMessage_Sim` (fill a free `tx[]` slot,
      `HAL_ERROR` if all 3 pending)/`GetRxMessage_Sim` (copy from `rx`, gated
      on the FIFO the matching filter routed to)/`IRQHandler_Sim` (apply the
      matching filter's ID/mask arithmetic to `rx`, deliver to
      `HAL_CAN_RxFifo0MsgPendingCallback` or `RxFifo1MsgPendingCallback` per
      its `FilterFIFOAssignment`). Only `CAN_FILTERMODE_IDMASK` at
      `CAN_FILTERSCALE_32BIT` is modelled — identifier-list mode and 16-bit
      scale use register layouts this project's headers don't name, so
      rather than guess, a bank configured either way never matches; revisit
      if a scenario needs one.
      Verify: `Tests/test_can_hal_sim.c`, host-built, no target.
- [x] **0.3 — `can_hal_sim_wrap.c`: `__wrap_HAL_CAN_*`.** Thin forwarders to
      0.2, plus the explicit `HAL_CAN_MspInit` call from `__wrap_HAL_CAN_Init`
      (Key decision 2).
      Verify: links cleanly once Task 0.4's config exists — no missing or
      duplicate symbols.
- [x] **0.4 — CubeIDE "Simulation" build configuration.** Adds 0.1-0.3 as
      sources, `-DCAN_SIMULATION`, and the seven `-Wl,--wrap=HAL_CAN_*` flags.
      Debug/Release also gained a source-exclusion filter for 0.1-0.3 — not
      needed for binary correctness (`--gc-sections` already strips that code
      from an unwrapped build; confirmed via `nm` on both configs), just
      keeps it out of their object dirs.
      Verify: Simulation builds to a `.elf` with no errors, `main.c`/`can.c`/
      `stm32f1xx_it.c` unmodified; `nm` confirms the real vendor `HAL_CAN_*`
      bodies are gone (gc'd once `--wrap` redirected their only references)
      and only the `__wrap_`/`_Sim` chain remains.

### Phase 1 — IO server: portable layer

- [ ] **1.1 — `firmware_abi/can/stm32_bxcan.py`.** Layout/offset dataclasses
      mirroring 0.1, `CAN_SIM_SYMBOL = "can_sim"`, magic/version constants.
      Verify: a layout test asserts the Python offsets against the numbers
      `can_sim.h` documents (mirrors USB's offset-assertion test).
- [ ] **1.2 — `peripherals/can/abi.py`: `CanFrame`, `CanSimAbi` (ABC).**
      `validate()`, `inject_frame(frame: CanFrame)` (bus -> target, writes
      `rx` + pends the IRQ), `wait_for_tx(timeout_s) -> CanFrame` /
      `ack_tx(mailbox)` (target -> bus, drains `tx[]`). No STM32 detail.
      Verify: `tests/test_architecture_boundaries.py` gains the Key
      decision 5 rule; fails against a deliberate throwaway violation, then
      passes clean once it's removed.
- [ ] **1.3 — `peripherals/can/bus.py`: `CanBus`.** `send_frame(id, data,
      ide=False, rtr=False)` / `receive_frame(timeout_s)`, named from the
      mock's side (send = onto the bus for the target; receive = what the
      target put on the bus) — same convention as
      `UsbHostCdcKeywords`. Written purely against `CanSimAbi`.
      Verify: unit test using a fake `CanSimAbi` double (no gateway) drives
      both methods.

### Phase 2 — IO server: STM32 backend and engine wiring

- [ ] **2.1 — `peripherals/can/backends/stm32_bxcan.py`:
      `Stm32BxcanAbi(CanSimAbi)`.** Translates to MMB reads/writes over
      `TargetGateway`; `irqn` is a constructor parameter (Key decision 5).
      Verify: unit test against `FakeMmbGateway`, mirrors
      `tests/usb/host/backends/test_stm32_otgfs.py`.
- [ ] **2.2 — `engine/peripheral_factory.py`.** `_DRIVERS["can"] = CanBus`;
      `_resolve_abi` grows a `can` branch with a `part -> irqn` table (one
      row: `STM32F103* -> USB_LP_CAN1_RX0_IRQn`).
      Verify: extend the existing `_resolve_abi`/`_resolve_driver` test for
      `class: can`.
- [ ] **2.3 — `engine/context.py`: class-keyed `resolve_mmb_base()`.**
      Replace the hardcoded `USBH_SIM_SYMBOL` lookup with a `class -> symbol`
      map covering both `cdc` and `can` (Key decision 6).
      Verify: extend `tests/engine/test_context.py` with a CAN-class config
      resolving `can_sim`; existing USB case passes unmodified.
- [ ] **2.4 — `engine/keywords/can_keywords.py`.** `Send CAN Frame` /
      `Receive CAN Frame`, mirrors `usb_host_cdc_keywords.py`'s shape
      (registry lookup by the mock's `name:`).
      Verify: unit test drives both keywords through Robot's library-loading
      path (mirrors `tests/engine/test_usb_host_cdc_keywords.py`), not just
      direct Python calls.

### Phase 3 — hardware verification

- [ ] **3.1 — `tests/hardware/fixtures/f103_can_jlink.yaml`.** One Blue Pill,
      `class: can`, mirrors `f401_jlink.yaml`'s shape.
- [ ] **3.2 — End-to-end hardware test, opt-in (`WCET_CAN_TEST=1`).** Flash
      `f103-cansim`'s `ROLE_DEVICE` firmware, built with the Simulation
      config, to **one** Blue Pill — no second board, no transceiver, no
      breadboard bus. `CanBus.send_frame` a `CAN_ID_COMMAND`; assert
      `CAN_ID_STATUS` comes back with the applied value. This is the
      architecture's acceptance criterion: unmodified `ROLE_DEVICE`
      firmware, the IO server standing in for `ROLE_TESTER`.
      Verify: passes on real hardware with `WCET_CAN_TEST=1` set; skipped
      (not failed) with it unset.

### Phase 4 — documentation

- [ ] **4.1 — `docs/arch/can-bxcan-simulation.md`.** Peer to
      `usb-host-simulation.md`: the MMB layout, why the seam is `--wrap` on
      `HAL_CAN_*` rather than USB's dispatch layer, any ordering rule the
      `tx[]`/`rx` slots need, and the Task 3.2 verification story.
- [ ] **4.2 — Add the new doc to `io-server-architecture.md`'s doc table.**
- [ ] **4.3 — Record the deferred register-level approach in `todo.md`.**
      One entry capturing Non-goals item 1 (repointed-`CAN1` mocking,
      rejected for bxCAN's reactive registers, worth reconsidering for a
      passive-register peripheral) so it doesn't need re-deriving from
      scratch later.

## Order

Phase 0 before everything: it validates the unproven part (does `--wrap`
actually work here) before Python is written against an assumed contract.
1 depends on 0.1's layout to mirror. 2 depends on 1 (needs `CanSimAbi`) and
touches the two shared `engine/` files last, after the CAN-specific pieces
are solid. 3 depends on 0 and 2 both landing. 4 documents the end state,
written last like USB's Phase 5.
