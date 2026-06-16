from __future__ import annotations

import os
import sys
import time
import unittest

# 'tests/usb-host' has a hyphen, so it is not an importable package and
# `python -m unittest discover` cannot traverse it. Put the wcet-io-server root
# (three levels up) on sys.path so the file runs directly:
#   python tests/usb-host/test_usbh_conf_urb_sim.py
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backends.jlink import JLinkBackend, JLinkConnectionConfig
from firmware_abi.usbh_otgfs import (
    CDC_DEVICE_DESCRIPTOR,
    DEFAULT_USBH_SIM_LAYOUT,
    OTG_FS_IRQn,
    URB_DONE,
    USBH_SIM_MAGIC,
    resolve_usbh_sim_base,
)
from target.gateway import TargetError

NVIC_ISER_BASE = 0xE000E100
PORT_SPEED_FS = 1

# Control SETUP packets (8 bytes) the CDC enumeration emits first.
#   GET_DESCRIPTOR(Device, wLength=8)  -> learn bMaxPacketSize0
#   GET_DESCRIPTOR(Device, wLength=18) -> full device descriptor
GET_DESCRIPTOR_DEVICE_8 = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])
GET_DESCRIPTOR_DEVICE_18 = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00])

# Control pipes (deterministic allocation): ch0 = EP0 OUT, ch1 = EP0 IN.
CTRL_OUT_CH = 0
CTRL_IN_CH = 1


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None else int(value, 0)


class UsbhConfUrbSimTest(unittest.TestCase):
    """Verify the task-2.4 URB shims in usbh_conf.c over SWD/J-Link:

        USBH_LL_SubmitURB     -> OUT/SETUP lands in out_buf (+out_pending);
                                 IN raises in_requested
        USBH_LL_GetURBState   -> a DONE IN delivers in_buf to the middleware,
                                 so the control transfer can complete
        USBH_LL_GetLastXferSize-> reports the delivered byte count, letting the
                                 stack advance to the next request

    test_first_setup_arrives covers the OUT path alone. test_control_in_round_trip
    drives the first GET_DESCRIPTOR(Device,8) control transfer to completion --
    SETUP, the 8-byte IN data stage, and the status stage -- and proves all three
    shims work by observing the stack advance to the *next* SETUP
    (GET_DESCRIPTOR(Device,18)). This is one control transfer, not full
    enumeration.

    Needs firmware built with -DUSB_HOST_SIMULATION (tasks 2.1-2.4) running on
    the target, plus a JLinkGDBServerCLExe already attached. Enable with
    WCET_USB_HOST_TEST=1 (skips otherwise).
    """

    def setUp(self) -> None:
        if not _truthy(os.environ.get("WCET_USB_HOST_TEST")):
            raise unittest.SkipTest("set WCET_USB_HOST_TEST=1 to run the USB host hardware test")

        self.layout = DEFAULT_USBH_SIM_LAYOUT
        self.base = resolve_usbh_sim_base()
        self.timeout_s = float(os.environ.get("WCET_USB_HOST_TIMEOUT", "3.0"))

        config = JLinkConnectionConfig(
            host=os.environ.get("WCET_JLINK_HOST", "127.0.0.1"),
            gdb_port=_env_int("WCET_JLINK_PORT", 2331),
            timeout_s=float(os.environ.get("WCET_JLINK_TIMEOUT", "2.0")),
        )
        self.target = JLinkBackend(config).connect()

        # Fresh boot each test for isolation, then make sure the host stack is up.
        self.target.reset(halt_after=False)
        self._ensure_running()
        self._enable_irq(OTG_FS_IRQn)
        try:
            self.target.wait_until(
                lambda: self.target.read_u32(self._hdr("magic")) == USBH_SIM_MAGIC,
                timeout_s=self.timeout_s,
                description="USBH_LL_Start to publish the MMB magic",
            )
        except TargetError as exc:
            self.fail(
                f"host stack never published the magic ({exc}). Is the firmware "
                "built with -DUSB_HOST_SIMULATION and at tasks 2.1-2.4?"
            )

    def tearDown(self) -> None:
        target = getattr(self, "target", None)
        if target is not None:
            target.close()

    # -- helpers -------------------------------------------------------------

    def _hdr(self, field: str) -> int:
        return self.base + getattr(self.layout, field)

    def _ch(self, n: int, field: str) -> int:
        return self.base + self.layout.ch(n, field)

    def _ensure_running(self) -> None:
        try:
            self.target.monitor_command("go")
        except TargetError:
            pass

    def _enable_irq(self, irqn: int) -> None:
        iser = NVIC_ISER_BASE + (irqn // 32) * 4
        self.target.write_u32(iser, 1 << (irqn % 32))

    def _bring_up_port(self) -> None:
        """Connect a FS device and accept the port reset, leaving the stack
        about to enumerate (control pipes open, first SETUP imminent)."""
        t = self.target
        t.write_u8(self._hdr("port_speed"), PORT_SPEED_FS)
        t.write_u8(self._hdr("connected"), 1)
        t.write_u8(self._hdr("ev_connect"), 1)
        t.pend_irq(OTG_FS_IRQn)

        t.wait_until(
            lambda: t.read_u8(self._hdr("reset_req")) == 1,
            timeout_s=self.timeout_s,
            description="USBH_LL_ResetPort to set reset_req",
        )

        t.write_u8(self._hdr("reset_req"), 0)
        t.write_u8(self._hdr("port_speed"), PORT_SPEED_FS)
        t.write_u8(self._hdr("port_enabled"), 1)
        t.write_u8(self._hdr("ev_port_en"), 1)
        t.pend_irq(OTG_FS_IRQn)

    def _ack_out(self, ch: int) -> None:
        """Complete an OUT/SETUP stage: mark it DONE and clear out_pending."""
        self.target.write_u8(self._ch(ch, "urb_state"), URB_DONE)
        self.target.write_u8(self._ch(ch, "out_pending"), 0)

    def _provide_in(self, ch: int, data: bytes) -> None:
        """Fulfil an IN stage: place the bytes, set the count, mark DONE."""
        t = self.target
        t.write(self._ch(ch, "in_buf"), data)
        t.write_u16(self._ch(ch, "xfer_count"), len(data))
        t.write_u8(self._ch(ch, "urb_state"), URB_DONE)
        t.write_u8(self._ch(ch, "in_requested"), 0)

    # -- USBH_LL_SubmitURB (OUT/SETUP path) ----------------------------------

    def test_first_setup_arrives_on_ch0(self) -> None:
        """The first thing the stack submits is GET_DESCRIPTOR(Device,8) as a
        SETUP on the EP0 OUT pipe -- USBH_LL_SubmitURB must copy it into out_buf
        and raise out_pending."""
        t = self.target
        self._bring_up_port()

        t.wait_until(
            lambda: t.read_u8(self._ch(CTRL_OUT_CH, "out_pending")) == 1,
            timeout_s=self.timeout_s,
            description="first SETUP packet pending on ch[0]",
        )
        self.assertEqual(t.read_u16(self._ch(CTRL_OUT_CH, "out_len")), 8)
        setup = t.read(self._ch(CTRL_OUT_CH, "out_buf"), 8)
        self.assertEqual(
            setup, GET_DESCRIPTOR_DEVICE_8, f"unexpected first SETUP: {setup.hex(' ')}"
        )

    # -- full control transfer (SubmitURB IN + GetURBState + GetLastXferSize) -

    def test_control_in_round_trip(self) -> None:
        """Service the first control transfer end to end and confirm the stack
        advances to GET_DESCRIPTOR(Device,18).

        We act as the device: ack each OUT/SETUP/status stage, and answer the
        8-byte IN data stage with the device-descriptor head. Reaching the
        second SETUP requires GetURBState to have delivered our IN bytes and
        GetLastXferSize to have reported 8 -- otherwise the stack could not move
        on. We drive a generic service loop so stage ordering/timing is robust.
        """
        t = self.target
        self._bring_up_port()

        deadline = time.monotonic() + self.timeout_s * 3
        saw_in_stage = False
        found_second_setup = False

        while time.monotonic() < deadline:
            if t.read_u8(self._ch(CTRL_OUT_CH, "out_pending")) == 1:
                out_len = t.read_u16(self._ch(CTRL_OUT_CH, "out_len"))
                if out_len == 8:
                    setup = t.read(self._ch(CTRL_OUT_CH, "out_buf"), 8)
                    if setup == GET_DESCRIPTOR_DEVICE_18:
                        found_second_setup = True
                        break
                self._ack_out(CTRL_OUT_CH)  # first SETUP or zero-length status

            if t.read_u8(self._ch(CTRL_IN_CH, "in_requested")) == 1:
                saw_in_stage = True
                self._provide_in(CTRL_IN_CH, CDC_DEVICE_DESCRIPTOR[:8])

            time.sleep(0.005)

        self.assertTrue(
            saw_in_stage,
            "the stack never submitted the IN data stage on ch[1] "
            "(USBH_LL_SubmitURB IN path not reached)",
        )
        self.assertTrue(
            found_second_setup,
            "stack did not advance to GET_DESCRIPTOR(Device,18); the IN delivery "
            "(GetURBState) or byte count (GetLastXferSize) did not take effect",
        )


if __name__ == "__main__":
    unittest.main()
