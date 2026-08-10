from __future__ import annotations

import unittest

from firmware_abi.usb.host.stm32_otgfs import (
    BLOCK_HEADER_SIZE,
    BLOCK_SIZE,
    BUF_SIZE,
    CDC_CONFIGURATION_DESCRIPTOR,
    CDC_DEVICE_DESCRIPTOR,
    CDC_LINE_CODING_115200_8N1,
    CH_HEADER_SIZE,
    CH_STRIDE,
    DEFAULT_USBH_SIM_LAYOUT,
    NUM_CH,
    OTG_FS_IRQn,
    URB_DONE,
    URB_ERROR,
    URB_IDLE,
    URB_NOTREADY,
    URB_STALL,
    USBH_SIM_MAGIC,
    UsbhSimLayout,
)

# The C structs from Core/Inc/usbh_sim.h, as ordered (name, size) lists. C lays
# scalar members out at consecutive natural-aligned offsets; this struct is
# hand-padded (explicit _pad) so there is never implicit padding, and a simple
# cumulative sum reproduces the exact C offsets. We assert that below, then
# compare the result against the Python UsbhSimLayout -- that is what "offsets
# match the C layout" means here.

_CHANNEL_FIELDS = [
    ("ep_addr", 1),
    ("ep_type", 1),
    ("ep_dir", 1),
    ("urb_state", 1),
    ("out_len", 2),
    ("xfer_count", 2),
    ("out_pending", 1),
    ("in_requested", 1),
    ("_pad", 2),
    ("out_buf", BUF_SIZE),
    ("in_buf", BUF_SIZE),
]

_HEADER_FIELDS = [
    ("magic", 4),
    ("version", 2),
    ("block_size", 2),
    ("connected", 1),
    ("port_enabled", 1),
    ("port_speed", 1),
    ("reset_req", 1),
    ("ev_connect", 1),
    ("ev_disconnect", 1),
    ("ev_port_en", 1),
    ("_pad", 1),
]


def _offsets(fields):
    """Cumulative offsets and total size for an ordered (name, size) list."""
    offsets = {}
    cursor = 0
    for name, size in fields:
        offsets[name] = cursor
        cursor += size
    return offsets, cursor


class UsbhOtgFsAbiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = DEFAULT_USBH_SIM_LAYOUT

    # -- sizes ---------------------------------------------------------------

    def test_channel_size_is_140(self) -> None:
        offsets, size = _offsets(_CHANNEL_FIELDS)
        self.assertEqual(offsets["out_buf"], CH_HEADER_SIZE)
        self.assertEqual(size, 140)
        self.assertEqual(size, CH_STRIDE)
        self.assertEqual(size, self.layout.ch_stride)

    def test_block_size_is_1136(self) -> None:
        _, header_size = _offsets(_HEADER_FIELDS)
        self.assertEqual(header_size, BLOCK_HEADER_SIZE)
        self.assertEqual(header_size, self.layout.ch_base)
        self.assertEqual(header_size + NUM_CH * CH_STRIDE, 1136)
        self.assertEqual(BLOCK_SIZE, 1136)

    # -- no implicit padding (so cumulative sum == real C offsets) -----------

    def test_no_hidden_padding(self) -> None:
        for fields in (_CHANNEL_FIELDS, _HEADER_FIELDS):
            offsets, _ = _offsets(fields)
            for name, size in fields:
                if size in (1, 2, 4):  # scalar -> must sit on a natural boundary
                    self.assertEqual(
                        offsets[name] % size,
                        0,
                        f"{name} @ {offsets[name]} not {size}-byte aligned",
                    )

    # -- offsets match the Python layout -------------------------------------

    def test_channel_field_offsets_match_layout(self) -> None:
        offsets, _ = _offsets(_CHANNEL_FIELDS)
        for name, expected in offsets.items():
            self.assertEqual(
                getattr(self.layout.channel, name),
                expected,
                f"channel.{name} offset mismatch",
            )

    def test_header_field_offsets_match_layout(self) -> None:
        offsets, _ = _offsets(_HEADER_FIELDS)
        for name, expected in offsets.items():
            self.assertEqual(
                getattr(self.layout, name), expected, f"{name} offset mismatch"
            )

    # -- named ABI invariants ------------------------------------------------

    def test_ch_helper_named_invariants(self) -> None:
        # ch(0).out_buf at block offset 28 (16 header + 12 channel offset)
        self.assertEqual(self.layout.ch(0, "out_buf"), 28)
        # ch(1).ep_addr at block offset 156 (16 + 140)
        self.assertEqual(self.layout.ch(1, "ep_addr"), 156)
        # last channel still inside the block
        self.assertLess(self.layout.ch(NUM_CH - 1, "in_buf") + BUF_SIZE, BLOCK_SIZE + 1)

    def test_ch_helper_rejects_bad_index(self) -> None:
        with self.assertRaises(ValueError):
            self.layout.ch(NUM_CH, "ep_addr")
        with self.assertRaises(ValueError):
            self.layout.ch(-1, "ep_addr")

    def test_layout_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.layout.magic = 1  # type: ignore[misc]

    def test_custom_layout_constructs(self) -> None:
        self.assertEqual(UsbhSimLayout().ch(1, "ep_addr"), 156)

    # -- constants -----------------------------------------------------------

    def test_identity_constants(self) -> None:
        # Contract value shared with the firmware (Core/Inc/usbh_sim.h). The
        # little-endian bytes are 55 42 42 48 = "UBBH", not "USBH"; what matters
        # is firmware and Python agree on the same 32-bit sentinel.
        self.assertEqual(USBH_SIM_MAGIC, 0x48424255)
        self.assertEqual(USBH_SIM_MAGIC.to_bytes(4, "little"), bytes([0x55, 0x42, 0x42, 0x48]))
        self.assertEqual(OTG_FS_IRQn, 67)

    def test_urb_state_values(self) -> None:
        self.assertEqual(
            (URB_IDLE, URB_DONE, URB_NOTREADY, URB_ERROR, URB_STALL),
            (0, 1, 2, 4, 5),
        )

    # -- CDC descriptors -----------------------------------------------------

    def test_device_descriptor(self) -> None:
        d = CDC_DEVICE_DESCRIPTOR
        self.assertEqual(len(d), 18)
        self.assertEqual(d[0], 18)        # bLength
        self.assertEqual(d[1], 0x01)      # bDescriptorType = DEVICE
        self.assertEqual(int.from_bytes(d[8:10], "little"), 0x0483)   # VID
        self.assertEqual(int.from_bytes(d[10:12], "little"), 0x5740)  # PID

    def test_configuration_descriptor(self) -> None:
        c = CDC_CONFIGURATION_DESCRIPTOR
        self.assertEqual(len(c), 67)
        self.assertEqual(c[0], 9)         # bLength of config descriptor
        self.assertEqual(c[1], 0x02)      # bDescriptorType = CONFIGURATION
        self.assertEqual(int.from_bytes(c[2:4], "little"), 67)  # wTotalLength
        self.assertEqual(c[4], 0x02)      # bNumInterfaces

    def test_line_coding(self) -> None:
        lc = CDC_LINE_CODING_115200_8N1
        self.assertEqual(len(lc), 7)
        self.assertEqual(int.from_bytes(lc[0:4], "little"), 115200)  # dwDTERate
        self.assertEqual(lc[4], 0)        # 1 stop bit
        self.assertEqual(lc[5], 0)        # no parity
        self.assertEqual(lc[6], 8)        # 8 data bits


if __name__ == "__main__":
    unittest.main()
