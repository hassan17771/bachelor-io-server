from pathlib import Path

import pytest

from engine import context, registry
from engine.config_parser import ConfigError
from engine.context import FIRMWARE_ELF_ENV
from engine.test_engine import RunResult, run_suite
from tests.support.fake_gateway import FakeMmbGateway

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_fake.yaml"
SUITE = Path(__file__).parent / "test_engine_smoke.robot"
ELF = Path(__file__).parents[1] / "target" / "fixtures" / "f401_usbsim.elf"

USBH_SIM_ADDR = 0x200000EC


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch):
    monkeypatch.setattr(context, "build_backend", lambda soc: FakeMmbGateway())


@pytest.fixture(autouse=True)
def no_ambient_elf(monkeypatch):
    monkeypatch.delenv(FIRMWARE_ELF_ENV, raising=False)


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset()
    yield
    registry.reset()


def test_run_suite_passes_the_smoke_suite(tmp_path):
    result = run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF)

    assert isinstance(result, RunResult)
    assert result.return_code == 0
    assert result.passed


def test_run_suite_counts_the_tests(tmp_path):
    result = run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF)

    assert result.pass_count == 1
    assert result.fail_count == 0


def test_run_suite_writes_robot_artifacts(tmp_path):
    result = run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF)

    assert result.output_xml == tmp_path / "output.xml"
    assert result.log_html == tmp_path / "log.html"
    assert result.report_html == tmp_path / "report.html"
    assert result.output_xml.is_file()
    assert result.log_html.is_file()
    assert result.report_html.is_file()


def test_run_suite_twice_in_one_process(tmp_path):
    first = run_suite(SUITE, FIXTURE, outdir=tmp_path / "first", elf=ELF)
    second = run_suite(SUITE, FIXTURE, outdir=tmp_path / "second", elf=ELF)

    assert first.passed
    assert second.passed


def test_run_suite_clears_the_registry_afterwards(tmp_path):
    run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF)

    with pytest.raises(LookupError):
        registry.gateway()


def test_run_suite_hands_the_elf_address_to_the_peripherals(tmp_path, monkeypatch):
    seen = []
    build = context.build_peripherals

    def recording(mocks, soc, gateway, mmb_base):
        seen.append(mmb_base)
        return build(mocks, soc, gateway, mmb_base)

    monkeypatch.setattr(context, "build_peripherals", recording)

    assert run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF).passed
    assert seen == [USBH_SIM_ADDR]


def test_run_suite_without_an_elf_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        run_suite(SUITE, FIXTURE, outdir=tmp_path)


def test_registry_is_cleared_even_when_the_run_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engine.test_engine.robot.run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        run_suite(SUITE, FIXTURE, outdir=tmp_path, elf=ELF)

    with pytest.raises(LookupError):
        registry.gateway()
