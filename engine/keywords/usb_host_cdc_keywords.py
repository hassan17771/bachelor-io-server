"""Keywords for a CDC-ACM device mock, one library per device class."""
from __future__ import annotations

import time

from engine import registry
from peripherals.usb.host.abi import UsbHostSimAbi, UsbHostSimError
from peripherals.usb.host.cdc import UsbHostCdcDevice

MOCK_NAME = "usb_host"

# The firmware publishes the MMB header when its host stack starts, which is a
# moment after a reset. A suite that attaches straight after `Reset Target` has
# to allow for that; anything still failing by then is a real mismatch.
HOST_STACK_TIMEOUT_S = 2.0
HOST_STACK_POLL_S = 0.05


class UsbHostCdcKeywords:
    """Drives the CDC-ACM device the target's USB host middleware talks to.

    Enumeration is not a keyword of its own: it is what attaching a device
    inevitably triggers, so `Attach Device` returns with the mock already
    configured and ready for traffic. `Send Packet` and `Receive Packet` are
    named from the mock's side -- send is device-to-host, the target's
    `USBH_CDC_Receive`.

    Attaching is the first thing that touches the MMB, so it checks the block's
    identity header before driving it: a wrong firmware ELF, a stale address or
    an ABI mismatch is named there rather than surfacing much later as an
    enumeration timeout.
    """

    def attach_device(self) -> None:
        """Check the MMB, then present the device, bring the port up and enumerate."""
        device = self._device()
        self._await_host_stack(device.abi)
        device.attach()
        device.enumerate()

    def detach_device(self) -> None:
        """Unplug the device."""
        self._device().abi.disconnect()

    def send_packet(self, data: bytes) -> None:
        """Deliver `data` to the host's pending bulk-IN request."""
        device = self._device()
        device.bulk_in(device.bulk_in_ch, data)

    def receive_packet(self) -> bytes:
        """Return the payload the host transmitted on the bulk-OUT pipe."""
        device = self._device()
        return device.bulk_out(device.bulk_out_ch)

    @staticmethod
    def _await_host_stack(abi: UsbHostSimAbi) -> None:
        """Poll `validate()` until it passes; re-raise its last failure on timeout."""
        deadline = time.monotonic() + HOST_STACK_TIMEOUT_S
        while True:
            try:
                abi.validate()
                return
            except UsbHostSimError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(HOST_STACK_POLL_S)

    @staticmethod
    def _device() -> UsbHostCdcDevice:
        return registry.peripheral(MOCK_NAME)
