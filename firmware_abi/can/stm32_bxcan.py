from __future__ import annotations

from dataclasses import dataclass, field

# STM32 bxCAN simulation ABI.
#
# Mirrors the C contract in `can_sim.h` (CanSimBlock / CanSimFrame /
# CanSimFilter). Both sides assert the same sizes/offsets: the firmware via
# _Static_assert, the Python side via
# tests/can/backends/test_stm32_bxcan_layout.py. Keep the two in lock-step.
#
# No IRQ number lives here (contrast OTG_FS_IRQn in firmware_abi/usb/host/
# stm32_otgfs.py): USB has exactly one IRQ line for the whole USB family,
# bxCAN's depends on the part, so it is resolved by a part -> irqn table in
# engine/peripheral_factory.py instead.

# --- identity -----------------------------------------------------------

CAN_SIM_MAGIC = 0x534E4143  # "CANS" little-endian; written by HAL_CAN_Start_Sim
CAN_SIM_VERSION = 0x0001

# --- placement ------------------------------------------------------------

# Name of the CanSimBlock instance in the firmware. The block is an ordinary
# .bss global, so its address is a property of a build and is read from that
# build's ELF symbol table.
CAN_SIM_SYMBOL = "can_sim"

# --- block geometry ---------------------------------------------------------

CAN_SIM_NUM_TX = 3  # CAN_SIM_NUM_TX -- matches real bxCAN's mailbox count
CAN_SIM_NUM_FILTERS = 1  # CAN_SIM_NUM_FILTERS default; override matches a -D build

FRAME_SIZE = 16  # sizeof(CanSimFrame)
FILTER_SIZE = 32  # sizeof(CanSimFilter), 8 uint32_t fields
BLOCK_HEADER_SIZE = 8  # CanSimBlock fields before filters[]

# CanSimFrame and CanSimFilter are all-scalar structs whose fields already
# sit on their natural boundaries, so there is no implicit padding to model
# and sizes are just the sum of their parts. A build that disagrees is
# caught by the block_size field in validate() rather than read at the
# wrong offsets.
BLOCK_SIZE = (
    BLOCK_HEADER_SIZE
    + CAN_SIM_NUM_FILTERS * FILTER_SIZE
    + CAN_SIM_NUM_TX * FRAME_SIZE
    + FRAME_SIZE  # rx
)

# --- ide values (mirror CAN_SIM_ID_* in can_sim.h) ---------------------------

CAN_SIM_ID_STD = 0
CAN_SIM_ID_EXT = 1


# --- layout -------------------------------------------------------------

@dataclass(frozen=True)
class CanSimFrameLayout:
    """Byte offsets of CanSimFrame fields, relative to the frame base."""

    id: int = 0
    ide: int = 4
    rtr: int = 5
    dlc: int = 6
    pending: int = 7
    data: int = 8


@dataclass(frozen=True)
class CanSimFilterLayout:
    """Byte offsets of CanSimFilter fields, relative to the filter base."""

    FilterIdHigh: int = 0
    FilterIdLow: int = 4
    FilterMaskIdHigh: int = 8
    FilterMaskIdLow: int = 12
    FilterMode: int = 16
    FilterScale: int = 20
    FilterFIFOAssignment: int = 24
    FilterActivation: int = 28


@dataclass(frozen=True)
class CanSimLayout:
    """Byte offsets of CanSimBlock fields, relative to the block base.

    Header fields are absolute offsets from the block base; per-bank and
    per-frame fields are reached through :meth:`bank`/:meth:`tx`/:meth:`rx`.
    """

    magic: int = 0
    version: int = 4
    block_size: int = 6

    filters_base: int = BLOCK_HEADER_SIZE
    filter_stride: int = FILTER_SIZE
    num_filters: int = CAN_SIM_NUM_FILTERS
    filter: CanSimFilterLayout = field(default_factory=CanSimFilterLayout)

    tx_base: int = BLOCK_HEADER_SIZE + CAN_SIM_NUM_FILTERS * FILTER_SIZE
    tx_stride: int = FRAME_SIZE
    num_tx: int = CAN_SIM_NUM_TX
    frame: CanSimFrameLayout = field(default_factory=CanSimFrameLayout)

    rx_base: int = (
        BLOCK_HEADER_SIZE
        + CAN_SIM_NUM_FILTERS * FILTER_SIZE
        + CAN_SIM_NUM_TX * FRAME_SIZE
    )

    def bank(self, n: int, field_name: str) -> int:
        """Block-relative offset of filter bank ``n``'s ``field_name``.

        The bank number doubles as the array index (can_sim.h's FilterBank
        comment): ``layout.bank(0, "FilterIdHigh") == 8``.
        """
        if not 0 <= n < self.num_filters:
            raise ValueError(f"filter bank {n} out of range 0..{self.num_filters - 1}")
        rel = getattr(self.filter, field_name)
        return self.filters_base + n * self.filter_stride + rel

    def tx(self, n: int, field_name: str) -> int:
        """Block-relative offset of tx mailbox ``n``'s ``field_name``."""
        if not 0 <= n < self.num_tx:
            raise ValueError(f"tx mailbox {n} out of range 0..{self.num_tx - 1}")
        rel = getattr(self.frame, field_name)
        return self.tx_base + n * self.tx_stride + rel

    def rx(self, field_name: str) -> int:
        """Block-relative offset of the rx slot's ``field_name``."""
        rel = getattr(self.frame, field_name)
        return self.rx_base + rel


DEFAULT_CAN_SIM_LAYOUT = CanSimLayout()
