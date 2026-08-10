import re
from pathlib import Path

import pytest

from engine import context, registry
from engine.context import FIRMWARE_ELF_ENV, TestContext
from engine.keywords import usb_host_cdc_keywords
from engine.keywords.usb_host_cdc_keywords import UsbHostCdcKeywords
from engine.test_engine import run_suite
from peripherals.usb.host.abi import UsbHostSimError
from peripherals.usb.host.cdc import CDC_BULK_IN_CH, CDC_BULK_OUT_CH
from tests.support.fake_gateway import FakeMmbGateway

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_fake.yaml"
SUITE = Path(__file__).parent / "test_usb_host_cdc_keywords.robot"
ELF = Path(__file__).parents[1] / "target" / "fixtures" / "f401_usbsim.elf"

BAD_MAGIC = "bad MMB magic 0x00000000 (expected 0x48424255)"
BAD_VERSION = "MMB version 0x0001 != 0x0002"


class RecordingAbi:
    """Records what the keywords call, and can fail `validate` on demand.

    `fails` is how many `validate()` calls raise `error` before one passes --
    a block that publishes its magic late; `None` never publishes at all.
    """

    def __init__(self, calls, error=BAD_MAGIC, fails=0):
        self.calls = calls
        self.error = error
        self.fails = fails

    def validate(self):
        self.calls.append(("validate",))
        if self.fails is None:
            raise UsbHostSimError(self.error)
        if self.fails:
            self.fails -= 1
            raise UsbHostSimError(self.error)

    def disconnect(self):
        self.calls.append(("disconnect",))


class RecordingCdcDevice:
    """Stands in for `UsbHostCdcDevice` and records what the keywords call.

    A real driver would only time out here: nothing plays the target's side
    of the MMB, so there is no port reset to wait for and no SETUP to answer.
    """

    def __init__(self, received=b"pong", validate_error=BAD_MAGIC, validate_fails=0):
        self.calls = []
        self.abi = RecordingAbi(self.calls, error=validate_error, fails=validate_fails)
        self.bulk_in_ch = CDC_BULK_IN_CH
        self.bulk_out_ch = CDC_BULK_OUT_CH
        self.received = received

    def attach(self):
        self.calls.append(("attach",))

    def enumerate(self):
        self.calls.append(("enumerate",))

    def bulk_in(self, channel, data):
        self.calls.append(("bulk_in", channel, bytes(data)))

    def bulk_out(self, channel):
        self.calls.append(("bulk_out", channel))
        return self.received


@pytest.fixture(autouse=True)
def firmware_elf(monkeypatch):
    monkeypatch.setenv(FIRMWARE_ELF_ENV, str(ELF))


@pytest.fixture
def device(monkeypatch):
    mock = RecordingCdcDevice()
    monkeypatch.setattr(context, "build_backend", lambda soc: FakeMmbGateway())
    monkeypatch.setattr(
        context,
        "build_peripherals",
        lambda mocks, soc, gateway, mmb_base: {"usb_host": mock},
    )
    return mock


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset()
    yield
    registry.reset()


def install(mock):
    """Make `mock` the peripheral the keywords resolve, without a suite run."""
    registry.install(
        TestContext(soc=None, gateway=FakeMmbGateway(), peripherals={"usb_host": mock})
    )
    return mock


def test_cdc_keyword_suite_passes(device, tmp_path):
    assert run_suite(SUITE, FIXTURE, outdir=tmp_path).passed


def test_keywords_reach_the_mock_in_order(device, tmp_path):
    run_suite(SUITE, FIXTURE, outdir=tmp_path)

    assert device.calls == [
        ("validate",),
        ("attach",),
        ("enumerate",),
        ("bulk_in", CDC_BULK_IN_CH, b"ping"),
        ("bulk_out", CDC_BULK_OUT_CH),
        ("disconnect",),
    ]


@pytest.mark.parametrize("error", [BAD_MAGIC, BAD_VERSION])
def test_attach_fails_naming_an_invalid_block(monkeypatch, error):
    monkeypatch.setattr(usb_host_cdc_keywords, "HOST_STACK_TIMEOUT_S", 0.0)
    mock = install(RecordingCdcDevice(validate_error=error, validate_fails=None))

    with pytest.raises(UsbHostSimError, match=re.escape(error)):
        UsbHostCdcKeywords().attach_device()

    assert mock.calls == [("validate",)]  # nothing drove the block


def test_attach_waits_for_a_block_published_late(monkeypatch):
    monkeypatch.setattr(usb_host_cdc_keywords, "HOST_STACK_POLL_S", 0.0)
    mock = install(RecordingCdcDevice(validate_fails=2))

    UsbHostCdcKeywords().attach_device()

    assert mock.calls == [
        ("validate",),
        ("validate",),
        ("validate",),
        ("attach",),
        ("enumerate",),
    ]


def test_keywords_need_an_installed_context():
    with pytest.raises(LookupError):
        UsbHostCdcKeywords().attach_device()
