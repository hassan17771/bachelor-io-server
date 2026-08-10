*** Settings ***
Documentation    Core keywords need a connected target and nothing else.
Library          engine.keywords.core_keywords.CoreKeywords

*** Test Cases ***
Delay Waits
    Delay    0.01

Reset Target Reboots The Device
    Reset Target

Keywords Still Work After A Reset
    Reset Target
    Delay    0.01
    Reset Target
