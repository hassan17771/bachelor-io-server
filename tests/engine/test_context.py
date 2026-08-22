from pathlib import Path

import pytest

from engine import context
from engine.config_parser import ConfigError
from engine.context import (
    FIRMWARE_ELF_ENV,
    TestContext,
    build_context,
    resolve_mmb_base,
)
from peripherals.can.backends.stm32_bxcan import Stm32BxcanAbi
from peripherals.can.bus import CanBus
from peripherals.usb.host.backends.stm32_otgfs import Stm32OtgFsHostAbi
from peripherals.usb.host.cdc import UsbHostCdcDevice
from tests.support.fake_gateway import FakeMmbGateway

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_fake.yaml"
ELF = Path(__file__).parents[1] / "target" / "fixtures" / "f401_usbsim.elf"
CAN_ELF = Path(__file__).parents[1] / "target" / "fixtures" / "f103_cansim.elf"

USBH_SIM_ADDR = 0x200000EC
CAN_SIM_ADDR = 0x20000058


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch):
    monkeypatch.setattr(context, "build_backend", lambda soc: FakeMmbGateway())


@pytest.fixture(autouse=True)
def no_ambient_elf(monkeypatch):
    """Whatever the developer has exported must not decide these tests."""
    monkeypatch.delenv(FIRMWARE_ELF_ENV, raising=False)


def test_builds_a_working_fake_backed_context():
    ctx = build_context(FIXTURE, elf=ELF)

    assert isinstance(ctx, TestContext)
    assert isinstance(ctx.gateway, FakeMmbGateway)
    assert ctx.soc.part == "STM32F407VG"

    usb_host = ctx.peripherals["usb_host"]
    assert isinstance(usb_host, UsbHostCdcDevice)
    assert isinstance(usb_host.abi, Stm32OtgFsHostAbi)
    assert usb_host.abi.gateway is ctx.gateway


def test_the_peripheral_gets_the_address_from_the_elf():
    ctx = build_context(FIXTURE, elf=ELF)

    assert ctx.peripherals["usb_host"].abi.base_address == USBH_SIM_ADDR


def test_a_can_class_config_resolves_can_sim(tmp_path):
    config_path = tmp_path / "can_fake.yaml"
    config_path.write_text(
        "soc:\n"
        "  part: STM32F103C8T6\n"
        "  backend: fake\n"
        "mocks:\n"
        "  - name: can_bus\n"
        "    class: can\n"
    )

    ctx = build_context(config_path, elf=CAN_ELF)

    can_bus = ctx.peripherals["can_bus"]
    assert isinstance(can_bus, CanBus)
    assert isinstance(can_bus.abi, Stm32BxcanAbi)
    assert can_bus.abi.gateway is ctx.gateway
    assert can_bus.abi.base_address == CAN_SIM_ADDR


def test_the_elf_argument_resolves_the_mmb():
    assert resolve_mmb_base(ELF) == USBH_SIM_ADDR


def test_the_environment_supplies_the_elf_when_no_argument_does(monkeypatch):
    monkeypatch.setenv(FIRMWARE_ELF_ENV, str(ELF))

    assert resolve_mmb_base() == USBH_SIM_ADDR


def test_the_argument_wins_over_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(FIRMWARE_ELF_ENV, str(tmp_path / "stale.elf"))

    assert resolve_mmb_base(ELF) == USBH_SIM_ADDR


def test_no_elf_at_all_names_both_ways_of_supplying_one():
    with pytest.raises(ConfigError, match=rf"elf=.*{FIRMWARE_ELF_ENV}"):
        resolve_mmb_base()


def test_a_blank_environment_variable_counts_as_unset(monkeypatch):
    monkeypatch.setenv(FIRMWARE_ELF_ENV, "")

    with pytest.raises(ConfigError, match="no firmware ELF"):
        resolve_mmb_base()


def test_a_missing_elf_is_reported_as_missing(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        resolve_mmb_base(tmp_path / "nope.elf")


def test_an_elf_without_the_mmb_symbol_is_rejected(tmp_path):
    # A real build of something else: the fixture with its MMB symbol renamed,
    # so the table is intact and only `usbh_sim` is absent.
    other = tmp_path / "other_firmware.elf"
    other.write_bytes(ELF.read_bytes().replace(b"usbh_sim\0", b"usbh_zzz\0"))

    with pytest.raises(ConfigError, match="usbh_sim"):
        resolve_mmb_base(other)


def test_a_missing_elf_is_caught_before_the_backend_is_built(monkeypatch):
    # Reaching a probe only to discover there is no ELF is the wrong order.
    monkeypatch.setattr(
        context, "build_backend", lambda soc: pytest.fail("built without an ELF")
    )

    with pytest.raises(ConfigError):
        build_context(FIXTURE)
