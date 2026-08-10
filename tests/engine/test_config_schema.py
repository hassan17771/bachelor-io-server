import dataclasses

import pytest

from engine.config_schema import (
    DirectProbeConfig,
    MockConfig,
    ServerProbeConfig,
    SocConfig,
    TimeoutConfig,
)


def test_server_probe_config_defaults_to_backend_defaults():
    probe = ServerProbeConfig()
    assert probe.host is None
    assert probe.port is None
    assert probe.timeout_s is None


def test_server_probe_config_holds_endpoint():
    probe = ServerProbeConfig(host="10.0.0.4", port=3333, timeout_s=5.0)
    assert probe.host == "10.0.0.4"
    assert probe.port == 3333
    assert probe.timeout_s == 5.0


def test_direct_probe_config_holds_serial():
    assert DirectProbeConfig().serial is None
    assert DirectProbeConfig(serial="000123456789").serial == "000123456789"


def test_timeout_config_holds_value():
    assert TimeoutConfig(default_s=3.0).default_s == 3.0


def test_soc_config_optional_blocks_default_none():
    soc = SocConfig(part="STM32F407VG", backend="fake")
    assert soc.part == "STM32F407VG"
    assert soc.backend == "fake"
    assert soc.probe is None
    assert soc.timeouts is None


def test_soc_config_carries_sub_blocks():
    soc = SocConfig(
        part="STM32F407VG",
        backend="jlink",
        probe=ServerProbeConfig(host="127.0.0.1", port=2331),
        timeouts=TimeoutConfig(default_s=5.0),
    )
    assert soc.probe.port == 2331
    assert soc.timeouts.default_s == 5.0


def test_mock_config_defaults_and_class_rename():
    mock = MockConfig(name="usb_host", peripheral_class="cdc")
    assert mock.name == "usb_host"
    assert mock.peripheral_class == "cdc"
    assert mock.channels is None
    assert mock.device is None


def test_mock_config_carries_device_passthrough():
    mock = MockConfig(
        name="usb_host",
        peripheral_class="cdc",
        channels=8,
        device={"vid": 0x0483, "pid": 0x5740},
    )
    assert mock.channels == 8
    assert mock.device == {"vid": 0x0483, "pid": 0x5740}


def test_dataclasses_are_frozen():
    mock = MockConfig(name="usb_host", peripheral_class="cdc")
    with pytest.raises(dataclasses.FrozenInstanceError):
        mock.name = "other"
