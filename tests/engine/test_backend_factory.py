from pathlib import Path

import pytest

from backends import JLinkConnectionConfig, OpenOcdConnectionConfig
from engine import backend_factory
from engine.backend_factory import ALLOW_FAKE_ENV, build_backend
from engine.config_parser import ConfigError, parse_config
from engine.config_schema import DirectProbeConfig, ServerProbeConfig, SocConfig
from tests.support.fake_gateway import FakeMmbGateway

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_fake.yaml"


class _FakeBackend:
    """Stands in for a real gateway: records the connection config it got."""

    def __init__(self, config):
        self.config = config


class _FakeJLink(_FakeBackend):
    pass


class _FakeOpenOcd(_FakeBackend):
    pass


@pytest.fixture(autouse=True)
def no_real_probes(monkeypatch):
    monkeypatch.setattr(backend_factory, "JLinkBackend", _FakeJLink)
    monkeypatch.setattr(backend_factory, "OpenOcdBackend", _FakeOpenOcd)


def _soc(backend, probe=None):
    return SocConfig(part="STM32F407VG", backend=backend, probe=probe)


@pytest.mark.parametrize(
    "backend, expected",
    [
        ("jlink", _FakeJLink),
        ("openocd", _FakeOpenOcd),
    ],
)
def test_selects_backend_class(backend, expected):
    assert isinstance(build_backend(_soc(backend)), expected)


@pytest.mark.parametrize("backend", ["pyocd", "renode"])
def test_unimplemented_backend_raises(backend):
    with pytest.raises(NotImplementedError, match=backend):
        build_backend(_soc(backend))


@pytest.mark.parametrize(
    "backend, defaults",
    [
        ("jlink", JLinkConnectionConfig()),
        ("openocd", OpenOcdConnectionConfig()),
    ],
)
def test_absent_probe_keeps_backend_defaults(backend, defaults):
    config = build_backend(_soc(backend)).config

    assert config == defaults


@pytest.mark.parametrize(
    "backend, port_field",
    [
        ("jlink", "gdb_port"),
        ("openocd", "telnet_port"),
    ],
)
def test_server_probe_overrides_endpoint(backend, port_field):
    probe = ServerProbeConfig(host="10.0.0.4", port=3333, timeout_s=9.0)

    config = build_backend(_soc(backend, probe)).config

    assert config.host == "10.0.0.4"
    assert getattr(config, port_field) == 3333
    assert config.timeout_s == 9.0


@pytest.mark.parametrize(
    "backend, defaults, port_field",
    [
        ("jlink", JLinkConnectionConfig(), "gdb_port"),
        ("openocd", OpenOcdConnectionConfig(), "telnet_port"),
    ],
)
def test_unset_probe_fields_fall_through(backend, defaults, port_field):
    config = build_backend(_soc(backend, ServerProbeConfig(host="10.0.0.4"))).config

    assert config.host == "10.0.0.4"
    assert getattr(config, port_field) == getattr(defaults, port_field)
    assert config.timeout_s == defaults.timeout_s


@pytest.mark.parametrize("backend", ["jlink", "openocd"])
def test_direct_probe_is_not_implemented(backend):
    probe = DirectProbeConfig(serial="000123456789")

    with pytest.raises(NotImplementedError, match="direct"):
        build_backend(_soc(backend, probe))


@pytest.fixture
def fake_allowed(monkeypatch):
    monkeypatch.setenv(ALLOW_FAKE_ENV, "1")


def test_fake_backend_builds_from_a_config_file(fake_allowed):
    gateway = build_backend(parse_config(FIXTURE).soc)

    assert isinstance(gateway, FakeMmbGateway)


def test_fake_backend_is_refused_unless_opted_into(monkeypatch):
    monkeypatch.delenv(ALLOW_FAKE_ENV, raising=False)

    with pytest.raises(ConfigError, match=ALLOW_FAKE_ENV):
        build_backend(_soc("fake"))


@pytest.mark.parametrize("value", ["", "0", "true"])
def test_only_an_exact_1_opts_into_the_fake_backend(monkeypatch, value):
    monkeypatch.setenv(ALLOW_FAKE_ENV, value)

    with pytest.raises(ConfigError, match=ALLOW_FAKE_ENV):
        build_backend(_soc("fake"))


def test_fake_gateway_records_reset_instead_of_refusing_it(fake_allowed):
    gateway = build_backend(_soc("fake"))

    gateway.reset()
    gateway.reset(halt_after=True)

    assert gateway.resets == [False, True]
