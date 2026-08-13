from pathlib import Path

import pytest

from engine.config_parser import ConfigError, parse_config
from engine.config_schema import MockConfig, SocConfig
from engine.peripheral_factory import build_peripherals
from peripherals.can.backends.stm32_bxcan import Stm32BxcanAbi
from peripherals.can.bus import CanBus
from peripherals.usb.host.backends.stm32_otgfs import Stm32OtgFsHostAbi
from peripherals.usb.host.cdc import UsbHostCdcDevice
from tests.support.fake_gateway import FakeMmbGateway

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_fake.yaml"
MMB_BASE = 0x200000EC


def test_builds_driver_wired_to_the_gateway():
    config = parse_config(FIXTURE)
    gateway = FakeMmbGateway()

    peripherals = build_peripherals(config.mocks, config.soc, gateway, MMB_BASE)

    usb_host = peripherals["usb_host"]
    assert isinstance(usb_host, UsbHostCdcDevice)
    assert isinstance(usb_host.abi, Stm32OtgFsHostAbi)
    assert usb_host.abi.gateway is gateway
    assert usb_host.abi.base_address == MMB_BASE


def test_builds_can_driver_wired_to_the_gateway():
    soc = SocConfig(part="STM32F103C8T6", backend="fake")
    mocks = [MockConfig(name="can_bus", peripheral_class="can")]
    gateway = FakeMmbGateway()

    peripherals = build_peripherals(mocks, soc, gateway, MMB_BASE)

    can_bus = peripherals["can_bus"]
    assert isinstance(can_bus, CanBus)
    assert isinstance(can_bus.abi, Stm32BxcanAbi)
    assert can_bus.abi.gateway is gateway
    assert can_bus.abi.base_address == MMB_BASE
    assert can_bus.abi.irqn == 20  # USB_LP_CAN1_RX0_IRQn


@pytest.mark.parametrize(
    "soc, mocks, match",
    [
        (
            SocConfig(part="ESP32", backend="fake"),
            [MockConfig(name="usb_host", peripheral_class="cdc")],
            "no ABI available",
        ),
        (
            SocConfig(part="STM32F407VG", backend="fake"),
            [MockConfig(name="usb_host", peripheral_class="hid")],
            "unknown peripheral class 'hid'",
        ),
        (
            SocConfig(part="STM32F407VG", backend="fake"),
            [MockConfig(name="can_bus", peripheral_class="can")],
            "no ABI available",
        ),
    ],
)
def test_unresolvable_mock_raises_config_error(soc, mocks, match):
    with pytest.raises(ConfigError, match=match):
        build_peripherals(mocks, soc, FakeMmbGateway(), MMB_BASE)
