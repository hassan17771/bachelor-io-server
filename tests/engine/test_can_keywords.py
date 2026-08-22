import re
from pathlib import Path

import pytest

from engine import context, registry
from engine.context import FIRMWARE_ELF_ENV, TestContext
from engine.keywords import can_keywords
from engine.keywords.can_keywords import CanKeywords
from engine.test_engine import run_suite
from peripherals.can.abi import CanFrame, CanSimError
from tests.support.fake_gateway import FakeMmbGateway

FIXTURE = Path(__file__).parent / "fixtures" / "can_fake.yaml"
SUITE = Path(__file__).parent / "test_can_keywords.robot"
ELF = Path(__file__).parents[1] / "target" / "fixtures" / "f103_cansim.elf"

BAD_MAGIC = "bad MMB magic 0x00000000 (expected 0x534E4143)"


class RecordingAbi:
    """Records `validate()` calls, and can fail it on demand.

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
            raise CanSimError(self.error)
        if self.fails:
            self.fails -= 1
            raise CanSimError(self.error)


class RecordingCanBus:
    """Stands in for `CanBus` and records what the keywords call."""

    def __init__(self, received=None, validate_error=BAD_MAGIC, validate_fails=0):
        self.calls = []
        self.abi = RecordingAbi(self.calls, error=validate_error, fails=validate_fails)
        self.received = received or CanFrame(id=0x100, data=b"\x01\x02", mailbox=0)

    def send_frame(self, id, data=b"", ide=False, rtr=False):
        self.calls.append(("send_frame", id, bytes(data), ide, rtr))

    def receive_frame(self, timeout_s=3.0):
        self.calls.append(("receive_frame", timeout_s))
        return self.received


@pytest.fixture(autouse=True)
def firmware_elf(monkeypatch):
    monkeypatch.setenv(FIRMWARE_ELF_ENV, str(ELF))


@pytest.fixture
def bus(monkeypatch):
    mock = RecordingCanBus()
    monkeypatch.setattr(context, "build_backend", lambda soc: FakeMmbGateway())
    monkeypatch.setattr(
        context,
        "build_peripherals",
        lambda mocks, soc, gateway, mmb_base: {"can_bus": mock},
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
        TestContext(soc=None, gateway=FakeMmbGateway(), peripherals={"can_bus": mock})
    )
    return mock


def test_can_keyword_suite_passes(bus, tmp_path):
    assert run_suite(SUITE, FIXTURE, outdir=tmp_path).passed


def test_keywords_reach_the_mock_in_order(bus, tmp_path):
    run_suite(SUITE, FIXTURE, outdir=tmp_path)

    assert bus.calls == [
        ("validate",),
        ("send_frame", 0x100, b"\x01\x02", False, False),
        ("validate",),
        ("receive_frame", 3.0),
    ]


@pytest.mark.parametrize("error", [BAD_MAGIC])
def test_send_fails_naming_an_invalid_block(monkeypatch, error):
    monkeypatch.setattr(can_keywords, "CAN_STACK_TIMEOUT_S", 0.0)
    mock = install(RecordingCanBus(validate_error=error, validate_fails=None))

    with pytest.raises(CanSimError, match=re.escape(error)):
        CanKeywords().send_can_frame(0x100)

    assert mock.calls == [("validate",)]  # nothing drove the block


def test_send_waits_for_a_block_published_late(monkeypatch):
    monkeypatch.setattr(can_keywords, "CAN_STACK_POLL_S", 0.0)
    mock = install(RecordingCanBus(validate_fails=2))

    CanKeywords().send_can_frame(0x100)

    assert mock.calls == [
        ("validate",),
        ("validate",),
        ("validate",),
        ("send_frame", 0x100, b"", False, False),
    ]


def test_keywords_need_an_installed_context():
    with pytest.raises(LookupError):
        CanKeywords().send_can_frame(0x100)
