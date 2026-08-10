# Plan: STM32F401 test plan — ELF-resolved MMB and a CDC echo suite

Status: **Done**

Depends on [`test-engine-config-yaml.md`](../test-engine-config-yaml.md) Phase 2
(the runtime registry, `run_suite`, and the keyword libraries), which is where
the pieces this plan extends already live.

## Why

`../f401-USBsim` is a second simulation firmware, and it places its Mocking
Memory Block differently from `../wcet-blackusb`. Three facts drive this plan:

1. **The MMB is an ordinary `.bss` global.** `usbh_sim.c` declares
   `volatile UsbhSimBlock usbh_sim;` with no section attribute, and
   `STM32F401RCTX_FLASH.ld` has no `.mmb` region — deliberately, so that
   adopting the simulation driver costs a project nothing in its linker
   script. The address is therefore a property of a *build*, discoverable
   only from that build's ELF.
2. **Our hardcoded base is worse than stale.** `firmware_abi/usb/host/stm32_otgfs.py`
   defaults to `0x2001E000`, the address the F411 linker script pinned. The
   F401RC has 64 K of RAM ending at `0x20010000`, so on this target the
   default is not merely wrong, it is outside RAM: a misconfigured run reads
   zeros instead of failing.
3. **The firmware ABI moved to version 2.** `validate()` currently rejects
   anything but `0x0001` and would refuse every f401 connection.

The deliverable is a Robot Framework CDC echo suite for the f401 application
(`USBH_CDC_ReceiveCallback` echoes each bulk packet straight back), plus the
MMB-addressing work it needs to reach the board at all.

### Non-goals

- **No CLI.** Out of scope repo-wide; `run_suite()` stays the entry point.
- **No per-build MMB sizing.** `USBH_SIM_NUM_CH` / `USBH_SIM_BUF_SIZE` are
  overridable in firmware now, but no project overrides them. The derived
  constants below assume the defaults and the `block_size` check rejects a
  build that disagrees — accommodating such a build is a `todo.md` item.
- **No fake model of the target's USB middleware.** Nothing on the fake
  backend answers a SETUP or arms an IN, so no fake-backed test can prove the
  echo protocol. See key decision 4.
- **No firmware changes** to `f401-USBsim` or `wcet-blackusb`.
- **No second peripheral / second MMB.** Unchanged from the parent plan.

## Key decisions

1. **The ELF is the only way to locate the MMB.** Not a preferred way among
   several — the only one. Every fixed-address path is deleted:
   `USBH_SIM_BASE`, the `WCET_USB_HOST_BASE` override, `GatewayConfig` and the
   `soc.gateway:` YAML section, which has nothing left to hold once
   `mmb_base` goes. Keeping a numeric fallback alive for one firmware that
   happens to pin its block is exactly the dead code this plan is removing.
2. **The ELF path is a runtime input, never config YAML.** Which build is
   flashed depends on who is running the server and from where; the YAML
   describes *the board*, which does not change between invocations. Mixing
   the two would make every config file machine-specific. So: an `elf=`
   argument to `run_suite()`/`build_context()`, falling back to
   `WCET_FIRMWARE_ELF`, and a `ConfigError` naming both if neither is set.
3. **Only ABI version 2 is supported.** `wcet-blackusb` still publishes
   `0x0001` and will be rejected until its firmware is updated. Accepting
   both was considered and dropped: the layouts are byte-identical, so a
   version set would be two constants and a branch carrying no behaviour.
   Consequence: the existing F411 hardware tests fail against that board.
   They are opt-in (`WCET_USB_HOST_TEST=1`), so CI is unaffected.
4. **The fake tier proves the wiring; only hardware proves the echo.** This is
   the parent plan's key decision 7 applied here. On the fake backend the
   suite runs against a scripted stand-in device (as
   `tests/engine/test_usb_host_cdc_keywords.py` already does) and proves that
   the suite parses, the keywords resolve, the config loads and the ELF
   lookup returns the right address. The echo itself is an opt-in hardware
   task, left unticked until a board is attached.
5. **The symbol name is a firmware-ABI constant, not user config.**
   `usbh_sim` is fixed by `usbh_sim.h`, the same contract that fixes the field
   offsets, so it belongs beside the layout in `firmware_abi/`.

## Target structure

Changed:

```
target/symbols.py                    + ElfSymbolResolver (stdlib .symtab reader)
firmware_abi/usb/host/stm32_otgfs.py version 2; derived sizes; + USBH_SIM_SYMBOL;
                                     USBH_SIM_BASE / WCET_USB_HOST_BASE deleted
engine/config_schema.py              GatewayConfig and SocConfig.gateway deleted
engine/config_parser.py              gateway: section deleted, and rejected
engine/context.py                    + resolve_mmb_base(), elf= parameter
engine/peripheral_factory.py         takes a resolved address, not a config
engine/test_engine.py                elf= parameter passed through
engine/keywords/usb_host_cdc_keywords.py  Attach Device validates the MMB first
backends/jlink.py, backends/openocd.py  reset(halt_after=False) actually resumes
peripherals/usb/host/cdc.py          enumerate()'s default timeout_s raised to 60s
tests/support/hardware.py            resolves its base from the ELF
tests/engine/fixtures/minimal_fake.yaml   gateway: block dropped
docs/arch/                           split in two; the fixed address described
                                     nowhere any more
```

Added:

```
tests/target/test_symbols.py              ElfSymbolResolver tests
tests/target/fixtures/f401_usbsim.elf     ../f401-USBsim Release build, checked in
tests/robot/cdc_echo.robot                the suite; board-independent, as the
                                          config is what names a board
tests/engine/fixtures/f401_fake.yaml      backend: fake
tests/hardware/fixtures/f401_jlink.yaml   backend: jlink, probe: server
tests/engine/test_f401_cdc_echo.py        fake-tier driver
tests/backends/test_reset.py              reset(halt_after=False) resumes
tests/usb/host/test_cdc.py                enumerate()'s default timeout_s
tests/hardware/engine/test_f401_cdc_echo.py  opt-in hardware driver
```

`arm-none-eabi-nm` is not assumed to be installed; `ElfSymbolResolver` reads
the section headers and `.symtab` directly with `struct`. `NmSymbolResolver`
stays as a fallback for anyone who has the toolchain. Only little-endian
ELF32 is parsed — Cortex-M is nothing else, and a wider reader would be
untested code.

The real f401 ELF is committed as a fixture rather than read from
`../f401-USBsim/Release/`, so the tests need neither that checkout nor a
build, and the addresses they assert cannot drift under a rebuild.

## Tasks

### Phase 1 — resolve the MMB from the ELF

- [x] **1.1 — `ElfSymbolResolver` in `target/symbols.py`.** Parse ELF32
      section headers and `.symtab` with the standard library only; same
      `SymbolResolver` interface as the existing resolvers.
      Verify: resolves `usbh_sim` and `Appli_state` out of the committed f401
      Release ELF, and returns `None` for an absent symbol and for anything
      that is not a little-endian ELF32.
- [x] **1.2 — `resolve_mmb_base()` in `engine/context.py`, threaded through
      `run_suite()`.** `elf=` argument, else `WCET_FIRMWARE_ELF`, else
      `ConfigError` naming both. `build_context` resolves once and hands the
      address to `build_peripherals`. Adds `USBH_SIM_SYMBOL` to `firmware_abi/`,
      which the lookup needs (moved here from 2.2).
      Verify: the argument wins over the environment variable; a run with
      neither raises `ConfigError`; a suite run with `elf=` set builds a
      peripheral whose ABI carries the symbol's address.
- [x] **1.3 — Delete the fixed-address path.** `USBH_SIM_BASE`,
      `WCET_USB_HOST_BASE_ENV`, `resolve_usbh_sim_base`, `GatewayConfig`,
      `SocConfig.gateway`, `_parse_gateway`, and the `gateway:` block in
      `minimal_fake.yaml`. `Stm32OtgFsHostAbi.base_address` becomes required.
      A config still carrying `soc.gateway:` is rejected rather than ignored,
      so a stale pinned address cannot silently do nothing.
      Verify: `tests/usb/` and `tests/engine/` pass; a config with
      `soc.gateway:` raises `ConfigError`.
- [x] **1.5 — Re-split `docs/arch/`.** The single architecture file becomes a
      master `io-server-architecture.md` (layers, gateway, backends, engine,
      MMB conventions) plus one file per simulated peripheral, starting with
      `usb-host-simulation.md` — so a second peripheral has somewhere to go.
      Brings both up to date with the engine and the ELF lookup.
      Verify: nothing under `docs/arch/` still describes a pinned MMB address
      or a `soc.gateway:` block; the peripheral file is reachable from the
      master.

### Phase 2 — USBH_SIM ABI v2

- [x] **2.1 — `USBH_SIM_VERSION = 0x0002`.**
      Verify: `validate()` passes for a header reporting 2 and fails with a
      message naming both versions for anything else.
- [x] **2.2 — Derive the block geometry.** `CH_STRIDE` and `BLOCK_SIZE`
      computed from `BUF_SIZE`/`NUM_CH` instead of being written out.
      Verify: `tests/usb/host/backends/test_stm32_otgfs_layout.py` still
      asserts 140 and 1136 and now derives them.

### Phase 3 — the echo suite

- [x] **3.1 — `Attach Device` validates the MMB first.** `abi.validate()` is
      called only from tests today, so a suite runs against an unchecked
      block: a wrong ELF, a stale base or an ABI v1 build surfaces twenty
      seconds later as an enumeration timeout. Validating inside
      `attach_device` — with a short retry, so a run straight after
      `Reset Target` does not race the boot — names the fault at once. A
      `Wait For Host Stack` keyword was considered and dropped: a suite author
      can forget a keyword, and nothing clears the MMB on the fake backend, so
      it would have asserted nothing there.
      Verify: an attach against a block with the wrong magic or version fails
      naming it; one that publishes the magic late still attaches.
- [x] **3.2 — The suite and its two configs.** `tests/robot/cdc_echo.robot`:
      reset, attach, send a packet, assert the same bytes come back, detach.
      Plus the fake and J-Link config fixtures, which are what make it an f401
      run.
      Verify: the suite passes on the fake backend against a scripted device
      stand-in, and fails if the stand-in returns different bytes.
- [x] **3.2a — `reset(halt_after=False)` actually resumes.** Found running
      3.3 against real hardware: J-Link/OpenOCD `monitor reset` halts the core
      regardless of the flag, so `Reset Target` left the target parked at
      `Reset_Handler` and the firmware never ran. Both backends now call
      `resume()` when `halt_after` is false, so the parameter means what it
      says.
      Verify: `reset(halt_after=False)` sends resume after the reset command;
      `reset(halt_after=True)` does not.
- [x] **3.2b — `enumerate()`'s default timeout was too short for real hardware.**
      Found running 3.3: each control transfer costs several seconds over the
      J-Link link (up to 4.4s observed), so a full CDC enumeration (9-10
      transfers) measured 25-28s end to end -- past the old 20s ceiling every
      time, always at the same SETUP. Default raised to 60s, clear of the
      ~47s worst case extrapolated from the slowest transfer observed.
      Verify: `UsbHostCdcDevice.enumerate`'s default `timeout_s` clears the
      measured worst-case enumeration cost.
- [x] **3.3 — Hardware run (opt-in).** The same suite against the F401 board
      over the J-Link GDB server, with `WCET_FIRMWARE_ELF` pointed at the
      CubeIDE build.
      Verify: the echo suite passes against real hardware. Left unticked
      until a board is attached — this is the only task that proves the ELF
      lookup found a real address and that the target actually echoes.

## Order

Phase 1 is strictly sequential: 1.2 must land before 1.3, so the replacement
exists before the fixed-address path is deleted. Phase 2 is independent of
Phase 1 and can be done in parallel.
Phase 3 needs both — the suite cannot reach an f401 without Phase 1's address
or Phase 2's version. 3.3 needs hardware and closes the plan.
