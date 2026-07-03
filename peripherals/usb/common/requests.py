"""USB 2.0 chapter-9 constants: requests, descriptor types, endpoint encoding.

Class-agnostic by design -- nothing CDC/MSC/HID-specific belongs here. Class
drivers keep their own request constants next to their driver code (e.g. the
CDC PSTN requests live with the CDC driver).
"""

# Endpoint direction, as encoded in the MMB ep_dir field and bit 7 of bEndpointAddress.
DIR_OUT = 0
DIR_IN = 1

# Transfer types, matching the USB bmAttributes encoding.
EP_TYPE_CTRL = 0
EP_TYPE_ISOC = 1
EP_TYPE_BULK = 2
EP_TYPE_INTR = 3

# Port speed values the MMB carries (mirror USBH_SpeedTypeDef: FS == 1).
SPEED_HIGH = 0
SPEED_FULL = 1
SPEED_LOW = 2

# Standard device requests (USB 2.0 sec 9.4) carried in the SETUP bRequest.
USB_REQ_SET_ADDRESS = 0x05
USB_REQ_GET_DESCRIPTOR = 0x06
USB_REQ_SET_CONFIGURATION = 0x09

# GET_DESCRIPTOR descriptor types (wValue high byte).
DESC_DEVICE = 0x01
DESC_CONFIGURATION = 0x02
DESC_STRING = 0x03

# bmRequestType fields (SETUP byte 0).
REQ_DIR_DEVICE_TO_HOST = 0x80          # bit 7 set: device -> host data stage
REQ_TYPE_MASK = 0x60                   # bits 6:5: standard / class / vendor
REQ_TYPE_STANDARD = 0x00
REQ_TYPE_CLASS = 0x20
