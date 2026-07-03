"""Transitional re-exports for the old ``peripherals.usb_host`` module path.

Everything here has moved into ``peripherals/usb/`` (Tasks 2.1-2.4 of the
restructure plan):

* protocol-generic constants -> ``peripherals.usb.common.requests``
* the pipe-level ABI contract -> ``peripherals.usb.host.abi``
* the class-agnostic engine   -> ``peripherals.usb.host.core``
* the CDC class driver        -> ``peripherals.usb.host.cdc``

This shim keeps the old imports alive until Task 2.6 deletes it along with
the last importers of the old path.
"""
from peripherals.usb.common.requests import (  # noqa: F401
    DESC_CONFIGURATION,
    DESC_DEVICE,
    DESC_STRING,
    DIR_IN,
    DIR_OUT,
    EP_TYPE_BULK,
    EP_TYPE_CTRL,
    EP_TYPE_INTR,
    EP_TYPE_ISOC,
    REQ_DIR_DEVICE_TO_HOST,
    REQ_TYPE_CLASS,
    REQ_TYPE_MASK,
    REQ_TYPE_STANDARD,
    SPEED_FULL,
    SPEED_HIGH,
    SPEED_LOW,
    USB_REQ_GET_DESCRIPTOR,
    USB_REQ_SET_ADDRESS,
    USB_REQ_SET_CONFIGURATION,
)
from peripherals.usb.host.abi import (  # noqa: F401
    ChannelSelector,
    UsbChannelInfo,
    UsbHostSimAbi,
    UsbHostSimError,
    UsbHostSimStatus,
    UsbOutPacket,
)
from peripherals.usb.host.core import ControlTransferLog, UsbHostCore  # noqa: F401
from peripherals.usb.host.cdc import (  # noqa: F401
    CDC_BULK_IN_CH,
    CDC_BULK_OUT_CH,
    CDC_GET_LINE_CODING,
    CDC_SET_CONTROL_LINE_STATE,
    CDC_SET_LINE_CODING,
    CdcDeviceProfile,
    CdcEcho,
    EnumerationError,
    EnumerationResult,
    UsbHostCdcDevice,
)
