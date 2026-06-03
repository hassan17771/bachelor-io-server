from __future__ import annotations

import time
from typing import Optional

from firmware_abi.usb2 import (
    CDC_IN_EP,
    CDC_OUT_EP,
    CDC_SET_CONTROL_LINE_STATE,
    CDC_SET_LINE_CODING,
    DEFAULT_LINE_CODING_115200_8N1,
    DEFAULT_USB2_SIM_CONFIG,
    EP0,
    USB_DESC_TYPE_CONFIGURATION,
    USB_DESC_TYPE_DEVICE,
    USB_EP_CTR_RX,
    USB_EP_CTR_TX,
    USB_EP_RX_COUNT_MASK,
    USB_EP_SETUP,
    USB_REQ_GET_DESCRIPTOR,
    USB_REQ_SET_ADDRESS,
    USB_REQ_SET_CONFIGURATION,
    EnumerationResult,
    Usb2Packet,
    Usb2SimConfig,
    Usb2SimStatus,
    UsbIstr,
    UsbSetupPacket,
)
from target.gateway import SymbolNotFoundError, TargetGateway


class Usb2Simulator:
    def __init__(
        self,
        gateway: TargetGateway,
        config: Usb2SimConfig = DEFAULT_USB2_SIM_CONFIG,
    ) -> None:
        self.gateway = gateway
        self.config = config
        self.layout = config.layout
        self._base_address: Optional[int] = None

    @property
    def base_address(self) -> int:
        if self._base_address is None:
            self._base_address = self._resolve_base_address()
        return self._base_address

    def status(self) -> Usb2SimStatus:
        return Usb2SimStatus(
            base_address=self.base_address,
            cntr=self._read_u16(self.layout.cntr),
            istr=self._read_u16(self.layout.istr),
            daddr=self._read_u16(self.layout.daddr),
            connected=self.gateway.read_u8(self.base_address + self.layout.connected),
            device_address=self.gateway.read_u8(self.base_address + self.layout.device_address),
        )

    def connect(self) -> None:
        self.gateway.write_u8(self.base_address + self.layout.connected, 1)

    def disconnect(self) -> None:
        self.gateway.write_u8(self.base_address + self.layout.connected, 0)

    def endpoint_register(self, endpoint: int) -> int:
        self._validate_endpoint(endpoint)
        return self._read_u16(self.layout.ep_reg + 2 * endpoint)

    def set_endpoint_register(self, endpoint: int, value: int) -> None:
        self._validate_endpoint(endpoint)
        self._write_u16(self.layout.ep_reg + 2 * endpoint, value)

    def tx_pma_address(self, endpoint: int) -> int:
        self._validate_endpoint(endpoint)
        return self._read_u16(self.layout.tx_pma_addr + 2 * endpoint)

    def rx_pma_address(self, endpoint: int) -> int:
        self._validate_endpoint(endpoint)
        return self._read_u16(self.layout.rx_pma_addr + 2 * endpoint)

    def tx_byte_count(self, endpoint: int) -> int:
        self._validate_endpoint(endpoint)
        return self._read_u16(self.layout.tx_count + 2 * endpoint)

    def inject_rx_packet(self, endpoint: int, payload: bytes) -> None:
        self.inject_packet(Usb2Packet(endpoint=endpoint, payload=payload))

    def inject_setup_packet(self, payload: bytes) -> None:
        self.inject_packet(Usb2Packet(endpoint=0, payload=payload, setup=True))

    def inject_packet(self, packet: Usb2Packet) -> None:
        if packet.direction_in:
            raise ValueError("inject_packet only injects host-to-device packets")

        def write_packet(gateway: TargetGateway) -> None:
            rx_addr_u16 = self.rx_pma_address(packet.endpoint)
            rx_byte_offset = rx_addr_u16 * self.config.pma_access
            if rx_byte_offset + len(packet.payload) > self.config.pma_size:
                raise ValueError("payload too large for simulated PMA region")

            gateway.write(self.base_address + self.layout.pma + rx_byte_offset, packet.payload)

            ep_reg = self.endpoint_register(packet.endpoint)
            ep_reg = (ep_reg & ~USB_EP_RX_COUNT_MASK) | (
                (len(packet.payload) << 10) & USB_EP_RX_COUNT_MASK
            )
            ep_reg |= USB_EP_CTR_RX
            if packet.setup:
                ep_reg |= USB_EP_SETUP
            else:
                ep_reg &= ~USB_EP_SETUP
            self.set_endpoint_register(packet.endpoint, ep_reg)
            self._raise_ctr(packet.endpoint, direction_in=False)

        self.gateway.transaction(write_packet)

    def ack_in_packet(self, endpoint: int) -> None:
        self._validate_endpoint(endpoint)

        def ack(gateway: TargetGateway) -> None:
            ep_reg = self.endpoint_register(endpoint)
            self.set_endpoint_register(endpoint, ep_reg | USB_EP_CTR_TX)
            self._raise_ctr(endpoint, direction_in=True)

        self.gateway.transaction(ack)

    def read_in_packet(self, endpoint: int, max_len: int) -> Usb2Packet:
        return Usb2Packet(
            endpoint=endpoint,
            payload=self.get_tx_packet(endpoint, max_len),
            direction_in=True,
        )

    def get_tx_packet(self, endpoint: int, max_len: int) -> bytes:
        self._validate_endpoint(endpoint)
        tx_addr_u16 = self.tx_pma_address(endpoint)
        tx_count = self.tx_byte_count(endpoint)
        n = min(tx_count, max_len)
        tx_byte_offset = tx_addr_u16 * self.config.pma_access
        if tx_byte_offset + n > self.config.pma_size:
            return b""
        return self.gateway.read(self.base_address + self.layout.pma + tx_byte_offset, n)

    def raise_istr(self, mask: int, endpoint: Optional[int] = None) -> None:
        def raise_bits(gateway: TargetGateway) -> None:
            istr = self._read_u16(self.layout.istr)
            istr |= mask
            if endpoint is not None:
                self._validate_endpoint(endpoint)
                istr = (istr & ~int(UsbIstr.EP_ID)) | (endpoint & int(UsbIstr.EP_ID))
            self._write_u16(self.layout.istr, istr)
            gateway.pend_irq(self.config.irq_number)

        self.gateway.transaction(raise_bits)

    def raise_bus_reset(self) -> None:
        self.raise_istr(int(UsbIstr.RESET))

    def _raise_ctr(self, endpoint: int, direction_in: bool) -> None:
        self._validate_endpoint(endpoint)
        istr = self._read_u16(self.layout.istr)
        istr |= int(UsbIstr.CTR)
        if direction_in:
            istr &= ~int(UsbIstr.DIR)
        else:
            istr |= int(UsbIstr.DIR)
        istr = (istr & ~int(UsbIstr.EP_ID)) | (endpoint & int(UsbIstr.EP_ID))
        self._write_u16(self.layout.istr, istr)
        self.gateway.pend_irq(self.config.irq_number)

    def _resolve_base_address(self) -> int:
        if self.config.resolve_symbol:
            try:
                return self.gateway.resolve_symbol(self.config.symbol)
            except SymbolNotFoundError:
                if self.config.base_address is None:
                    raise
        if self.config.base_address is not None:
            return self.config.base_address
        return self.gateway.resolve_symbol(self.config.symbol)

    def _read_u16(self, offset: int) -> int:
        return self.gateway.read_u16(self.base_address + offset)

    def _write_u16(self, offset: int, value: int) -> None:
        self.gateway.write_u16(self.base_address + offset, value)

    @staticmethod
    def _validate_endpoint(endpoint: int) -> None:
        if endpoint < 0 or endpoint > 7:
            raise ValueError("endpoint must be between 0 and 7")


class Usb2CdcMockHost:
    def __init__(
        self,
        simulator: Usb2Simulator,
        settle_time_s: float = 0.05,
        tx_timeout_s: float = 1.0,
        verbose: bool = False,
    ) -> None:
        self.simulator = simulator
        self.settle_time_s = settle_time_s
        self.tx_timeout_s = tx_timeout_s
        self.verbose = verbose

    def enumerate(self, address: int = 1) -> EnumerationResult:
        self.log("connect")
        self.simulator.connect()

        self.log("bus reset")
        self.simulator.raise_bus_reset()
        self.settle()

        self.log("GET_DESCRIPTOR device[0:8]")
        self.get_descriptor(USB_DESC_TYPE_DEVICE, 0, 8)

        self.log("GET_DESCRIPTOR device")
        device_descriptor = self.get_descriptor(USB_DESC_TYPE_DEVICE, 0, 18)

        self.log(f"SET_ADDRESS {address}")
        self.set_address(address)

        self.log("GET_DESCRIPTOR configuration header")
        cfg_header = self.get_descriptor(USB_DESC_TYPE_CONFIGURATION, 0, 9)
        total_len = 67
        if len(cfg_header) >= 4:
            total_len = int.from_bytes(cfg_header[2:4], "little")

        self.log(f"GET_DESCRIPTOR configuration[{total_len}]")
        configuration_descriptor = self.get_descriptor(USB_DESC_TYPE_CONFIGURATION, 0, total_len)

        self.log("SET_CONFIGURATION 1")
        self.set_configuration(1)

        self.log("CDC SET_CONTROL_LINE_STATE DTR/RTS")
        self.set_control_line_state(0x0003)

        self.log("CDC SET_LINE_CODING 115200 8N1")
        self.set_line_coding(DEFAULT_LINE_CODING_115200_8N1)

        return EnumerationResult(
            address=address,
            device_descriptor=device_descriptor,
            configuration_descriptor=configuration_descriptor,
        )

    def get_descriptor(self, descriptor_type: int, index: int, length: int) -> bytes:
        setup = UsbSetupPacket(
            bm_request_type=0x80,
            b_request=USB_REQ_GET_DESCRIPTOR,
            w_value=((descriptor_type & 0xFF) << 8) | (index & 0xFF),
            w_index=0,
            w_length=length,
        )
        return self.control_in(setup, length)

    def set_address(self, address: int) -> None:
        self.control_out(
            UsbSetupPacket(
                bm_request_type=0x00,
                b_request=USB_REQ_SET_ADDRESS,
                w_value=address,
            )
        )

    def set_configuration(self, configuration: int) -> None:
        self.control_out(
            UsbSetupPacket(
                bm_request_type=0x00,
                b_request=USB_REQ_SET_CONFIGURATION,
                w_value=configuration,
            )
        )

    def set_control_line_state(self, value: int) -> None:
        self.control_out(
            UsbSetupPacket(
                bm_request_type=0x21,
                b_request=CDC_SET_CONTROL_LINE_STATE,
                w_value=value,
                w_index=0,
            )
        )

    def set_line_coding(self, line_coding: bytes) -> None:
        if len(line_coding) != 7:
            raise ValueError("CDC SET_LINE_CODING payload must be 7 bytes")
        self.control_out(
            UsbSetupPacket(
                bm_request_type=0x21,
                b_request=CDC_SET_LINE_CODING,
                w_index=0,
                w_length=len(line_coding),
            ),
            line_coding,
        )

    def cdc_write(self, payload: bytes, max_response: int = 64) -> bytes:
        self.log(f"CDC OUT {payload.hex()}")
        self.simulator.inject_rx_packet(CDC_OUT_EP, payload)
        self.settle()
        response = self.wait_for_tx(CDC_IN_EP, max_response)
        self.log(f"CDC IN {response.hex()}")
        self.simulator.ack_in_packet(CDC_IN_EP)
        self.settle()
        return response

    def control_in(self, setup: UsbSetupPacket, expected_length: int) -> bytes:
        self.simulator.inject_setup_packet(setup.pack())
        self.settle()

        chunks = []
        remaining = expected_length
        while remaining > 0:
            chunk = self.wait_for_tx(EP0, remaining, reject_oversize=True)
            chunks.append(chunk)
            remaining -= len(chunk)
            self.simulator.ack_in_packet(EP0)
            self.settle()
            if len(chunk) == 0 or len(chunk) < 64:
                break

        self.simulator.inject_rx_packet(EP0, b"")
        self.settle()
        return b"".join(chunks)

    def control_out(self, setup: UsbSetupPacket, payload: bytes = b"") -> None:
        self.simulator.inject_setup_packet(setup.pack())
        self.settle()
        if payload:
            self.simulator.inject_rx_packet(EP0, payload)
            self.settle()
        self.simulator.ack_in_packet(EP0)
        self.settle()

    def wait_for_tx(self, endpoint: int, max_len: int, reject_oversize: bool = False) -> bytes:
        deadline = time.monotonic() + self.tx_timeout_s
        while time.monotonic() < deadline:
            tx_count = self.simulator.tx_byte_count(endpoint)
            if max_len == 0:
                return b""
            if tx_count != 0 and (not reject_oversize or tx_count <= max_len):
                return self.simulator.get_tx_packet(endpoint, max_len)
            time.sleep(self.settle_time_s)
        raise TimeoutError(f"timed out waiting for EP{endpoint} IN data")

    def settle(self) -> None:
        time.sleep(self.settle_time_s)

    def log(self, message: str) -> None:
        if self.verbose:
            print(f"[usb2-cdc] {message}")
