"""CanBus against a fake CanSimAbi double -- no gateway, no MMB.

Proves CanBus is a thin, correctly-wired driver over CanSimAbi's contract:
send_frame builds the right CanFrame and hands it to inject_frame; receive_frame
drains wait_for_tx and acks the mailbox it names. No backend exists yet
(Task 2.1), so the double is a plain CanSimAbi subclass rather than a
FakeMmbGateway-backed one.
"""
from __future__ import annotations

import unittest
from typing import List

from peripherals.can.abi import CanFrame, CanSimAbi, CanSimStatus
from peripherals.can.bus import CanBus


class FakeCanSimAbi(CanSimAbi):
    def __init__(self) -> None:
        self.injected: List[CanFrame] = []
        self.acked: List[int] = []
        self.tx_queue: List[CanFrame] = []

    def validate(self) -> CanSimStatus:
        return CanSimStatus(magic=0, version=0, block_size=0)

    def inject_frame(self, frame: CanFrame) -> None:
        self.injected.append(frame)

    def wait_for_tx(self, timeout_s: float = 3.0) -> CanFrame:
        return self.tx_queue.pop(0)

    def ack_tx(self, mailbox: int) -> None:
        self.acked.append(mailbox)


class CanBusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.abi = FakeCanSimAbi()
        self.bus = CanBus(self.abi)

    def test_send_frame_injects_a_standard_data_frame(self) -> None:
        self.bus.send_frame(0x123, b"\x01\x02")

        self.assertEqual(self.abi.injected, [CanFrame(id=0x123, data=b"\x01\x02")])

    def test_send_frame_passes_through_ide_and_rtr(self) -> None:
        self.bus.send_frame(0x456, ide=True, rtr=True)

        self.assertEqual(
            self.abi.injected, [CanFrame(id=0x456, ide=True, rtr=True)]
        )

    def test_receive_frame_returns_the_tx_frame_and_acks_its_mailbox(self) -> None:
        self.abi.tx_queue.append(CanFrame(id=0x321, data=b"\xaa", mailbox=2))

        frame = self.bus.receive_frame(timeout_s=0.5)

        self.assertEqual(frame, CanFrame(id=0x321, data=b"\xaa", mailbox=2))
        self.assertEqual(self.abi.acked, [2])


if __name__ == "__main__":
    unittest.main()
