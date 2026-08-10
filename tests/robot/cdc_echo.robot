*** Settings ***
Documentation    The target's CDC application echoes every packet it receives:
...              whatever the mock sends must come straight back. Which board
...              runs it is the config's business, not the suite's.
Library          engine.keywords.core_keywords.CoreKeywords
Library          engine.keywords.usb_host_cdc_keywords.UsbHostCdcKeywords

*** Variables ***
${PAYLOAD}       ${{ b'ping' }}

*** Test Cases ***
Device Echoes What It Receives
    Reset Target
    Attach Device
    Send Packet    ${PAYLOAD}
    ${echo}=    Receive Packet
    Should Be Equal    ${echo}    ${PAYLOAD}
    [Teardown]    Detach Device
