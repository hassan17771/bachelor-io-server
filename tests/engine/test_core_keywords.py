from pathlib import Path

import pytest

from engine import context, registry
from engine.context import FIRMWARE_ELF_ENV
from engine.keywords.core_keywords import CoreKeywords
from engine.test_engine import run_suite
from tests.support.fake_gateway import FakeMmbGateway

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_fake.yaml"
SUITE = Path(__file__).parent / "test_core_keywords.robot"
ELF = Path(__file__).parents[1] / "target" / "fixtures" / "f401_usbsim.elf"


@pytest.fixture(autouse=True)
def firmware_elf(monkeypatch):
    monkeypatch.setenv(FIRMWARE_ELF_ENV, str(ELF))


@pytest.fixture
def gateway(monkeypatch):
    fake = FakeMmbGateway()
    monkeypatch.setattr(context, "build_backend", lambda soc: fake)
    return fake


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset()
    yield
    registry.reset()


def test_core_keyword_suite_passes(gateway, tmp_path):
    assert run_suite(SUITE, FIXTURE, outdir=tmp_path).passed


def test_reset_target_reaches_the_gateway(gateway, tmp_path):
    run_suite(SUITE, FIXTURE, outdir=tmp_path)

    assert gateway.resets == [False, False, False]


def test_reset_target_needs_an_installed_context():
    with pytest.raises(LookupError):
        CoreKeywords().reset_target()
