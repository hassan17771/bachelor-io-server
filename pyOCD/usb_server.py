import pyocd
from pyocd.core.helpers import ConnectHelper
from pyocd.core.session import Session
import time
import ctypes

# -------- Structure definition (must match C layout) ----------
class USB_SimState(ctypes.LittleEndianStructure):
    _fields_ = [
        ("CNTR", ctypes.c_uint16),
        ("ISTR", ctypes.c_uint16),
        ("FNR", ctypes.c_uint16),
        ("DADDR", ctypes.c_uint16),
        ("BTABLE", ctypes.c_uint16),
        ("EP_REG", ctypes.c_uint16 * 8),
        ("pma", ctypes.c_uint8 * 512),
        ("connected", ctypes.c_uint8),
        ("remote_wakeup", ctypes.c_uint8),
        ("device_address", ctypes.c_uint8),
    ]

USB_SIM_BASE = 0x20005000          # from linker script
NVIC_ISPR_BASE = 0xE000E200
USB_IRQ_NUMBER = 19                # USB_LP_CAN1_RX0_IRQn (adjust if needed)
USB_IRQ_MASK = 1 << USB_IRQ_NUMBER
NVIC_ISPR_REG = NVIC_ISPR_BASE + (USB_IRQ_NUMBER // 32) * 4
NVIC_ISPR_BIT = 1 << (USB_IRQ_NUMBER % 32)

class USBSimulator:
    def __init__(self, target_type='stm32f103c8'):
        self.session = None
        self.target = None
        self.connect(target_type)

    def connect(self, target_type='stm32f103c8'):
        # Get list of all connected probes (no serial filtering)
        probes = ConnectHelper.get_all_connected_probes(blocking=False)
        if not probes:
            raise ProbeError("No probe found. Is J‑Link connected?")
        # Use the first probe
        probe = probes[0]
        print(f"Using probe: {probe.description}")

        # Create a session with target_override option
        self.session = Session(probe, options={'target_override': target_type})
        self.session.open()
        self.target = self.session.target
        print(f"Connected to {self.target.part_number}")

    # ---- Memory access helpers ----
    def read_struct(self):
        data = self.target.read_memory_block8(USB_SIM_BASE, ctypes.sizeof(USB_SimState))
        return USB_SimState.from_buffer_copy(bytearray(data))

    def write_struct(self, sim):
        self.target.write_memory_block8(USB_SIM_BASE, list(bytes(sim)))

    def write_ep_reg(self, ep, value):
        addr = USB_SIM_BASE + USB_SimState.EP_REG.offset + ep * 2
        self.target.write_memory(addr, value, 2)

    def read_ep_reg(self, ep):
        addr = USB_SIM_BASE + USB_SimState.EP_REG.offset + ep * 2
        return self.target.read_memory(addr, 2)[0]

    def write_pma(self, offset, data):
        addr = USB_SIM_BASE + USB_SimState.pma.offset + offset
        self.target.write_memory_block8(addr, list(data))

    def read_pma(self, offset, length):
        addr = USB_SIM_BASE + USB_SimState.pma.offset + offset
        return bytes(self.target.read_memory_block8(addr, length))

    # ---- Trigger USB interrupt (externally) ----
    def trigger_usb_interrupt(self):
        """Set the pending bit in NVIC for the USB IRQ."""
        # Read current value, OR with the bit, write back.
        reg_val = self.target.read_memory(NVIC_ISPR_REG, 4)[0]
        reg_val |= NVIC_ISPR_BIT
        self.target.write_memory(NVIC_ISPR_REG, reg_val, 4)

    # ---- Inject an OUT / SETUP packet ----
    def inject_out_packet(self, ep, data):
        # Determine RX PMA address (simplified: fixed offset per endpoint)
        rx_addr = ep * 0x20   # adjust to match your stack's allocation
        if len(data) > 64:
            data = data[:64]
        self.write_pma(rx_addr, data)

        # Update EP register: count in bits 10-15
        ep_reg = self.read_ep_reg(ep)
        count_rx = len(data) << 10
        ep_reg = (ep_reg & ~0xFC00) | (count_rx & 0xFC00)
        # Set CTR_RX bit (typically bit 7 in STM32 USB_EPnR)
        ep_reg |= 0x0080   # CTR_RX
        self.write_ep_reg(ep, ep_reg)

        # Set ISTR: CTR and EP_ID
        sim = self.read_struct()
        sim.ISTR |= 0x0001                   # CTR bit
        sim.ISTR = (sim.ISTR & ~0x1E00) | ((ep & 0x0F) << 8)   # EP_ID field
        self.write_struct(sim)

        # Fire the USB interrupt
        self.trigger_usb_interrupt()

    # ---- Retrieve an IN packet sent by device ----
    def get_in_packet(self, ep, max_len=64, timeout=1.0):
        start = time.time()
        while True:
            ep_reg = self.read_ep_reg(ep)
            # Check CTR_TX bit (typically bit 6 for TX complete)
            if ep_reg & 0x0040:
                break
            if time.time() - start > timeout:
                raise TimeoutError("No IN packet received")
            time.sleep(0.001)

        # Get TX count (bits 10-15)
        tx_count = (ep_reg >> 10) & 0x3F
        tx_addr = ep * 0x20 + 0x10   # fixed offset for TX buffer
        data = self.read_pma(tx_addr, tx_count)

        # Clear CTR_TX by writing 1 to that bit
        self.write_ep_reg(ep, ep_reg | 0x0040)
        return data

    # ---- Additional utilities ----
    def set_connected(self, connected=True):
        sim = self.read_struct()
        sim.connected = 1 if connected else 0
        self.write_struct(sim)

    def reset_device(self):
        # Simulate USB reset: set FRES bit in CNTR
        sim = self.read_struct()
        sim.CNTR |= 0x0004   # FRES
        self.write_struct(sim)
        time.sleep(0.01)
        sim.CNTR &= ~0x0004
        self.write_struct(sim)
        # Trigger RESET interrupt
        sim = self.read_struct()
        sim.ISTR |= 0x0020   # RESET bit
        self.write_struct(sim)
        self.trigger_usb_interrupt()