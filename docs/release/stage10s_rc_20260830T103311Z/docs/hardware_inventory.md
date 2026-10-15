# Hardware Inventory and Evidence State

This inventory records the supplied baseline. It is not a physical inspection result.

| Capability | Configured item | Baseline state | Verification evidence required |
|---|---|---|---|
| Autopilot | ArduPilot/ArduCopter | CONFIGURED | ArduPilot heartbeat plus `AUTOPILOT_VERSION` |
| Flight controller | Pixhawk 2.4.8 | CONFIGURED | Board identity/firmware evidence and physical label inspection |
| GPS | u-blox NEO-M8N | CONFIGURED | `GPS_RAW_INT`, fix type, satellite count, eph/epv |
| Precision GNSS | False | CONFIGURED | Not applicable to baseline |
| Compass | GPS-module electronic compass | CONFIGURED | Heading and compass/EKF health evidence; parameter presence alone is insufficient |
| Camera | Arducam OV9281 monochrome | CONFIGURED | rpicam/libcamera sensor enumeration and kernel/media evidence |
| Global shutter | True for OV9281 | CONFIGURED | Confirmed sensor identity |
| Edge compute | Raspberry Pi 5, 8 GB | CONFIGURED | Device-tree model and RAM measurement |
| AI accelerator | None | CONFIGURED | Inventory inspection |
| Rangefinder | None | CONFIGURED | Inventory inspection |
| Payload | Unknown | UNKNOWN | Physical inspection; no actuation in Stage 1 |
| Payload feedback | Unknown | UNKNOWN | Physical inspection; no actuation in Stage 1 |
| Ground node | Raspberry Pi 4, 4 GB | CONFIGURED | Separate host diagnostic later |
| Fleet | One aircraft | CONFIGURED | Inventory inspection |
| Network | No external internet required | CONFIGURED | Offline integration test in a later stage |

## Status meanings

- `CONFIGURED`: declared intent or known bill-of-materials entry; not live evidence.
- `DETECTED`: enumerated by the operating system or protocol.
- `VERIFIED`: expected identity or behavior supported by retained evidence.
- `FAILED`: the test ran and observed a negative result or error.
- `PARTIAL`: useful evidence exists but does not meet the complete criterion.
- `UNKNOWN`: insufficient knowledge, often because a property cannot be inferred safely.
- `NOT_TESTED`: no applicable test result was collected.

