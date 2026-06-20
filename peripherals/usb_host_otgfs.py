"""STM32 OTG_FS concrete implementation of :class:`UsbHostSimAbi`.

Translates the portable host-sim operations into reads/writes of the
``UsbhSimBlock`` MMB (see firmware_abi/usbh_otgfs.py and Core/Inc/usbh_sim.h).
Everything STM32-specific about the USB-host mock lives here; the protocol layer
in usb_host.py never sees an offset or an OTG_FS detail.
"""
from __future__ import annotations

from typing import Optional

from firmware_abi.usbh_otgfs import (
    BLOCK_SIZE,
    BUF_SIZE,
    DEFAULT_USBH_SIM_LAYOUT,
    EP_TYPE_CTRL,
    OTG_FS_IRQn,
    URB_DONE,
    USBH_SIM_MAGIC,
    USBH_SIM_VERSION,
    UsbhSimLayout,
    resolve_usbh_sim_base,
)
from target.gateway import TargetGateway

from .usb_host import (
    DIR_OUT,
    SPEED_FULL,
    ChannelSelector,
    UsbChannelInfo,
    UsbHostSimAbi,
    UsbHostSimError,
    UsbHostSimStatus,
    UsbOutPacket,
)


class Stm32OtgFsHostAbi(UsbHostSimAbi):
    """Drive the STM32 OTG_FS USB-host MMB over a :class:`TargetGateway`."""

    def __init__(
        self,
        gateway: TargetGateway,
        base_address: Optional[int] = None,
        layout: UsbhSimLayout = DEFAULT_USBH_SIM_LAYOUT,
        irqn: int = OTG_FS_IRQn,
    ) -> None:
        self.gateway = gateway
        self.base_address = resolve_usbh_sim_base() if base_address is None else base_address
        self.layout = layout
        self.irqn = irqn

    # -- address helpers -----------------------------------------------------

    def _hdr(self, field: str) -> int:
        return self.base_address + getattr(self.layout, field)

    def _ch(self, channel: int, field: str) -> int:
        return self.base_address + self.layout.ch(channel, field)

    @staticmethod
    def _normalise_channels(channels: ChannelSelector) -> tuple[int, ...]:
        if isinstance(channels, int):
            return (channels,)
        return tuple(channels)

    # -- lifecycle / port ----------------------------------------------------

    def validate(self) -> UsbHostSimStatus:
        """Read and check the identity header USBH_LL_Start publishes.

        The magic is written last by the firmware, so its presence means the
        whole header is valid and the host stack is running.
        """
        g = self.gateway
        status = UsbHostSimStatus(
            magic=g.read_u32(self._hdr("magic")),
            version=g.read_u16(self._hdr("version")),
            block_size=g.read_u16(self._hdr("block_size")),
        )
        if status.magic != USBH_SIM_MAGIC:
            raise UsbHostSimError(
                f"bad MMB magic 0x{status.magic:08X} (expected 0x{USBH_SIM_MAGIC:08X}); "
                "is the firmware built with -DUSB_HOST_SIMULATION and started?"
            )
        if status.block_size != BLOCK_SIZE:
            raise UsbHostSimError(
                f"MMB block_size {status.block_size} != {BLOCK_SIZE}; ABI mismatch"
            )
        if status.version != USBH_SIM_VERSION:
            raise UsbHostSimError(
                f"MMB version 0x{status.version:04X} != 0x{USBH_SIM_VERSION:04X}"
            )
        return status

    def connect(self, speed: int = SPEED_FULL) -> None:
        g = self.gateway
        g.write_u8(self._hdr("port_speed"), speed)
        g.write_u8(self._hdr("connected"), 1)
        g.write_u8(self._hdr("ev_connect"), 1)
        g.pend_irq(self.irqn)

    def disconnect(self) -> None:
        g = self.gateway
        g.write_u8(self._hdr("connected"), 0)
        g.write_u8(self._hdr("ev_disconnect"), 1)
        g.pend_irq(self.irqn)

    def wait_for_port_reset(self, timeout_s: float = 3.0) -> None:
        g = self.gateway
        g.wait_until(
            lambda: g.read_u8(self._hdr("reset_req")) == 1,
            timeout_s=timeout_s,
            description="USBH_LL_ResetPort to set reset_req",
        )

    def accept_port_reset(self, speed: int = SPEED_FULL) -> None:
        g = self.gateway
        g.write_u8(self._hdr("reset_req"), 0)
        g.write_u8(self._hdr("port_speed"), speed)
        g.write_u8(self._hdr("port_enabled"), 1)
        g.write_u8(self._hdr("ev_port_en"), 1)
        g.pend_irq(self.irqn)

    # -- per-transfer servicing ----------------------------------------------

    def channel_info(self, channel: int) -> UsbChannelInfo:
        g = self.gateway
        return UsbChannelInfo(
            endpoint=g.read_u8(self._ch(channel, "ep_addr")),
            direction=g.read_u8(self._ch(channel, "ep_dir")),
            ep_type=g.read_u8(self._ch(channel, "ep_type")),
        )

    def wait_for_out(self, channels: ChannelSelector, timeout_s: float = 3.0) -> UsbOutPacket:
        watch = self._normalise_channels(channels)
        g = self.gateway

        def poll() -> Optional[UsbOutPacket]:
            for cn in watch:
                if g.read_u8(self._ch(cn, "out_pending")) == 1:
                    return self._read_out_packet(cn)
            return None

        return g.wait_until(
            poll,
            timeout_s=timeout_s,
            description=f"a pending OUT/SETUP on channels {watch}",
        )

    def _read_out_packet(self, channel: int) -> UsbOutPacket:
        g = self.gateway
        out_len = g.read_u16(self._ch(channel, "out_len"))
        n = min(out_len, BUF_SIZE)
        data = g.read(self._ch(channel, "out_buf"), n) if n else b""
        info = self.channel_info(channel)
        # The firmware does not record the SETUP token in the MMB. A control
        # transfer always opens with an 8-byte OUT on its control pipe, so that
        # signature is what marks a SETUP -- the same rule the bring-up trace and
        # a USB analyser use.
        is_setup = info.ep_type == EP_TYPE_CTRL and info.direction == DIR_OUT and out_len == 8
        return UsbOutPacket(
            channel=channel,
            endpoint=info.endpoint,
            is_setup=is_setup,
            data=data,
        )

    def ack_out(self, channel: int) -> None:
        """Complete an OUT/SETUP/status stage: clear out_pending, THEN mark DONE.

        The DONE-last ordering is required. urb_state=DONE unblocks the
        middleware's CTRL_*_WAIT poll, which then re-arms the next URB on this
        channel (raising out_pending again). Each write is a separate ~ms GDB
        round-trip, so clearing out_pending *after* DONE risks stomping that
        freshly-armed request. DONE must be last.
        """
        g = self.gateway
        g.write_u8(self._ch(channel, "out_pending"), 0)
        g.write_u8(self._ch(channel, "urb_state"), URB_DONE)

    def provide_in(self, channel: int, data: bytes, timeout_s: float = 3.0) -> None:
        """Wait for the IN request on ``channel``, then deliver ``data``.

        Same ordering rule as :meth:`ack_out`: write the payload, byte count and
        cleared request flag first, and urb_state=DONE last.
        """
        if len(data) > BUF_SIZE:
            raise ValueError(f"IN payload {len(data)} bytes exceeds buffer {BUF_SIZE}")
        g = self.gateway
        g.wait_until(
            lambda: g.read_u8(self._ch(channel, "in_requested")) == 1,
            timeout_s=timeout_s,
            description=f"an IN request on channel {channel}",
        )
        g.write(self._ch(channel, "in_buf"), data)
        g.write_u16(self._ch(channel, "xfer_count"), len(data))
        g.write_u8(self._ch(channel, "in_requested"), 0)
        g.write_u8(self._ch(channel, "urb_state"), URB_DONE)
