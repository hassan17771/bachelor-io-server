from __future__ import annotations

import unittest

from firmware_abi.can.stm32_bxcan import (
    BLOCK_HEADER_SIZE,
    BLOCK_SIZE,
    CAN_SIM_ID_EXT,
    CAN_SIM_ID_STD,
    CAN_SIM_MAGIC,
    CAN_SIM_NUM_FILTERS,
    CAN_SIM_NUM_TX,
    CAN_SIM_SYMBOL,
    CAN_SIM_VERSION,
    DEFAULT_CAN_SIM_LAYOUT,
    FILTER_SIZE,
    FRAME_SIZE,
    CanSimLayout,
)

# The C structs from can_sim.h, as ordered (name, size) lists. Every
# field in both structs already sits on its natural boundary, so a simple
# cumulative sum reproduces the exact C offsets. We assert that below, then
# compare the result against the Python CanSimLayout -- that is what "offsets
# match the C layout" means here.

_FRAME_FIELDS = [
    ("id", 4),
    ("ide", 1),
    ("rtr", 1),
    ("dlc", 1),
    ("pending", 1),
    ("data", 8),
]

_FILTER_FIELDS = [
    ("FilterIdHigh", 4),
    ("FilterIdLow", 4),
    ("FilterMaskIdHigh", 4),
    ("FilterMaskIdLow", 4),
    ("FilterMode", 4),
    ("FilterScale", 4),
    ("FilterFIFOAssignment", 4),
    ("FilterActivation", 4),
]

_HEADER_FIELDS = [
    ("magic", 4),
    ("version", 2),
    ("block_size", 2),
]


def _offsets(fields):
    """Cumulative offsets and total size for an ordered (name, size) list."""
    offsets = {}
    cursor = 0
    for name, size in fields:
        offsets[name] = cursor
        cursor += size
    return offsets, cursor


class Stm32BxcanLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = DEFAULT_CAN_SIM_LAYOUT

    # -- sizes -----------------------------------------------------------

    def test_frame_size_is_16(self) -> None:
        _, size = _offsets(_FRAME_FIELDS)
        self.assertEqual(size, 16)
        self.assertEqual(size, FRAME_SIZE)
        self.assertEqual(size, self.layout.tx_stride)

    def test_filter_size_is_32(self) -> None:
        _, size = _offsets(_FILTER_FIELDS)
        self.assertEqual(size, 32)
        self.assertEqual(size, FILTER_SIZE)
        self.assertEqual(size, self.layout.filter_stride)

    def test_block_size_is_104(self) -> None:
        _, header_size = _offsets(_HEADER_FIELDS)
        self.assertEqual(header_size, BLOCK_HEADER_SIZE)
        self.assertEqual(header_size, self.layout.filters_base)
        expected = header_size + CAN_SIM_NUM_FILTERS * FILTER_SIZE + CAN_SIM_NUM_TX * FRAME_SIZE + FRAME_SIZE
        self.assertEqual(expected, 104)
        self.assertEqual(BLOCK_SIZE, 104)

    # -- no implicit padding (so cumulative sum == real C offsets) -------

    def test_no_hidden_padding(self) -> None:
        for fields in (_FRAME_FIELDS, _FILTER_FIELDS, _HEADER_FIELDS):
            offsets, _ = _offsets(fields)
            for name, size in fields:
                if size in (1, 2, 4):  # scalar -> must sit on a natural boundary
                    self.assertEqual(
                        offsets[name] % size,
                        0,
                        f"{name} @ {offsets[name]} not {size}-byte aligned",
                    )

    # -- offsets match the Python layout ----------------------------------

    def test_frame_field_offsets_match_layout(self) -> None:
        offsets, _ = _offsets(_FRAME_FIELDS)
        for name, expected in offsets.items():
            self.assertEqual(
                getattr(self.layout.frame, name), expected, f"frame.{name} offset mismatch"
            )

    def test_filter_field_offsets_match_layout(self) -> None:
        offsets, _ = _offsets(_FILTER_FIELDS)
        for name, expected in offsets.items():
            self.assertEqual(
                getattr(self.layout.filter, name), expected, f"filter.{name} offset mismatch"
            )

    def test_header_field_offsets_match_layout(self) -> None:
        offsets, _ = _offsets(_HEADER_FIELDS)
        for name, expected in offsets.items():
            self.assertEqual(
                getattr(self.layout, name), expected, f"{name} offset mismatch"
            )

    # -- named ABI invariants ---------------------------------------------

    def test_bank_helper_named_invariants(self) -> None:
        # bank(0).FilterIdHigh at block offset 8 (right after the 8-byte header)
        self.assertEqual(self.layout.bank(0, "FilterIdHigh"), 8)

    def test_tx_helper_named_invariants(self) -> None:
        # tx(0).id at block offset 40 (8 header + 1 filter bank * 32)
        self.assertEqual(self.layout.tx(0, "id"), 40)
        # tx(1).id at block offset 56 (40 + 1*16)
        self.assertEqual(self.layout.tx(1, "id"), 56)
        # tx(2).id at block offset 72 (40 + 2*16)
        self.assertEqual(self.layout.tx(2, "id"), 72)

    def test_rx_helper_named_invariants(self) -> None:
        # rx.id at block offset 88 (40 + 3*16, right after the last tx mailbox)
        self.assertEqual(self.layout.rx("id"), 88)
        # rx.data ends exactly at the block size
        self.assertEqual(self.layout.rx("data") + 8, BLOCK_SIZE)

    def test_bank_helper_rejects_bad_index(self) -> None:
        with self.assertRaises(ValueError):
            self.layout.bank(CAN_SIM_NUM_FILTERS, "FilterIdHigh")
        with self.assertRaises(ValueError):
            self.layout.bank(-1, "FilterIdHigh")

    def test_tx_helper_rejects_bad_index(self) -> None:
        with self.assertRaises(ValueError):
            self.layout.tx(CAN_SIM_NUM_TX, "id")
        with self.assertRaises(ValueError):
            self.layout.tx(-1, "id")

    def test_layout_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.layout.magic = 1  # type: ignore[misc]

    def test_custom_layout_constructs(self) -> None:
        self.assertEqual(CanSimLayout().tx(1, "id"), 56)

    # -- constants ---------------------------------------------------------

    def test_identity_constants(self) -> None:
        # Contract value shared with the firmware (Core/Inc/can_sim.h).
        self.assertEqual(CAN_SIM_MAGIC, 0x534E4143)
        self.assertEqual(CAN_SIM_MAGIC.to_bytes(4, "little"), b"CANS")
        self.assertEqual(CAN_SIM_VERSION, 0x0001)
        self.assertEqual(CAN_SIM_SYMBOL, "can_sim")

    def test_num_slots(self) -> None:
        self.assertEqual(CAN_SIM_NUM_TX, 3)
        self.assertEqual(CAN_SIM_NUM_FILTERS, 1)

    def test_ide_values(self) -> None:
        self.assertEqual((CAN_SIM_ID_STD, CAN_SIM_ID_EXT), (0, 1))


if __name__ == "__main__":
    unittest.main()
