*** Settings ***
Documentation    Every CDC keyword resolves and reaches the mock it names.
Library          engine.keywords.usb_host_cdc_keywords.UsbHostCdcKeywords

*** Test Cases ***
Attach Device Enumerates
    Attach Device

Send Packet Carries Its Argument
    Send Packet    ping

Receive Packet Returns The Payload
    ${data}=    Receive Packet
    Should Be Equal    ${data}    ${{ b'pong' }}

Detach Device Unplugs
    Detach Device
