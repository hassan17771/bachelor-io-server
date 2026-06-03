from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntFlag
from typing import Dict, Optional


class UsbIstr(IntFlag):
    EP_ID = 0x000F
    DIR = 0x0010
    ESOF = 0x0100
    SOF = 0x0200
    RESET = 0x0400
    SUSP = 0x0800
    WKUP = 0x1000
    ERR = 0x2000
    PMAOVR = 0x4000
    CTR = 0x8000


USB_ISTR_NAMES: Dict[str, int] = {
    "CTR": int(UsbIstr.CTR),
    "RESET": int(UsbIstr.RESET),
    "SOF": int(UsbIstr.SOF),
    "SUSP": int(UsbIstr.SUSP),
    "WKUP": int(UsbIstr.WKUP),
    "ERR": int(UsbIstr.ERR),
    "PMAOVR": int(UsbIstr.PMAOVR),
}

USB_EP_CTR_RX = 0x8000
USB_EP_DTOG_RX = 0x4000
USB_EPRX_STAT = 0x3000
USB_EP_SETUP = 0x0800
USB_EP_CTR_TX = 0x0080
USB_EP_DTOG_TX = 0x0040
USB_EPTX_STAT = 0x0030
USB_EP_RX_VALID = 0x3000
USB_EP_TX_VALID = 0x0030
USB_EP_RX_COUNT_MASK = 0xFC00
USB_EPADDR_FIELD = 0x000F

USB_LP_CAN_RX0_IRQN = 20
PMA_ACCESS = 2
PMA_SIZE = 512
DEFAULT_USB_SIM_BASE = 0x20004A38

USB_REQ_GET_DESCRIPTOR = 0x06
USB_REQ_SET_ADDRESS = 0x05
USB_REQ_SET_CONFIGURATION = 0x09

USB_DESC_TYPE_DEVICE = 0x01
USB_DESC_TYPE_CONFIGURATION = 0x02
USB_DESC_TYPE_STRING = 0x03

CDC_SET_LINE_CODING = 0x20
CDC_SET_CONTROL_LINE_STATE = 0x22

CDC_OUT_EP = 0x01
CDC_IN_EP = 0x01
EP0 = 0

DEFAULT_LINE_CODING_115200_8N1 = bytes([0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08])


@dataclass(frozen=True)
class Usb2SimLayout:
    cntr: int = 0x00
    istr: int = 0x02
    fnr: int = 0x04
    daddr: int = 0x06
    btable: int = 0x08
    ep_reg: int = 0x0A
    tx_pma_addr: int = 0x1A
    rx_pma_addr: int = 0x2A
    tx_count: int = 0x3A
    pma: int = 0x4A
    pma_size: int = 0x24A
    connected: int = 0x24C
    remote_wakeup: int = 0x24D
    device_address: int = 0x24E


@dataclass(frozen=True)
class Usb2SimConfig:
    symbol: str = "usb_sim"
    base_address: Optional[int] = DEFAULT_USB_SIM_BASE
    resolve_symbol: bool = False
    irq_number: int = USB_LP_CAN_RX0_IRQN
    layout: Usb2SimLayout = Usb2SimLayout()
    pma_access: int = PMA_ACCESS
    pma_size: int = PMA_SIZE


DEFAULT_USB2_SIM_CONFIG = Usb2SimConfig()


@dataclass(frozen=True)
class Usb2SimStatus:
    base_address: int
    cntr: int
    istr: int
    daddr: int
    connected: int
    device_address: int

    def format(self) -> str:
        return (
            f"usb_sim base: 0x{self.base_address:08X}\n"
            f"CNTR=0x{self.cntr:04X} ISTR=0x{self.istr:04X} "
            f"DADDR=0x{self.daddr:04X} connected={self.connected} "
            f"device_address={self.device_address}"
        )


@dataclass(frozen=True)
class Usb2Packet:
    endpoint: int
    payload: bytes
    setup: bool = False
    direction_in: bool = False

    def __post_init__(self) -> None:
        if self.endpoint < 0 or self.endpoint > 7:
            raise ValueError("endpoint must be between 0 and 7")
        object.__setattr__(self, "payload", bytes(self.payload))
        if self.setup and (self.endpoint != 0 or len(self.payload) != 8):
            raise ValueError("USB SETUP packets must be exactly 8 bytes on EP0")


@dataclass(frozen=True)
class UsbSetupPacket:
    bm_request_type: int
    b_request: int
    w_value: int = 0
    w_index: int = 0
    w_length: int = 0

    def pack(self) -> bytes:
        return struct.pack(
            "<BBHHH",
            self.bm_request_type & 0xFF,
            self.b_request & 0xFF,
            self.w_value & 0xFFFF,
            self.w_index & 0xFFFF,
            self.w_length & 0xFFFF,
        )


@dataclass(frozen=True)
class EnumerationResult:
    address: int
    device_descriptor: bytes
    configuration_descriptor: bytes

    @property
    def vid(self) -> Optional[int]:
        if len(self.device_descriptor) < 10:
            return None
        return int.from_bytes(self.device_descriptor[8:10], "little")

    @property
    def pid(self) -> Optional[int]:
        if len(self.device_descriptor) < 12:
            return None
        return int.from_bytes(self.device_descriptor[10:12], "little")

    @property
    def total_configuration_length(self) -> Optional[int]:
        if len(self.configuration_descriptor) < 4:
            return None
        return int.from_bytes(self.configuration_descriptor[2:4], "little")

    def summary(self) -> str:
        vid = "unknown" if self.vid is None else f"0x{self.vid:04X}"
        pid = "unknown" if self.pid is None else f"0x{self.pid:04X}"
        cfg_len = self.total_configuration_length
        cfg = "unknown" if cfg_len is None else str(cfg_len)
        return (
            f"USB2 FS CDC device enumerated at address {self.address}: "
            f"VID={vid} PID={pid} config_len={cfg}"
        )


def parse_hex_bytes(text: str) -> bytes:
    cleaned = text.replace(" ", "").replace("_", "")
    if cleaned.startswith(("0x", "0X")):
        cleaned = cleaned[2:]
    if len(cleaned) % 2:
        raise ValueError("hex payload must have even number of nybbles")
    return bytes.fromhex(cleaned)


def parse_istr_mask(mask_arg: str) -> int:
    if mask_arg.startswith(("0x", "0X")):
        return int(mask_arg, 16)
    mask = 0
    for name in mask_arg.split(","):
        key = name.strip().upper()
        if key not in USB_ISTR_NAMES:
            raise ValueError(f"unknown ISTR mask token: {key}")
        mask |= USB_ISTR_NAMES[key]
    return mask
