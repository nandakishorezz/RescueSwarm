# Stage 6 physical wiring record

Complete this record by inspecting the actual prototype. Historical notes are not proof.

## Mandatory safety

- [ ] **All propellers removed**
- [ ] Pixhawk has its appropriate power source
- [ ] Raspberry Pi 5 has an appropriate independent supply
- [ ] Pi is not assumed to be powered from TELEM 5 V
- [ ] Shared signal ground exists where UART requires it

## Operator-verified wiring

| Item | Record |
|---|---|
| Pixhawk physical label/photo | UNKNOWN |
| Pixhawk port | TELEM1 / TELEM2 / USB / UNKNOWN |
| Pi device | `/dev/...` / UNKNOWN |
| Baud | UNKNOWN |
| Pixhawk TX connected to | UNKNOWN |
| Pixhawk RX connected to | UNKNOWN |
| Ground connected to | UNKNOWN |
| TELEM 5 V connected | YES / NO / UNKNOWN |
| Pi supply and rating | UNKNOWN |
| OV9281 board label | UC-580 Rev.B / OTHER / UNKNOWN |
| Pi 5 CSI connector | CAM0 / CAM1 / UNKNOWN |
| Pixhawk arrow/body-X direction | UNKNOWN |
| Camera optical direction | UNKNOWN |
| M8N placement/arrow | UNKNOWN |

For UART, verify TX ↔ RX, RX ↔ TX, and GND ↔ GND. Do not change wires while powered. USB needs no TX/RX crossover. Record photos under the run’s `mount_evidence/` directory.

## Boot/console evidence

Retain `/boot/firmware/config.txt`, `/boot/firmware/cmdline.txt`, `/dev/serial*` symlinks, user groups, and permissions in the baseline. If a Linux serial console occupies the UART, report the needed manual change; Stage 6 does not edit it.

## Read-only orientation checks

With the airframe stationary and propellers removed, record 30–60 seconds level for roll/pitch/yaw mean, standard deviation, and P95 variation. Then align the body nose approximately north, east, south, and west and record heading. Document the reference instrument and its quality; an uncalibrated phone compass is low-quality evidence. Do not run compass or accelerometer calibration from Stage 6.
