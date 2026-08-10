"""reset(halt_after=False) must leave the target running, not just reset it.

Both debug servers halt at the reset vector after a plain reset command
regardless of the flag; only an explicit resume leaves the core executing.
Discovered when a suite's `Reset Target` left an F401 parked at
Reset_Handler forever, since nothing resumed it afterwards.
"""
from backends.jlink import JLinkBackend
from backends.openocd import OpenOcdBackend


def test_jlink_reset_without_halt_resumes(monkeypatch):
    backend = JLinkBackend()
    calls = []
    monkeypatch.setattr(backend.client, "monitor_command", lambda cmd: calls.append(cmd) or "")

    backend.reset(halt_after=False)

    assert calls == ["reset", "go"]


def test_jlink_reset_with_halt_does_not_resume(monkeypatch):
    backend = JLinkBackend()
    calls = []
    monkeypatch.setattr(backend.client, "monitor_command", lambda cmd: calls.append(cmd) or "")

    backend.reset(halt_after=True)

    assert calls == ["reset halt"]


def test_openocd_reset_without_halt_resumes(monkeypatch):
    backend = OpenOcdBackend()
    calls = []
    monkeypatch.setattr(backend.client, "command", lambda cmd: calls.append(cmd) or "")

    backend.reset(halt_after=False)

    assert calls == ["reset", "resume"]


def test_openocd_reset_with_halt_does_not_resume(monkeypatch):
    backend = OpenOcdBackend()
    calls = []
    monkeypatch.setattr(backend.client, "command", lambda cmd: calls.append(cmd) or "")

    backend.reset(halt_after=True)

    assert calls == ["reset halt"]
