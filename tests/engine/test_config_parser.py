import re
from pathlib import Path

import pytest

from engine.config_parser import ConfigError, ResolvedConfig, parse_config
from engine.config_schema import DirectProbeConfig, ServerProbeConfig

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_fake.yaml"


def test_parses_minimal_fake_fixture():
    config = parse_config(FIXTURE)

    assert isinstance(config, ResolvedConfig)
    assert config.soc.part == "STM32F407VG"
    assert config.soc.backend == "fake"

    assert len(config.mocks) == 1
    mock = config.mocks[0]
    assert mock.name == "usb_host"
    assert mock.peripheral_class == "cdc"
    assert mock.channels == 8
    assert mock.device == {"vid": 0x0483, "pid": 0x5740}


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


_VALID_SOC = "soc:\n  part: STM32F407VG\n  backend: fake\n"
_VALID_MOCKS = "mocks:\n  - name: usb_host\n    class: cdc\n"


@pytest.mark.parametrize(
    "text, match",
    [
        # missing soc:
        (_VALID_MOCKS, "missing the required 'soc:' section"),
        # unknown backend:
        (
            "soc:\n  part: STM32F407VG\n  backend: pyocd\n" + _VALID_MOCKS,
            "unknown backend 'pyocd'",
        ),
        # empty mocks:
        (_VALID_SOC + "mocks: []\n", "exactly one entry under 'mocks:'"),
        # missing mocks:
        (_VALID_SOC, "exactly one entry under 'mocks:'"),
        # more than one mocks: entry
        (
            _VALID_SOC
            + "mocks:\n  - name: a\n    class: cdc\n  - name: b\n    class: cdc\n",
            "multiple 'mocks:' entries are not supported",
        ),
        # unknown class:
        (
            _VALID_SOC + "mocks:\n  - name: usb_host\n    class: hid\n",
            "unknown peripheral class 'hid'",
        ),
        # a pinned MMB address: rejected, so it cannot look like it still works
        (
            "soc:\n  part: STM32F407VG\n  backend: fake\n"
            "  gateway:\n    mmb_base: 0x2001E000\n" + _VALID_MOCKS,
            "'soc.gateway' is no longer supported",
        ),
        # and rejected however it is written, not just when it carries mmb_base
        (
            "soc:\n  part: STM32F407VG\n  backend: fake\n  gateway:\n"
            + _VALID_MOCKS,
            "'soc.gateway' is no longer supported",
        ),
    ],
)
def test_rejects_invalid_config(tmp_path, text, match):
    with pytest.raises(ConfigError, match=re.escape(match)):
        parse_config(_write(tmp_path, text))


def test_probe_defaults_to_server_connection(tmp_path):
    text = (
        "soc:\n"
        "  part: STM32F407VG\n"
        "  backend: jlink\n"
        "  probe:\n"
        "    host: 10.0.0.4\n"
        "    port: 3333\n" + _VALID_MOCKS
    )
    probe = parse_config(_write(tmp_path, text)).soc.probe

    assert probe == ServerProbeConfig(host="10.0.0.4", port=3333)


def test_probe_omitted_is_none(tmp_path):
    assert parse_config(_write(tmp_path, _VALID_SOC + _VALID_MOCKS)).soc.probe is None


def test_parses_direct_probe(tmp_path):
    text = (
        "soc:\n"
        "  part: STM32F407VG\n"
        "  backend: jlink\n"
        "  probe:\n"
        "    connection: direct\n"
        "    serial: '000123456789'\n" + _VALID_MOCKS
    )
    probe = parse_config(_write(tmp_path, text)).soc.probe

    assert probe == DirectProbeConfig(serial="000123456789")


@pytest.mark.parametrize(
    "probe_body, match",
    [
        # unknown connection mode
        ("    connection: usb\n", "unknown probe connection 'usb'"),
        # serial is meaningless when talking to a debug server
        (
            "    connection: server\n    serial: '000123456789'\n",
            "'soc.probe.serial' has no meaning for connection 'server'",
        ),
        # an endpoint is meaningless when driving the probe directly
        (
            "    connection: direct\n    host: 127.0.0.1\n",
            "'soc.probe.host' has no meaning for connection 'direct'",
        ),
        (
            "    connection: direct\n    port: 2331\n",
            "'soc.probe.port' has no meaning for connection 'direct'",
        ),
    ],
)
def test_rejects_fields_foreign_to_the_connection_mode(tmp_path, probe_body, match):
    text = (
        "soc:\n  part: STM32F407VG\n  backend: jlink\n  probe:\n"
        + probe_body
        + _VALID_MOCKS
    )
    with pytest.raises(ConfigError, match=re.escape(match)):
        parse_config(_write(tmp_path, text))
