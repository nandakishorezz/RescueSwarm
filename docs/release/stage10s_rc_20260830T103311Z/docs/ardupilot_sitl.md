# Official ArduPilot SITL

ArduPilot is an external development dependency and is never vendored into RescueSwarm. Linux or WSL2 is supported; Gazebo and other 3D simulators are unnecessary.

Follow the official [Linux SITL setup](https://ardupilot.org/dev/docs/setting-up-sitl-on-linux.html):

```bash
cd ~
git clone --recurse-submodules https://github.com/ArduPilot/ardupilot.git
cd ardupilot
Tools/environment_install/install-prereqs-ubuntu.sh -y
. ~/.profile
./waf configure --board sitl
./waf copter
```

Record `git -C "$HOME/ardupilot" rev-parse HEAD` and `git -C "$HOME/ardupilot" branch --show-current`. Set `ARDUPILOT_HOME=/path/to/ardupilot`, or pass `--ardupilot-home` to the demo. On Windows this is the WSL Linux path.

The launcher follows the official [Using SITL](https://ardupilot.org/dev/docs/using-sitl-for-ardupilot-testing.html) flow:

```bash
Tools/autotest/sim_vehicle.py -v ArduCopter -f quad --no-mavproxy \
  --custom-location LAT,LON,ALT,HEADING
```

It does not use `--map`. Commands require `environment: sitl`, `command_authority: simulation_only`, and a numeric loopback endpoint. `/dev/*`, `COM*`, hostnames, wildcard listeners, LAN addresses, and public IPs fail before connection.

Run the included fixture end to end:

```bash
python scripts/run_stage3_demo.py data/fixtures/stage3_sitl_boundary.geojson \
  --execute --start-sitl --launch-altitude-m 584 \
  --ardupilot-home /home/operator/ardupilot
```

The launch altitude is site mean-sea-level altitude, not relative search altitude. Ordinary tests skip live SITL. Opt in with `RESCUESWARM_RUN_SITL=1 python -m pytest -m sitl -s` after setting `ARDUPILOT_HOME`.
