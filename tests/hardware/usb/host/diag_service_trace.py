"""Verbose, timestamped enumeration-servicing trace (no assertions).

Brings the port up and then acts as the device for ~20s, logging every SETUP
(decoded) and every IN request with a timestamp, and sampling the middleware
state machine (gState/EnumState/Control.state) as it goes. Unlike the unit test
it answers control INs with the *requested* wLength, so the DATA stage can
actually complete -- this shows whether enumeration progresses past
GET_DESCRIPTOR(Device,8) and, if not, exactly where and when it stalls.

    cd wcet-io-server
    python tests/hardware/usb/host/diag_service_trace.py
"""
from __future__ import annotations

import os
import sys
import time

# Standalone script (not collected by pytest, so conftest.py does not run):
# put the wcet-io-server root on sys.path itself.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backends.jlink import JLinkBackend, JLinkConnectionConfig
from firmware_abi.usb.host.stm32_otgfs import (
    CDC_CONFIGURATION_DESCRIPTOR,
    CDC_DEVICE_DESCRIPTOR,
    CDC_LINE_CODING_115200_8N1,
    DEFAULT_USBH_SIM_LAYOUT,
    NUM_CH,
    OTG_FS_IRQn,
    URB_DONE,
    USBH_SIM_MAGIC,
    resolve_usbh_sim_base,
)

NVIC_ISER_BASE = 0xE000E100
PORT_SPEED_FS = 1
HUSBHOST_FS = 0x200000F8
HOST_STATE = ["IDLE", "WAIT_ATTACH", "DEV_ATTACHED", "DISCONNECTED",
              "DETECT_SPEED", "ENUMERATION", "CLASS_REQUEST", "INPUT",
              "SET_CONFIG", "SET_WAKEUP", "CHECK_CLASS", "CLASS", "SUSPENDED",
              "ABORT"]
ENUM_STATE = ["IDLE", "GET_FULL_DEV_DESC", "SET_ADDR", "GET_CFG_DESC",
              "GET_FULL_CFG_DESC", "GET_MFC_STR", "GET_PROD_STR", "GET_SERIAL_STR"]
CTRL_STATE = ["IDLE", "SETUP", "SETUP_WAIT", "DATA_IN", "DATA_IN_WAIT",
              "DATA_OUT", "DATA_OUT_WAIT", "STATUS_IN", "STATUS_IN_WAIT",
              "STATUS_OUT", "STATUS_OUT_WAIT", "ERROR", "STALLED", "COMPLETE"]


def _name(t, v):
    return t[v] if 0 <= v < len(t) else f"?{v}"


def _decode_setup(b: bytes) -> str:
    if len(b) < 8:
        return f"<short {b.hex(' ')}>"
    bm, br = b[0], b[1]
    wval = b[2] | (b[3] << 8)
    widx = b[4] | (b[5] << 8)
    wlen = b[6] | (b[7] << 8)
    reqs = {0: "GET_STATUS", 1: "CLEAR_FEATURE", 3: "SET_FEATURE",
            5: "SET_ADDRESS", 6: "GET_DESCRIPTOR", 7: "SET_DESCRIPTOR",
            8: "GET_CONFIG", 9: "SET_CONFIG"}
    dtypes = {1: "DEVICE", 2: "CONFIG", 3: "STRING", 4: "INTERFACE", 5: "ENDPOINT"}
    name = reqs.get(br, f"req{br}")
    if br == 6:
        name += f"({dtypes.get(wval >> 8, wval >> 8)},idx{wval & 0xFF})"
    return f"bm=0x{bm:02x} {name} wValue=0x{wval:04x} wIndex=0x{widx:04x} wLength={wlen}"


def _resp_for(wval_hi: int, wlen: int) -> bytes:
    """Pick the descriptor bytes to answer a GET_DESCRIPTOR IN with."""
    table = {1: CDC_DEVICE_DESCRIPTOR, 2: CDC_CONFIGURATION_DESCRIPTOR}
    data = table.get(wval_hi, CDC_DEVICE_DESCRIPTOR)
    return (data + b"\x00" * wlen)[:wlen]


def main() -> int:
    layout = DEFAULT_USBH_SIM_LAYOUT
    base = resolve_usbh_sim_base()
    cfg = JLinkConnectionConfig(
        host=os.environ.get("WCET_JLINK_HOST", "127.0.0.1"),
        gdb_port=int(os.environ.get("WCET_JLINK_PORT", "2331"), 0),
        timeout_s=float(os.environ.get("WCET_JLINK_TIMEOUT", "2.0")),
    )
    t = JLinkBackend(cfg).connect()

    def hdr(f): return base + getattr(layout, f)
    def ch(n, f): return base + layout.ch(n, f)
    def t0(): return time.monotonic() - start

    try:
        t.reset(halt_after=False)
        try:
            t.monitor_command("go")
        except Exception:
            pass
        iser = NVIC_ISER_BASE + (OTG_FS_IRQn // 32) * 4
        t.write_u32(iser, 1 << (OTG_FS_IRQn % 32))

        start = time.monotonic()
        t.wait_until(lambda: t.read_u32(hdr("magic")) == USBH_SIM_MAGIC,
                     timeout_s=3.0, description="magic")
        print(f"[{t0():6.3f}] magic up; bringing port up")

        # connect + accept reset
        t.write_u8(hdr("port_speed"), PORT_SPEED_FS)
        t.write_u8(hdr("connected"), 1)
        t.write_u8(hdr("ev_connect"), 1)
        t.pend_irq(OTG_FS_IRQn)
        t.wait_until(lambda: t.read_u8(hdr("reset_req")) == 1,
                     timeout_s=3.0, description="reset_req")
        t.write_u8(hdr("reset_req"), 0)
        t.write_u8(hdr("port_speed"), PORT_SPEED_FS)
        t.write_u8(hdr("port_enabled"), 1)
        t.write_u8(hdr("ev_port_en"), 1)
        t.pend_irq(OTG_FS_IRQn)
        print(f"[{t0():6.3f}] port reset accepted; servicing...")

        last_setup_wval_hi = 1
        last_setup_wlen = 0
        last_state = None
        opened = set()
        deadline = time.monotonic() + 6.0
        n_setup = n_in = n_status = 0

        while time.monotonic() < deadline:
            # note any newly-opened channel (pipe allocation beyond ctrl)
            for n in range(NUM_CH):
                ep = t.read_u8(ch(n, "ep_addr"))
                if ep and n not in opened:
                    opened.add(n)
                    print(f"[{t0():6.3f}] ch{n} opened ep=0x{ep:02x} "
                          f"type={t.read_u8(ch(n,'ep_type'))} dir={t.read_u8(ch(n,'ep_dir'))}")

            # control/bulk OUT or SETUP on ch0 (and ch3 once bulk opens)
            for cn in (0, 3):
                if t.read_u8(ch(cn, "out_pending")) == 1:
                    olen = t.read_u16(ch(cn, "out_len"))
                    if olen == 0:
                        n_status += 1
                        print(f"[{t0():6.3f}] ch{cn} STATUS-OUT (zero-len)")
                    elif olen == 8 and cn == 0:
                        raw = t.read(ch(cn, "out_buf"), 8)
                        n_setup += 1
                        print(f"[{t0():6.3f}] ch{cn} SETUP  {_decode_setup(raw)}  [{raw.hex(' ')}]")
                        last_setup_wval_hi = raw[3]
                        last_setup_wlen = raw[6] | (raw[7] << 8)
                    else:
                        raw = t.read(ch(cn, "out_buf"), min(olen, 16))
                        print(f"[{t0():6.3f}] ch{cn} OUT len={olen} [{raw.hex(' ')}...]")
                    # clear request flag first, signal DONE last (avoids the
                    # ack/resubmit clobber race -- see test_urb_shims.py)
                    t.write_u8(ch(cn, "out_pending"), 0)
                    t.write_u8(ch(cn, "urb_state"), URB_DONE)

            # control/bulk IN on ch1 (and ch4 once bulk opens)
            for cn in (1, 4):
                if t.read_u8(ch(cn, "in_requested")) == 1:
                    n_in += 1
                    data = _resp_for(last_setup_wval_hi, last_setup_wlen) if cn == 1 else b"hello\r\n"
                    t.write(ch(cn, "in_buf"), data)
                    t.write_u16(ch(cn, "xfer_count"), len(data))
                    t.write_u8(ch(cn, "in_requested"), 0)
                    t.write_u8(ch(cn, "urb_state"), URB_DONE)
                    print(f"[{t0():6.3f}] ch{cn} IN  -> {len(data)}B [{data[:12].hex(' ')}...]")

            # sample the middleware FSM whenever it changes
            g = t.read_u8(HUSBHOST_FS + 0)
            e = t.read_u8(HUSBHOST_FS + 1)
            c = t.read_u8(HUSBHOST_FS + 24)
            state = (g, e, c)
            if state != last_state:
                print(f"[{t0():6.3f}]   FSM gState={_name(HOST_STATE,g)} "
                      f"EnumState={_name(ENUM_STATE,e)} Ctrl={_name(CTRL_STATE,c)} "
                      f"errcnt={t.read_u8(HUSBHOST_FS+25)}")
                last_state = state

            time.sleep(0.002)

        print(f"\nsummary: setups={n_setup} ins={n_in} statuses={n_status} "
              f"channels_opened={sorted(opened)}")

        # --- raw stuck-state dump --------------------------------------------
        # Firmware scratch arrays (addresses from the SubmitURB disassembly):
        S_URB_IS_IN = 0x200008B0   # uint8_t s_urb_is_in[8]
        S_XFER_BUFF = 0x200008B8   # uint8_t  *s_xfer_buff[8]
        print("\n== RAW stuck-state dump ==")
        g = t.read_u8(HUSBHOST_FS + 0)
        e = t.read_u8(HUSBHOST_FS + 1)
        print(f"  gState={_name(HOST_STATE,g)} EnumState={_name(ENUM_STATE,e)} "
              f"Ctrl={_name(CTRL_STATE, t.read_u8(HUSBHOST_FS+24))} "
              f"errcnt={t.read_u8(HUSBHOST_FS+25)}")
        print(f"  Control.pipe_in={t.read_u8(HUSBHOST_FS+4)} "
              f"pipe_out={t.read_u8(HUSBHOST_FS+5)} "
              f"pipe_size={t.read_u8(HUSBHOST_FS+6)} "
              f"length={t.read_u16(HUSBHOST_FS+12)} "
              f"timer={t.read_u16(HUSBHOST_FS+14)}")
        for n in range(NUM_CH):
            raw = t.read(base + layout.ch(n, "ep_addr"), 12)  # the 12-byte channel header
            if any(raw) or n < 2:
                print(f"  ch{n} hdr[0:12]={raw.hex(' ')}  "
                      f"s_urb_is_in={t.read_u8(S_URB_IS_IN + n)} "
                      f"s_xfer_buff=0x{t.read_u32(S_XFER_BUFF + n*4):08x}")
        print(f"  (channel header layout: ep_addr ep_type ep_dir urb_state "
              f"out_len(2) xfer_count(2) out_pending in_requested _pad(2))")
    finally:
        t.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
