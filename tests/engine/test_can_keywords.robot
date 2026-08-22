*** Settings ***
Documentation    Every CAN keyword resolves and reaches the mock it names.
Library          engine.keywords.can_keywords.CanKeywords

*** Test Cases ***
Send CAN Frame Carries Its Argument
    Send CAN Frame    0x100    ${{ b'\x01\x02' }}

Receive CAN Frame Returns The Frame
    ${frame}=    Receive CAN Frame
    Should Be Equal As Integers    ${frame.id}    0x100
