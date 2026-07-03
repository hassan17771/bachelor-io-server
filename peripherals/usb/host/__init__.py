"""USB host-mode simulation: the MCU is the USB host, the IO server mocks a device.

``abi.py`` defines the per-MCU contract, ``backends/`` its concrete
implementations, and the class drivers (``cdc.py``, ...) sit on top.
"""
