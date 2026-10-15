# Stage 1 Troubleshooting

All steps below are observational. Do not change boot overlays, parameters, firmware, modules, or wiring while powered merely to make a test pass.

## No MAVLink heartbeat

1. Inspect `serial.json`: confirm the selected path exists, note its symlink target, and check read/write permissions and group membership.
2. Confirm whether the actual link is USB (`/dev/ttyACM*`), a USB-UART adapter (`/dev/ttyUSB*`), or Pi GPIO UART (`/dev/serial0`/`ttyAMA*`). Do not assume the historical `/dev/serial0` path is current.
3. For GPIO UART, compare the recorded physical pinout with Pi TX-to-Pixhawk RX, Pi RX-to-Pixhawk TX, and common ground. Verify voltage and connector orientation from authoritative board documentation before powering.
4. Review `/boot/firmware/config.txt` or `/boot/config.txt` evidence for `enable_uart` and overlays. Review cmdline evidence for `console=serial0,...`, `console=ttyAMA...`, or another console conflict.
5. Ensure no other process (ground-control software, mavlink-router, serial console, or an older service) owns the selected port. A read-only inspection can use `lsof /dev/serial0` or `fuser /dev/serial0` when installed.
6. Retry only the known endpoint at its documented baud. Common candidates are 57600, 115200, and 921600, but an absent heartbeat is not permission to alter `SERIAL*` parameters.
7. Preserve the failed `mavlink.json`; its endpoint, baud, and exact exception are evidence.

## Heartbeat but missing firmware or telemetry

- A heartbeat proves a MAVLink speaker, not every peripheral. Keep firmware as `UNKNOWN` if `AUTOPILOT_VERSION` is absent.
- GPS may legitimately report fix type below 3 indoors. Move the powered bench setup to a suitable sky view only if safe; do not fabricate a 3D fix.
- `GPS_RAW_INT`, `ATTITUDE`, `GLOBAL_POSITION_INT`, `SYS_STATUS`, and `EKF_STATUS_REPORT` may have different stream rates. Increase the passive window up to 30 seconds before considering configuration work in a separately approved stage.
- If the parameter list is incomplete, preserve the count and raw snapshot. Do not write parameters to repair it in Stage 1.

## OV9281 not enumerated

1. Preserve `camera.json` and `camera_dmesg.txt` from one clean run.
2. Power down before inspecting the CSI ribbon. Confirm connector selection, orientation, contact seating, and that the cable/adapter matches the Pi 5 connector and camera board.
3. Review the active boot config captured by the tool. Note `camera_auto_detect` and any `dtoverlay=ov9281...` entries; do not repeatedly add competing overlays.
4. Distinguish states:
   - `DETECTED`: a camera utility names OV9281/OV9282.
   - `DRIVER_LOADED_BUT_SENSOR_NOT_RESPONDING`: the kernel names the driver/sensor and logs probe/error evidence.
   - `CONFIGURATION_PRESENT_BUT_NOT_DETECTED`: an overlay/auto-detect setting exists but enumeration does not.
   - `NO_CAMERA_DEVICE`: no camera enumeration or relevant device evidence.
   - `UNKNOWN`: evidence is ambiguous or tools/log permissions are insufficient.
5. Generic `/dev/video*` entries can belong to codecs or other devices. Use rpicam/libcamera and media topology to prove the CSI sensor.
6. Do not `modprobe -r`, force an overlay, or edit boot files until the retained evidence has been reviewed and an explicit change is approved.

## Diagnostic command permissions

`dmesg` may be restricted for non-root users. The tool records that failure instead of silently ignoring it. Prefer reviewing normal-user evidence first; if kernel-log access is essential, rerun only the camera collector with appropriately authorized access according to the Pi's administration policy.

