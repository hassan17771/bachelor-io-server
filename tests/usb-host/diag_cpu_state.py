"""Halt the target and report where the CPU actually is.

Run this RIGHT AFTER test_control_in_round_trip fails (do NOT reset in between):
the test leaves the core in whatever state it got stuck in, and this script
freezes it and reads the program counter + Cortex-M fault registers. That tells
us definitively whether the 9-second "hang" is a HardFault (frozen in the fault
handler) or the middleware spinning/delaying in normal code.

    cd wcet-io-server
    python tests/usb-host/diag_cpu_state.py

It does not reset the target, so the stuck state is preserved.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backends.jlink import JLinkBackend, JLinkConnectionConfig
from firmware_abi.usb.host.stm32_otgfs import DEFAULT_USBH_SIM_LAYOUT, resolve_usbh_sim_base

# --- STM32 USB Host middleware state (read straight from hUsbHostFS) ----------
# hUsbHostFS lives at a fixed .bss address (see Release/blackUSB.map). The handle
# begins with three 4-byte enums, then the Control sub-struct at offset 12:
#   gState@0  EnumState@4  RequestState@8  Control@12
#   Control: pipe_in@0 pipe_out@1 pipe_size@2 buff@4 length@8 timer@10
#            setup@12(8B union) state@20 errorcount@24
# so Control.state sits at handle offset 12+20 = 32.
HUSBHOST_FS = 0x200000F8
OFF_GSTATE = 0
OFF_ENUMSTATE = 4
OFF_REQSTATE = 8
OFF_CTRL = 12
OFF_CTRL_STATE = OFF_CTRL + 20          # 32
OFF_CTRL_PIPE_IN = OFF_CTRL + 0         # 12
OFF_CTRL_PIPE_OUT = OFF_CTRL + 1        # 13
OFF_CTRL_LENGTH = OFF_CTRL + 8          # 20
OFF_CTRL_TIMER = OFF_CTRL + 10          # 22
OFF_CTRL_ERRCOUNT = OFF_CTRL + 24       # 36

HOST_STATE = [
    "HOST_IDLE", "HOST_DEV_WAIT_FOR_ATTACHMENT", "HOST_DEV_ATTACHED",
    "HOST_DEV_DISCONNECTED", "HOST_DETECT_DEVICE_SPEED", "HOST_ENUMERATION",
    "HOST_CLASS_REQUEST", "HOST_INPUT", "HOST_SET_CONFIGURATION",
    "HOST_SET_WAKEUP_FEATURE", "HOST_CHECK_CLASS", "HOST_CLASS",
    "HOST_SUSPENDED", "HOST_ABORT_STATE",
]
ENUM_STATE = [
    "ENUM_IDLE", "ENUM_GET_FULL_DEV_DESC", "ENUM_SET_ADDR", "ENUM_GET_CFG_DESC",
    "ENUM_GET_FULL_CFG_DESC", "ENUM_GET_MFC_STRING_DESC",
    "ENUM_GET_PRODUCT_STRING_DESC", "ENUM_GET_SERIALNUM_STRING_DESC",
]
CTRL_STATE = [
    "CTRL_IDLE", "CTRL_SETUP", "CTRL_SETUP_WAIT", "CTRL_DATA_IN",
    "CTRL_DATA_IN_WAIT", "CTRL_DATA_OUT", "CTRL_DATA_OUT_WAIT", "CTRL_STATUS_IN",
    "CTRL_STATUS_IN_WAIT", "CTRL_STATUS_OUT", "CTRL_STATUS_OUT_WAIT",
    "CTRL_ERROR", "CTRL_STALLED", "CTRL_COMPLETE",
]


def _name(table: list[str], value: int) -> str:
    return table[value] if 0 <= value < len(table) else f"?({value})"


# Cortex-M System Control Block fault/status registers.
ICSR = 0xE000ED04   # VECTACTIVE[8:0] = currently executing exception number
SHCSR = 0xE000ED24
CFSR = 0xE000ED28   # MemManage[7:0] | BusFault[15:8] | UsageFault[31:16]
HFSR = 0xE000ED2C   # bit30 FORCED -> a configurable fault escalated to HardFault
MMFAR = 0xE000ED34
BFAR = 0xE000ED38

EXC_NAMES = {
    0: "Thread mode (normal code)",
    2: "NMI",
    3: "HardFault",
    4: "MemManage",
    5: "BusFault",
    6: "UsageFault",
    11: "SVCall",
    14: "PendSV",
    15: "SysTick",
}


def _le_word(regs_hex: str, index: int) -> int:
    """Decode register `index` from a GDB 'g' reply (each reg = 8 hex, LE)."""
    raw = regs_hex[index * 8:index * 8 + 8]
    return int.from_bytes(bytes.fromhex(raw), "little")


def main() -> int:
    layout = DEFAULT_USBH_SIM_LAYOUT
    base = resolve_usbh_sim_base()

    config = JLinkConnectionConfig(
        host=os.environ.get("WCET_JLINK_HOST", "127.0.0.1"),
        gdb_port=int(os.environ.get("WCET_JLINK_PORT", "2331"), 0),
        timeout_s=float(os.environ.get("WCET_JLINK_TIMEOUT", "2.0")),
    )
    target = JLinkBackend(config).connect()
    try:
        target.halt()

        regs = target.client.request("g")
        pc = _le_word(regs, 15)
        lr = _le_word(regs, 14)
        sp = _le_word(regs, 13)

        icsr = target.read_u32(ICSR)
        vectactive = icsr & 0x1FF
        cfsr = target.read_u32(CFSR)
        hfsr = target.read_u32(HFSR)
        shcsr = target.read_u32(SHCSR)
        mmfar = target.read_u32(MMFAR)
        bfar = target.read_u32(BFAR)

        print("== CPU registers ==")
        print(f"  PC = 0x{pc:08X}")
        print(f"  LR = 0x{lr:08X}")
        print(f"  SP = 0x{sp:08X}")
        print()
        print("== Exception state ==")
        print(f"  ICSR        = 0x{icsr:08X}")
        print(f"  VECTACTIVE  = {vectactive}  ({EXC_NAMES.get(vectactive, 'IRQ #' + str(vectactive - 16) if vectactive >= 16 else 'reserved')})")
        print()
        print("== Fault registers ==")
        print(f"  HFSR  = 0x{hfsr:08X}  (bit30 FORCED={'1' if hfsr & (1 << 30) else '0'}, "
              f"bit1 VECTTBL={'1' if hfsr & (1 << 1) else '0'})")
        print(f"  CFSR  = 0x{cfsr:08X}")
        mmfsr = cfsr & 0xFF
        bfsr = (cfsr >> 8) & 0xFF
        ufsr = (cfsr >> 16) & 0xFFFF
        print(f"          MMFSR=0x{mmfsr:02X}  BFSR=0x{bfsr:02X}  UFSR=0x{ufsr:04X}")
        if bfsr & 0x80:
            print(f"          BFAR valid  -> faulting address 0x{bfar:08X}")
        if mmfsr & 0x80:
            print(f"          MMFAR valid -> faulting address 0x{mmfar:08X}")
        if ufsr:
            bits = []
            if ufsr & 0x0001: bits.append("UNDEFINSTR")
            if ufsr & 0x0002: bits.append("INVSTATE")
            if ufsr & 0x0004: bits.append("INVPC")
            if ufsr & 0x0008: bits.append("NOCP")
            if ufsr & 0x0100: bits.append("UNALIGNED")
            if ufsr & 0x0200: bits.append("DIVBYZERO")
            print(f"          UsageFault bits: {', '.join(bits) or '(none)'}")
        print(f"  SHCSR = 0x{shcsr:08X}")
        print()

        def ch(n: int, field: str) -> int:
            return target.read_u8(base + layout.ch(n, field))

        print("== MMB (frozen) ==")
        for n in (0, 1):
            print(
                f"  ch{n}: ep=0x{ch(n, 'ep_addr'):02x} dir={ch(n, 'ep_dir')} "
                f"urb={ch(n, 'urb_state')} outp={ch(n, 'out_pending')} "
                f"outlen={target.read_u16(base + layout.ch(n, 'out_len'))} "
                f"inreq={ch(n, 'in_requested')} "
                f"xfer={target.read_u16(base + layout.ch(n, 'xfer_count'))}"
            )
        print()

        gstate = target.read_u32(HUSBHOST_FS + OFF_GSTATE)
        enumstate = target.read_u32(HUSBHOST_FS + OFF_ENUMSTATE)
        ctrlstate = target.read_u32(HUSBHOST_FS + OFF_CTRL_STATE)
        print("== USB Host middleware (hUsbHostFS @ 0x200000F8) ==")
        print(f"  gState     = {gstate}  ({_name(HOST_STATE, gstate)})")
        print(f"  EnumState  = {enumstate}  ({_name(ENUM_STATE, enumstate)})")
        print(f"  Control.state = {ctrlstate}  ({_name(CTRL_STATE, ctrlstate)})")
        print(f"  Control.pipe_in  = {target.read_u8(HUSBHOST_FS + OFF_CTRL_PIPE_IN)}")
        print(f"  Control.pipe_out = {target.read_u8(HUSBHOST_FS + OFF_CTRL_PIPE_OUT)}")
        print(f"  Control.length   = {target.read_u16(HUSBHOST_FS + OFF_CTRL_LENGTH)}")
        print(f"  Control.timer    = {target.read_u16(HUSBHOST_FS + OFF_CTRL_TIMER)}")
        print(f"  Control.errorcount = {target.read_u8(HUSBHOST_FS + OFF_CTRL_ERRCOUNT)}")
        print()

        if vectactive == 3 or (hfsr & (1 << 30)):
            print(">>> VERDICT: CPU is in a HardFault. The middleware faulted; the")
            print(">>> 9s 'hang' was the fault handler spinning. PC above is inside")
            print(">>> HardFault_Handler; LR/stacked PC point at the faulting code.")
        elif vectactive != 0:
            print(f">>> VERDICT: CPU is servicing exception {vectactive}, not thread mode.")
        else:
            print(">>> VERDICT: No fault — CPU is in normal code. The PC above shows")
            print(">>> where it is spinning/delaying (look it up in the .map/.list).")

        target.resume()
    finally:
        target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
