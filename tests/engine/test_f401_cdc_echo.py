"""The f401 echo suite on the fake backend: the wiring, not the echo.

Nothing on this backend answers a SETUP or arms an IN, so no test here can
prove the target echoes -- a scripted stand-in plays the device instead. What
these tests do prove is that the suite parses, its keywords resolve, the f401
config loads, and the ELF lookup hands the peripheral the right address.
"""
from pathlib import Path

import pytest

from engine import context, registry
from engine.test_engine import run_suite
from peripherals.usb.host.cdc import CDC_BULK_IN_CH, CDC_BULK_OUT_CH
from tests.support.fake_gateway import FakeMmbGateway

SUITE = Path(__file__).parents[1] / "robot" / "cdc_echo.robot"
FIXTURE = Path(__file__).parent / "fixtures" / "f401_fake.yaml"
ELF = Path(__file__).parents[1] / "target" / "fixtures" / "f401_usbsim.elf"

PAYLOAD = b"ping"
USBH_SIM_ADDR = 0x200000EC


def echo(data):
    return data


class ScriptedAbi:
    """The MMB half of the stand-in: a block that is always valid."""

    def __init__(self):
        self.base_address = None
        self.disconnected = False

    def validate(self):
        pass

    def disconnect(self):
        self.disconnected = True


class ScriptedCdcDevice:
    """Plays the target's side of the echo without a target.

    `reply` turns the packet the suite sent into the one the device sends
    back; the identity is a working echo, anything else is a broken one.
    """

    def __init__(self, reply):
        self.reply = reply
        self.abi = ScriptedAbi()
        self.bulk_in_ch = CDC_BULK_IN_CH
        self.bulk_out_ch = CDC_BULK_OUT_CH
        self.attached = False
        self.enumerated = False
        self.sent = None

    def attach(self):
        self.attached = True

    def enumerate(self):
        self.enumerated = True

    def bulk_in(self, channel, data):
        self.sent = bytes(data)

    def bulk_out(self, channel):
        return self.reply(self.sent)


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def gateway(monkeypatch):
    fake = FakeMmbGateway()
    monkeypatch.setattr(context, "build_backend", lambda soc: fake)
    return fake


@pytest.fixture
def scripted(monkeypatch, gateway):
    """Build the suite's peripheral from a script instead of a real driver."""

    def install(reply=echo):
        device = ScriptedCdcDevice(reply)

        def build_peripherals(mocks, soc, gateway, mmb_base):
            device.abi.base_address = mmb_base
            return {"usb_host": device}

        monkeypatch.setattr(context, "build_peripherals", build_peripherals)
        return device

    return install


def test_echo_suite_passes_against_an_echoing_device(scripted, tmp_path):
    scripted()

    result = run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF)

    assert result.passed
    assert result.pass_count == 1


def test_echo_suite_fails_when_other_bytes_come_back(scripted, tmp_path):
    scripted(reply=lambda data: b"pong")

    result = run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF)

    assert not result.passed
    assert result.fail_count == 1


def test_the_suite_drives_the_device_through_the_whole_exchange(
    scripted, gateway, tmp_path
):
    device = scripted()

    run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF)

    assert gateway.resets == [False]
    assert device.attached
    assert device.enumerated
    assert device.sent == PAYLOAD
    assert device.abi.disconnected


def test_the_fixture_and_elf_place_the_mmb(scripted, tmp_path):
    device = scripted()

    run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF)

    assert device.abi.base_address == USBH_SIM_ADDR
