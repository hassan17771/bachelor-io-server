```
+-----------------------------------------------------------------------------+
|                          Layer 1: User & API Layer                          |
|  [ CLI / CI-CD Plugin ]      [ Test Script Parser ]     [ Result Reporter ] |
+---------------------------------------+-------------------------------------+
                                        |
+---------------------------------------v-------------------------------------+
|                     Layer 2: Test Orchestration Layer                       |
|  [ Test Execution Engine ]                      [ Configuration Manager ]   |
|                                                (Parses SoC & Mock Lists)    |
+---------------------------------------+-------------------------------------+
                                        |
+---------------------------------------v-------------------------------------+
|                      Layer 3: Target Abstraction Layer                      |
|                           [ Mock Routing Manager ]                          |
|                                       |                                     |
|             +-------------------------+-------------------------+           |
|             |                                                   |           |
|  [ Virtual Register Map (DAP) ]                       [ Physical IO Map ]   |
+-------------+---------------------------------------------------+-----------+
              |                                                   |
+-------------v-----------------------+ +-------------------------v-----------+
| Layer 4a: SWD-DAP Backend           | | Layer 4b: Raspberry Pi HIL Backend  |
| [ PyOCD / OpenOCD Client ]          | | [ RPi RPC / Network Client ]        |
| [ Memory Access Controller ]        | | [ USB Injector ] [ Eth Generator ]  |
+-------------+-----------------------+ +-------------------------+-----------+
              |                                                   |
       +------v------+                                     +------v------+
       |   ARM SoC   |                                     | Raspberry Pi|
       |  (Target)   |                                     |  (HIL Node) |
       +-------------+                                     +-------------+

```
