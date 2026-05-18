import pyOCD.usb_server as usb_server

if __name__ == "__main__":
    sim = usb_server.USBSimulator(target_type='stm32f103rc')

    # Connect device
    sim.set_connected(True)

    # Reset the USB peripheral (optional, but good for clean state)
    sim.reset_device()
    time.sleep(0.1)

    # ---- GET DEVICE DESCRIPTOR ----
    setup = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00])
    sim.inject_out_packet(0, setup)
    response = sim.get_in_packet(0)
    print(f"Device descriptor: {response.hex()}")
    assert response[0] == 0x12   # correct length

    # ---- SET ADDRESS ----
    setup = bytes([0x00, 0x05, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00])
    sim.inject_out_packet(0, setup)
    # Status phase: device sends zero-length IN packet
    _ = sim.get_in_packet(0)

    # Verify address was set
    sim_struct = sim.read_struct()
    print(f"Device address: {sim_struct.device_address}")
    assert sim_struct.device_address == 2

    print("Enumeration successful")