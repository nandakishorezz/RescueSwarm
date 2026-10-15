# OV9281 and Raspberry Pi 5 setup

The supported stack is OV9281 → libcamera → Picamera2 → `Picamera2CameraSource`. The legacy Picamera/`raspistill`/`raspivid` stack is not used. Raspberry Pi's current camera documentation lists `dtoverlay=ov9281`, requires `camera_auto_detect=0` for a manually selected overlay, and documents `,cam0` for connector 0 on boards with two CSI connectors. Without `,cam0`, the overlay checks connector 1 by default. Review the [official Raspberry Pi camera software documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html) before applying any change.

## Bring-up order

With the aircraft unpowered from motors and no flight required:

```bash
cd /opt/rescueswarm
source .venv/bin/activate
python tools/system_info.py
python tools/camera_probe.py
python tools/prepare_ov9281_config.py --connector cam0
python tools/list_camera_modes.py --output camera_modes.json
```

The preparation helper is a dry run unless `--apply` is explicitly present. Confirm whether the cable is on CAM0 or CAM1. It reports active auto-detection, all camera overlays, duplicates, conflicts, and legacy settings. It refuses ambiguous changes. With `--apply`, it creates a timestamped sibling backup, displays/preserves unrelated configuration, does not modify modules, and does not reboot.

Example only after confirming CAM0 and reviewing the diff:

```bash
sudo /opt/rescueswarm/.venv/bin/python tools/prepare_ov9281_config.py --connector cam0 --apply
```

Review `/boot/firmware/config.txt` and its backup, then reboot manually. Do not repeatedly add overlays.

After reboot:

```bash
rpicam-hello --list-cameras
python tools/camera_probe.py
python tools/list_camera_modes.py --output camera_modes.json
RESCUESWARM_RUN_CAMERA=1 python -m pytest -m "pi and camera" -s
python tools/benchmark_vision.py --mode capture_only --source picamera2 --profile detection --duration 60 --save-samples
```

Only the live `sensor_modes` output should determine the benchmark profile. For endurance, repeat capture-only and end-to-end runs with `--duration 1800`.

After manual benchmarks pass, prepare—but do not enable blindly—the service:

```bash
sudo cp deployment/pi5/rescue-vision.service /etc/systemd/system/
sudo mkdir -p /etc/rescueswarm
sudo cp deployment/pi5/vision.env.example /etc/rescueswarm/vision.env
sudo systemctl daemon-reload
sudo systemctl enable --now rescue-vision.service
systemctl status rescue-vision.service
```

The unit waits 10 seconds between failures and has a start-rate limit. Keep it disabled until camera enumeration, the selected profile, and the local model hash all pass manually.

## Picamera2 packaging

Raspberry Pi recommends the OS package because Picamera2 and libcamera bindings must stay compatible. Install it without GUI dependencies when needed:

```bash
sudo apt install -y python3-picamera2 --no-install-recommends
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

The `--system-site-packages` flag makes the OS-managed Picamera2/libcamera binding visible inside the project environment. Do not replace a working OS camera stack with an arbitrary pip Picamera2 build. See the [official Picamera2 installation guidance](https://www.raspberrypi.com/documentation/computers/camera_software.html#use-libcamera-from-python-with-picamera2) and [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).
