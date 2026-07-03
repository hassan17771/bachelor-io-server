from __future__ import annotations

import os
import sys
import unittest

# 'tests/usb-host' has a hyphen, so it is not an importable package and
# `python -m unittest discover` cannot traverse it. Put the wcet-io-server root
# (three levels up) on sys.path so the file runs directly:
#   python tests/usb-host/test_usbh_conf_sim.py
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backends.jlink import JLinkBackend, JLinkConnectionConfig
from firmware_abi.usb.host.stm32_otgfs import (
    BLOCK_SIZE,
    DEFAULT_USBH_SIM_LAYOUT,
    EP_DIR_IN,
    EP_DIR_OUT,
    EP_TYPE_CTRL,
    NUM_CH,
    OTG_FS_IRQn,
    USBH_SIM_MAGIC,
    USBH_SIM_VERSION,
    resolve_usbh_sim_base,
)
from target.gateway import TargetError

NVIC_ISER_BASE = 0xE000E100
PORT_SPEED_FS = 1


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None else int(value, 0)


class UsbhConfSimTest(unittest.TestCase):
    """Verify the USBH_LL_* sim shims by observing their effects in the
    MMB over SWD/J-Link. Covered:

        USBH_LL_Init      -> the block is memset to 0 at boot
        USBH_LL_Start     -> magic / version / block_size published
        USBH_LL_ResetPort -> reset_req raised when the stack resets the port
        USBH_LL_OpenPipe  -> EP0 control pipe identities recorded in ch[0]/ch[1]

    Not covered (not observable here, and outside "no full enumeration"):
        USBH_LL_GetSpeed  -> result goes to phost->device.speed, not the MMB
                             (it does run, just before OpenPipe on the same path)
        USBH_LL_Stop      -> only called on USBH_Stop/DeInit (disconnect path)
        USBH_LL_ClosePipe -> only called on FreePipe (re-enum / disconnect path)

    Needs firmware built with -DUSB_HOST_SIMULATION running on the target, plus
    a JLinkGDBServerCLExe already attached, e.g.

        JLinkGDBServerCLExe -device STM32F411CE -if SWD -speed 4000 -port 2331

    Enable with WCET_USB_HOST_TEST=1 (skips otherwise). Config via env:
        WCET_USB_HOST_TEST / WCET_USB_HOST_BASE / WCET_JLINK_HOST /
        WCET_JLINK_PORT / WCET_USB_HOST_TIMEOUT
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

        # Start every test from a fresh boot: it isolates the cases and recovers
        # from the HardFault the OpenPipe case provokes (the unpatched
        # USBH_LL_SubmitURB runs once OpenPipe has finished -- see that test).
        self.target.reset(halt_after=False)
        self._ensure_running()

        # Sim Init skips HAL_HCD_MspInit, so firmware never enables OTG_FS_IRQn;
        # the IO server must, or pend_irq does nothing.
        self._enable_irq(OTG_FS_IRQn)

        # Wait until the host stack has started, i.e. USBH_LL_Init (memset) and
        # USBH_LL_Start (publishes the magic) have both run.
        try:
            self.target.wait_until(
                lambda: self.target.read_u32(self._hdr("magic")) == USBH_SIM_MAGIC,
                timeout_s=self.timeout_s,
                description="USBH_LL_Start to publish the MMB magic",
            )
        except TargetError as exc:
            self.fail(
                f"host stack never published the magic ({exc}). Is the firmware "
                "built with -DUSB_HOST_SIMULATION?"
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

    def _issue_connect(self) -> None:
        t = self.target
        t.write_u8(self._hdr("port_speed"), PORT_SPEED_FS)
        t.write_u8(self._hdr("connected"), 1)
        t.write_u8(self._hdr("ev_connect"), 1)
        t.pend_irq(OTG_FS_IRQn)

    # -- USBH_LL_Start -------------------------------------------------------

    def test_start_publishes_identity(self) -> None:
        """USBH_LL_Start writes version + block_size, then the magic."""
        t = self.target
        self.assertEqual(t.read_u32(self._hdr("magic")), USBH_SIM_MAGIC)
        self.assertEqual(t.read_u16(self._hdr("version")), USBH_SIM_VERSION)
        self.assertEqual(t.read_u16(self._hdr("block_size")), BLOCK_SIZE)

    # -- USBH_LL_Init --------------------------------------------------------

    def test_init_cleared_the_block(self) -> None:
        """USBH_LL_Init memsets the MMB: before any connection the header flags
        and every channel read back as 0 (magic/version/block_size aside)."""
        t = self.target
        for field in (
            "connected",
            "port_enabled",
            "port_speed",
            "reset_req",
            "ev_connect",
            "ev_disconnect",
            "ev_port_en",
        ):
            self.assertEqual(
                t.read_u8(self._hdr(field)), 0, f"header.{field} not cleared by USBH_LL_Init"
            )

        for n in range(NUM_CH):
            for field in ("ep_addr", "ep_type", "ep_dir", "urb_state", "out_pending", "in_requested"):
                self.assertEqual(
                    t.read_u8(self._ch(n, field)), 0, f"ch[{n}].{field} not cleared"
                )
            self.assertEqual(t.read_u16(self._ch(n, "out_len")), 0, f"ch[{n}].out_len not cleared")
            self.assertEqual(t.read_u16(self._ch(n, "xfer_count")), 0, f"ch[{n}].xfer_count not cleared")

    # -- USBH_LL_ResetPort ---------------------------------------------------

    def test_reset_port_raises_reset_req(self) -> None:
        """On connect the stack calls USBH_LL_ResetPort, which sets reset_req,
        then parks waiting for the port to come up -- so this never reaches the
        unpatched URB path."""
        t = self.target
        self.assertEqual(t.read_u8(self._hdr("reset_req")), 0)

        self._issue_connect()

        t.wait_until(
            lambda: t.read_u8(self._hdr("reset_req")) == 1,
            timeout_s=self.timeout_s,
            description="USBH_LL_ResetPort to set reset_req after connect",
        )

    # -- USBH_LL_OpenPipe ----------------------------------------------------

    def test_open_pipe_records_control_endpoints(self) -> None:
        """After the port comes up, the stack opens the two EP0 control pipes
        via USBH_LL_OpenPipe before submitting any URB. We accept the reset,
        then read the recorded pipe identities.

        Note: one iteration later the stack submits the first SETUP through
        USBH_LL_SubmitURB. That happens *after* OpenPipe has written ch[0]/ch[1],
        and the records persist in RAM, so the assertions below are unaffected
        (J-Link reads memory regardless of core state; setUp resets next time).
        """
        t = self.target

        self._issue_connect()
        t.wait_until(
            lambda: t.read_u8(self._hdr("reset_req")) == 1,
            timeout_s=self.timeout_s,
            description="USBH_LL_ResetPort before accepting the reset",
        )
        # Accept the reset: bring the port up and notify the stack.
        t.write_u8(self._hdr("reset_req"), 0)
        t.write_u8(self._hdr("port_speed"), PORT_SPEED_FS)
        t.write_u8(self._hdr("port_enabled"), 1)
        t.write_u8(self._hdr("ev_port_en"), 1)
        t.pend_irq(OTG_FS_IRQn)

        # The control IN pipe (EP 0x80) is the detectable one -- the OUT pipe's
        # endpoint is 0x00, indistinguishable from a cleared channel.
        t.wait_until(
            lambda: t.read_u8(self._ch(1, "ep_addr")) == 0x80,
            timeout_s=self.timeout_s,
            description="USBH_LL_OpenPipe to record the EP0 IN control pipe (ch[1])",
        )

        # ch[1] = EP0 control IN
        self.assertEqual(t.read_u8(self._ch(1, "ep_addr")), 0x80)
        self.assertEqual(t.read_u8(self._ch(1, "ep_type")), EP_TYPE_CTRL)
        self.assertEqual(t.read_u8(self._ch(1, "ep_dir")), EP_DIR_IN)

        # ch[0] = EP0 control OUT (ep_addr 0x00; direction is the real evidence)
        self.assertEqual(t.read_u8(self._ch(0, "ep_addr")), 0x00)
        self.assertEqual(t.read_u8(self._ch(0, "ep_type")), EP_TYPE_CTRL)
        self.assertEqual(t.read_u8(self._ch(0, "ep_dir")), EP_DIR_OUT)


if __name__ == "__main__":
    unittest.main()
