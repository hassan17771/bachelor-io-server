"""Keywords that hold for every suite, whatever the config declares."""
from __future__ import annotations

import time

from engine import registry


class CoreKeywords:
    """Peripheral-independent keywords: none of them needs a mock configured.

    The name is `Reset Target`, not `Reset`, because it reboots the device
    under test -- the engine's own teardown is not reachable from a suite.
    """

    def delay(self, seconds: float) -> None:
        """Wait `seconds` before running the next keyword."""
        time.sleep(seconds)

    def reset_target(self) -> None:
        """Reboot the device under test and leave it running."""
        registry.gateway().reset()
