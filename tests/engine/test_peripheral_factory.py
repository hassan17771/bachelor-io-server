from pathlib import Path

import pytest

from engine.config_parser import ConfigError, parse_config
from engine.config_schema import MockConfig, SocConfig
from engine.peripheral_factory import build_peripherals
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
    ],
)
def test_unresolvable_mock_raises_config_error(soc, mocks, match):
    with pytest.raises(ConfigError, match=match):
        build_peripherals(mocks, soc, FakeMmbGateway(), MMB_BASE)
