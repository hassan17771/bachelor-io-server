# Refactors worth doing

Candidates, not commitments. Each is a title, a sentence on why, and a link to
its own plan file once one exists. Nothing here is scheduled or approved.

- **Hardware-test scaffolding** — `tests/support/hardware.py` is presented as
  shared hardware-test support but is hard-wired to USB host over STM32
  OTG_FS, to J-Link, and to one firmware build living outside this
  repository, so nothing else can reuse it.
  See [`hardware-test-scaffolding.md`](hardware-test-scaffolding.md).
