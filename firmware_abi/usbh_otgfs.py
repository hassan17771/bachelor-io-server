from __future__ import annotations

# STM32 OTG_FS USB-host simulation ABI (firmware-side layout constants).
#
# NOTE: This file is fleshed out in task 1.2 (UsbhSimLayout, URB states,
# CDC descriptors, etc.). For task 1.1.1 it carries only the pinned MMB base
# address, which the firmware fixes via the '.usbh_sim' linker section.

# Fixed RAM address of the UsbhSimBlock MMB.
# It is the first block in the reserved 8 KB '.mmb' region at the top of RAM:
#   _mmb_origin = ORIGIN(RAM) + LENGTH(RAM) - _mmb_region_size
#               = 0x20000000 + 0x20000 - 0x2000 = 0x2001E000
# Pinned by STM32F411CEUX_FLASH.ld so it is stable across builds and resolvable
# without the ELF file. Future peripheral MMBs append above this address.
# Override with WCET_USB_HOST_BASE / a constructor arg for a different MCU.
USBH_SIM_BASE = 0x2001E000
