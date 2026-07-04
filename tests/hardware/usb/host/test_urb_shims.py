"""URB shims in usbh_conf.c, exercised over SWD/J-Link.

Covered:

    USBH_LL_SubmitURB      -> OUT/SETUP lands in out_buf (+out_pending);
                              IN raises in_requested
    USBH_LL_GetURBState    -> a DONE IN delivers in_buf to the middleware,
                              so the control transfer can complete
    USBH_LL_GetLastXferSize-> reports the delivered byte count, letting the
                              stack advance to the next request

test_first_setup_arrives_on_ch0 covers the OUT path alone.
test_control_in_round_trip drives the first GET_DESCRIPTOR(Device,8) control
transfer to completion -- SETUP, the 8-byte IN data stage, and the status
stage -- and proves all three shims work by observing the stack advance to
the *next* SETUP (GET_DESCRIPTOR(Device,18)). This is one control transfer,
not full enumeration.

Needs firmware built with -DUSB_HOST_SIMULATION running on the target, plus a
JLinkGDBServerCLExe already attached. Enable with WCET_USB_HOST_TEST=1 (skips
otherwise); see tests/support/hardware.py for the environment-variable table.
"""
from __future__ import annotations

import time
import unittest

from firmware_abi.usb.host.stm32_otgfs import CDC_DEVICE_DESCRIPTOR, NUM_CH
from tests.support.hardware import UsbHostHardwareTest

# Control SETUP packets (8 bytes) the CDC enumeration emits first.
#   GET_DESCRIPTOR(Device, wLength=8)  -> learn bMaxPacketSize0
#   GET_DESCRIPTOR(Device, wLength=18) -> full device descriptor
GET_DESCRIPTOR_DEVICE_8 = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])
GET_DESCRIPTOR_DEVICE_18 = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00])

# Control pipes (deterministic allocation): ch0 = EP0 OUT, ch1 = EP0 IN.
CTRL_OUT_CH = 0
CTRL_IN_CH = 1


class UsbhConfUrbSimTest(UsbHostHardwareTest):
    def _bring_up_port(self) -> None:
        """Connect a FS device and accept the port reset, leaving the stack
        about to enumerate (control pipes open, first SETUP imminent)."""
        self.abi.connect()
        self.abi.wait_for_port_reset(timeout_s=self.timeout_s)
        self.abi.accept_port_reset()

    # -- USBH_LL_SubmitURB (OUT/SETUP path) ----------------------------------

    def test_first_setup_arrives_on_ch0(self) -> None:
        """The first thing the stack submits is GET_DESCRIPTOR(Device,8) as a
        SETUP on the EP0 OUT pipe -- USBH_LL_SubmitURB must copy it into out_buf
        and raise out_pending."""
        self._bring_up_port()

        packet = self.abi.wait_for_out(CTRL_OUT_CH, timeout_s=self.timeout_s)
        self.assertTrue(packet.is_setup, "expected an 8-byte SETUP on the control OUT pipe")
        self.assertEqual(
            packet.data,
            GET_DESCRIPTOR_DEVICE_8,
            f"unexpected first SETUP: {packet.data.hex(' ')}",
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
        self._bring_up_port()

        deadline = time.monotonic() + self.timeout_s * 3
        counts = {"setup8": 0, "setup18": 0, "setup_other": 0, "status0": 0, "in": 0}
        trace: list[str] = []

        def log(ev: str) -> None:
            trace.append(ev)
            if len(trace) > 80:
                del trace[0]

        found = False
        while time.monotonic() < deadline:
            if self.view.ch_get_u8(CTRL_OUT_CH, "out_pending") == 1:
                out_len = self.view.ch_get_u16(CTRL_OUT_CH, "out_len")
                if out_len == 8:
                    setup = self.view.ch_read(CTRL_OUT_CH, "out_buf", 8)
                    if setup == GET_DESCRIPTOR_DEVICE_18:
                        counts["setup18"] += 1
                        log("SETUP18")
                        found = True
                        break
                    if setup == GET_DESCRIPTOR_DEVICE_8:
                        counts["setup8"] += 1
                        log("SETUP8")
                    else:
                        counts["setup_other"] += 1
                        log("SETUP?=" + setup.hex())
                    self.abi.ack_out(CTRL_OUT_CH)
                else:
                    counts["status0"] += 1
                    log(f"STATUS(out_len={out_len})")
                    self.abi.ack_out(CTRL_OUT_CH)

            if self.view.ch_get_u8(CTRL_IN_CH, "in_requested") == 1:
                counts["in"] += 1
                log("IN")
                self.abi.provide_in(CTRL_IN_CH, CDC_DEVICE_DESCRIPTOR[:8])

            time.sleep(0.003)

        header = {
            f: self.view.get_u8(f)
            for f in ("connected", "port_enabled", "port_speed", "reset_req",
                      "ev_connect", "ev_disconnect", "ev_port_en")
        }
        chans = []
        for n in range(NUM_CH):
            chans.append(
                f"ch{n}: ep=0x{self.view.ch_get_u8(n, 'ep_addr'):02x} "
                f"type={self.view.ch_get_u8(n, 'ep_type')} "
                f"dir={self.view.ch_get_u8(n, 'ep_dir')} "
                f"urb={self.view.ch_get_u8(n, 'urb_state')} "
                f"outp={self.view.ch_get_u8(n, 'out_pending')} "
                f"outlen={self.view.ch_get_u16(n, 'out_len')} "
                f"inreq={self.view.ch_get_u8(n, 'in_requested')} "
                f"xfer={self.view.ch_get_u16(n, 'xfer_count')}"
            )
        dump = "\n    ".join(chans)
        self.assertTrue(
            found,
            "stack did not advance to GET_DESCRIPTOR(Device,18).\n"
            f"  serviced counts: {counts}\n"
            f"  last events: {trace[-40:]}\n"
            f"  header: {header}\n"
            f"  channels:\n    {dump}",
        )


if __name__ == "__main__":
    unittest.main()
