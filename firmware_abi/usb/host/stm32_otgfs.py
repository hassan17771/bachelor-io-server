from __future__ import annotations

import os
from dataclasses import dataclass, field

# STM32 OTG_FS USB-host simulation ABI.
#
# Mirrors the C contract in Core/Inc/usbh_sim.h (UsbhSimBlock / UsbhSimChannel).
# Both sides assert the same sizes/offsets: the firmware via _Static_assert, the
# Python side via tests/test_usbh_otgfs_abi.py. Keep the two in lock-step.

# --- identity ---------------------------------------------------------------

USBH_SIM_MAGIC = 0x48424255  # "USBH" little-endian; written by USBH_LL_Start
USBH_SIM_VERSION = 0x0001
OTG_FS_IRQn = 67             # NVIC position of OTG_FS_IRQHandler

# --- placement --------------------------------------------------------------

# Fixed RAM address of the UsbhSimBlock MMB.
# It is the first block in the reserved 8 KB '.mmb' region at the top of RAM:
#   _mmb_origin = ORIGIN(RAM) + LENGTH(RAM) - _mmb_region_size
#               = 0x20000000 + 0x20000 - 0x2000 = 0x2001E000
# Pinned by STM32F411CEUX_FLASH.ld so it is stable across builds and resolvable
# without the ELF file. Future peripheral MMBs append above this address.
# Override with WCET_USB_HOST_BASE / a constructor arg for a different MCU.
USBH_SIM_BASE = 0x2001E000

WCET_USB_HOST_BASE_ENV = "WCET_USB_HOST_BASE"


def resolve_usbh_sim_base(default: int = USBH_SIM_BASE) -> int:
    """MMB base address, honouring the WCET_USB_HOST_BASE override (hex or dec)."""
    raw = os.environ.get(WCET_USB_HOST_BASE_ENV)
    if raw is None or not raw.strip():
        return default
    return int(raw, 0)


# --- block geometry ---------------------------------------------------------

NUM_CH = 8                  # UsbhSimBlock.ch[8]
BUF_SIZE = 64               # per-direction payload buffer
CH_STRIDE = 140             # sizeof(UsbhSimChannel)
BLOCK_HEADER_SIZE = 16      # bytes before the channel array
BLOCK_SIZE = 1136           # sizeof(UsbhSimBlock) = 16 + 8 * 140

# --- urb_state values (mirror USBH_SIM_URB_* in usbh_sim.h) ------------------

URB_IDLE = 0
URB_DONE = 1
URB_NOTREADY = 2
URB_ERROR = 4
URB_STALL = 5

# --- endpoint types / directions --------------------------------------------

EP_TYPE_CTRL = 0
EP_TYPE_ISOC = 1
EP_TYPE_BULK = 2
EP_TYPE_INTR = 3

EP_DIR_OUT = 0
EP_DIR_IN = 1


# --- layout -----------------------------------------------------------------

@dataclass(frozen=True)
class UsbhSimChannelLayout:
    """Byte offsets of UsbhSimChannel fields, relative to the channel base."""

    ep_addr: int = 0
    ep_type: int = 1
    ep_dir: int = 2
    urb_state: int = 3
    out_len: int = 4
    xfer_count: int = 6
    out_pending: int = 8
    in_requested: int = 9
    _pad: int = 10
    out_buf: int = 12
    in_buf: int = 76


@dataclass(frozen=True)
class UsbhSimLayout:
    """Byte offsets of UsbhSimBlock fields, relative to the block base.

    Header fields are absolute offsets from the block base; per-channel fields
    are reached through :meth:`ch`, which adds the channel base and stride.
    """

    magic: int = 0
    version: int = 4
    block_size: int = 6
    connected: int = 8
    port_enabled: int = 9
    port_speed: int = 10
    reset_req: int = 11
    ev_connect: int = 12
    ev_disconnect: int = 13
    ev_port_en: int = 14
    _pad: int = 15

    ch_base: int = BLOCK_HEADER_SIZE
    ch_stride: int = CH_STRIDE
    num_ch: int = NUM_CH
    channel: UsbhSimChannelLayout = field(default_factory=UsbhSimChannelLayout)

    def ch(self, n: int, field_name: str) -> int:
        """Block-relative offset of channel ``n``'s ``field_name``.

        Example: ``layout.ch(1, "ep_addr") == 156`` (16 + 1*140 + 0).
        """
        if not 0 <= n < self.num_ch:
            raise ValueError(f"channel index {n} out of range 0..{self.num_ch - 1}")
        rel = getattr(self.channel, field_name)
        return self.ch_base + n * self.ch_stride + rel


DEFAULT_USBH_SIM_LAYOUT = UsbhSimLayout()


# --- CDC descriptors the IO server replies with during enumeration ----------

# Device descriptor (18 bytes): STM32 Virtual COM Port (VID 0x0483 PID 0x5740).
CDC_DEVICE_DESCRIPTOR = bytes(
    [
        0x12,        # bLength = 18
        0x01,        # bDescriptorType = DEVICE
        0x00, 0x02,  # bcdUSB = 2.00
        0x02,        # bDeviceClass = CDC
        0x00,        # bDeviceSubClass
        0x00,        # bDeviceProtocol
        0x40,        # bMaxPacketSize0 = 64
        0x83, 0x04,  # idVendor = 0x0483 (STMicroelectronics)
        0x40, 0x57,  # idProduct = 0x5740 (STM32 VCP)
        0x00, 0x02,  # bcdDevice = 2.00
        0x01,        # iManufacturer
        0x02,        # iProduct
        0x03,        # iSerialNumber
        0x01,        # bNumConfigurations
    ]
)

# Configuration descriptor (67 bytes): CDC-ACM, two interfaces, three endpoints.
CDC_CONFIGURATION_DESCRIPTOR = bytes(
    [
        # Configuration descriptor (9)
        0x09, 0x02, 0x43, 0x00, 0x02, 0x01, 0x00, 0xC0, 0x32,
        # Interface 0 -- CDC Communications, 1 endpoint (9)
        0x09, 0x04, 0x00, 0x00, 0x01, 0x02, 0x02, 0x01, 0x00,
        # CDC Header functional descriptor (5)
        0x05, 0x24, 0x00, 0x10, 0x01,
        # CDC Call Management functional descriptor (5)
        0x05, 0x24, 0x01, 0x00, 0x01,
        # CDC Abstract Control Management functional descriptor (4)
        0x04, 0x24, 0x02, 0x02,
        # CDC Union functional descriptor (5)
        0x05, 0x24, 0x06, 0x00, 0x01,
        # Notification endpoint 0x82, interrupt IN (7)
        0x07, 0x05, 0x82, 0x03, 0x08, 0x00, 0x10,
        # Interface 1 -- CDC Data, 2 endpoints (9)
        0x09, 0x04, 0x01, 0x00, 0x02, 0x0A, 0x00, 0x00, 0x00,
        # Bulk OUT endpoint 0x01 (7)
        0x07, 0x05, 0x01, 0x02, 0x40, 0x00, 0x00,
        # Bulk IN endpoint 0x81 (7)
        0x07, 0x05, 0x81, 0x02, 0x40, 0x00, 0x00,
    ]
)

# CDC line coding (7 bytes): 115200 baud, 8 data bits, no parity, 1 stop bit.
CDC_LINE_CODING_115200_8N1 = bytes(
    [0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08]
)
