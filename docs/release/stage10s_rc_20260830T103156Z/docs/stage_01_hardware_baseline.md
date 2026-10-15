# Stage 1 Hardware Baseline Procedure

## Bench safety

1. Treat the aircraft as if propellers are installed. Keep clear of motors and restrain the frame appropriately.
2. Do not arm, run a motor test, change mode, upload a mission, calibrate, alter parameters, or flash firmware.
3. Power and wire the Pixhawk according to its documented connector pinout. Confirm common ground and voltage levels before attaching a Raspberry Pi UART. Do not infer wire function from color.
4. Choose a single known MAVLink path. The diagnostic tool never guesses by opening every port.

## Collection sequence

1. Create and activate the Python environment from the README.
2. Create one timestamp output directory.
3. Run `collect_pi_diagnostics.sh`. Inspect the Pi model/RAM and `serial.json` before continuing.
4. Record the actual wiring in bench notes: Pi connector/pins, Pixhawk connector, cable/pinout, power sources, and whether USB or UART is used.
5. For UART, check whether `/dev/serial0` resolves to `/dev/ttyAMA*`, whether the user has the required group, and whether `console=` occupies that UART. Do not edit boot files during collection.
6. Run `collect_mavlink_diagnostics.sh` against only the verified endpoint and baud. A successful heartbeat starts a passive 15-second telemetry window, followed by information-only firmware and parameter-list requests.
7. Run `collect_camera_diagnostics.sh` once. It does not alter overlays or kernel modules.
8. Generate `STAGE_1_REPORT.md` from the same output directory.

## Verification criteria

- Pi: `/proc/device-tree/model` names Raspberry Pi 5 and measured memory is consistent with the 8 GB model.
- Serial: the selected device exists, its symlink target is retained, permissions are visible, and no conflicting serial console is left unexplained.
- MAVLink: at least one valid `HEARTBEAT` arrives from the expected system/component; identity must indicate ArduPilot. A heartbeat alone cannot prove the physical board is a Pixhawk 2.4.8.
- Firmware: an actual `AUTOPILOT_VERSION.flight_sw_version` is received. Do not substitute a desired version.
- GPS: `GPS_RAW_INT` is present. Fix type, satellites, eph/epv and position are reported as observed, including zero/no-fix values.
- Position/attitude/battery: their corresponding live messages are retained. Missing fields remain unknown.
- Heading/compass: a live heading can be verified as telemetry, but compass hardware health additionally needs EKF/health and parameter context.
- Parameters: `parameters.param` exists and contains received `PARAM_VALUE` records. Missing groups are not fabricated.
- Camera: rpicam/libcamera identifies OV9281/OV9282. Generic `/dev/video*` nodes alone do not prove the sensor identity.

## Evidence to return from the Pi

Return the complete timestamp directory and generated report. Also provide a photo or written record of the actual Pixhawk-to-Pi cable path and the physical Pixhawk label; MAVLink typically identifies firmware family and board capabilities but may not uniquely prove a clone/controller model.

