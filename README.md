# bachelor-io-server

On-chip peripheral simulation for automated firmware testing.

Firmware is usually tested in one of two ways: on an emulator, which loses real timing and interrupts, or on a hardware-in-the-loop rig, which needs the real devices (CAN nodes, USB devices) wired to the board. This project keeps the firmware on the real microcontroller and replaces only the outside world. This **IO server** simulates a peripheral through the chip's debug port, so tests run on real silicon with no extra hardware.

## Method

1. **Driver swapping.** HAL driver functions are wrapped at link time (`--wrap`). The thin shims that replace them read and write a RAM block instead of the physical bus. Everything else in the firmware stays the same.
2. **Mocking Memory Block (MMB).** Each simulated peripheral has a versioned RAM block with a magic/version/size header. It is an ordinary global variable, and the host finds its address in the firmware ELF's symbol table, so the linker script does not change.
3. **Host injection over SWD.** The IO server writes the payload into the MMB through a debug probe, then pends the peripheral's interrupt to wake the firmware. The notification is always written last, so the firmware never sees a half-written event.
4. **Layered design.** Robot Framework suite + YAML board config → test engine → peripheral simulators (*what* to inject) → `TargetGateway` (memory, IRQ, reset, halt/resume, symbols) → probe backend (*how* to reach the chip) → MCU. Peripheral code does not depend on the probe, so a new probe changes the config, not the tests.
5. **Case studies.** A bxCAN receiver on an STM32F103 (Blue Pill) and a USB-CDC host on an STM32F401 (Black Pill), both driven by behaviour-driven (BDD) Robot Framework scenarios.

**Current limits:** one simulated peripheral per run (fixed MMB layout); every gateway access costs a debug-link round trip of a few milliseconds; no synchronisation between peripherals; each new peripheral needs manual edits to the CubeIDE project. See [todo.md](todo.md).

## Quick start

```bash
pip install -r requirements.txt pytest   # pytest is not in requirements.txt yet
python -m pytest                         # hardware tests are skipped by default

# Run a suite against a board (needs a running JLinkGDBServer or OpenOCD)
python -m engine.cli --robot tests/robot/cdc_echo.robot \
    --config tests/hardware/fixtures/f401_jlink.yaml \
    --elf path/to/firmware.elf --outdir results
```

The config YAML describes the board (part, backend, probe, mocks); the firmware ELF is passed at run time (`--elf` or `WCET_FIRMWARE_ELF`). Hardware tests run only with `WCET_USB_HOST_TEST=1`.

## Repository layout

| Folder | Contents |
| --- | --- |
| `engine/` | Config parser, context builder, `run_suite`, CLI, Robot keyword libraries |
| `peripherals/` | Peripheral simulators (USB host CDC, CAN) |
| `target/` | `TargetGateway`, ELF symbol resolvers, backend capabilities |
| `backends/` | J-Link and OpenOCD backends |
| `firmware_abi/` | MMB layouts, magic numbers and symbol names per chip |
| `tests/` | Unit tests (fake backend), Robot suites, opt-in hardware tests |
| `docs/` | Architecture ([io-server-architecture.md](docs/arch/io-server-architecture.md), [USB](docs/arch/usb-host-simulation.md), [CAN](docs/arch/can-bxcan-simulation.md)) and plans |

## Future Work

### Test automation
- **Dynamic MMB:** a discovery table at the start of the block, so several peripherals can share one target.
- **Multi-peripheral synchronisation:** scheduling and locking so tests can drive CAN, USB and other interfaces together.
- **Faster injection:** batch reads and writes into fewer round trips, and connect to the probe directly (pyOCD/pylink) instead of through a GDB server.
- **More coverage:** GPIO, UART, SPI and other peripherals, and ARM chips from other vendors.
- **AI-generated tests:** a language model writes Robot/BDD scenarios, the platform runs them on the chip, and the results go back to the model for the next round.

### WCET estimation
- **Measurement-based WCET:** the bottleneck of measurement-based timing analysis is producing many inputs and feeding them to real hardware. The IO server does this automatically, so a search algorithm or AI model can inject peripheral inputs, measure the execution time of a chosen code block, and steer the inputs towards the worst-case path.
- **ARM ETMv4 trace:** take cycle-accurate timing from the Embedded Trace Macrocell, decode it on the host, and report the time of each basic block in the code block's control-flow graph.
- **Statistical estimate:** turn the measured samples into a WCET estimate with statistical analysis, and present it in a report or an IDE extension.
- **Planned versions:** v0.1 instruments test blocks through the compiler's IR (Raspberry Pi Pico 2 W, pico-sdk, clang) and exports measurements over serial; v0.2 adds ETM trace on an STM32H7 (bare metal, Keil MDK, clang/LLVM), captured with a DSLogic logic analyzer and decoded on the host.

## Tools

| Area | Tools |
| --- | --- |
| Host and tests | Python, Robot Framework (BDD keywords), PyYAML, pytest (unit tests against a fake backend, opt-in hardware tests) |
| Debug access | SEGGER J-Link (JLinkGDBServer), OpenOCD; pyOCD planned; SWD/DAP |
| Firmware | STM32 HAL, STM32CubeIDE/CubeMX, GNU linker `--wrap`, ELF symbol parsing |
| Boards | STM32F103 Blue Pill, STM32F401 Black Pill; STM32H7 and Raspberry Pi Pico 2 W for WCET |
| WCET tracing | ARM CoreSight ETMv4, DSLogic logic analyzer, Keil MDK, clang/LLVM |
| Documentation | Obsidian (Markdown and Canvas diagrams), Git |
