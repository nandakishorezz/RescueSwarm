# Pi 4 Local Network Architecture

## Selected deployment option

Stage 2 chooses native systemd services plus native PostgreSQL/PostGIS on the Raspberry Pi 4. This avoids a container daemon and container image management on a 4 GB host, lowers idle overhead, and makes offline boot dependencies explicit. Containers remain a possible later packaging choice, but are not required.

```text
Pi 4 / 10.42.0.1
├── NetworkManager access point: RescueSwarm-Local
├── PostgreSQL/PostGIS on loopback
├── FastAPI + built React assets on TCP 80
├── operator laptop/tablet
└── future drone Pi 5 client
```

`rescue.local` through mDNS is a convenience. The static fallback `http://10.42.0.1` is the required access path and does not require DNS.

## Prepared files

- `deployment/pi4/prepare_access_point.sh`: dry-run by default; explicit Pi-only network mutation with `--apply`.
- `deployment/pi4/rescue-swarm-gcs.service`: systemd ordering, restart policy, restricted service, and local TCP 80 endpoint.
- `deployment/pi4/ground.env.example`: local database environment template without a real password.
- `ground/backend/migrations/001_stage2_postgresql.sql`: initial PostgreSQL/PostGIS schema.

## Deployment outline

1. While still in a controlled setup environment, install PostgreSQL, PostGIS, Python 3.11+, wheels (including the `postgres` extra via `python -m pip install -e '.[postgres]'`), and the prebuilt frontend. Do not rely on downloads after deployment.
2. Create an unprivileged `rescueswarm` account and copy the repository to `/opt/rescueswarm`.
3. Create `/etc/rescueswarm/ground.env` from the example with a strong local database password; permissions should restrict it to the service administrator.
4. Create the PostgreSQL role/database and apply `001_stage2_postgresql.sql` locally.
5. Copy the systemd unit, reload systemd, enable it, and verify database/API/static asset startup.
6. Run the AP script without arguments to review its action. Export `RESCUESWARM_AP_PASSWORD` locally and use `--apply` only after approving the network change.
7. Disconnect WAN, disable upstream Wi-Fi, reboot, and verify `http://10.42.0.1`, WebSocket telemetry simulation, and the offline report from an operator device.

The service file includes `CAP_NET_BIND_SERVICE` only so the unprivileged service can bind TCP 80. It has no network-administration capability. The AP change remains a separate administrator action.

## Configuration

SSID, address, subnet, and mDNS name are non-secret values in `config/ground.yaml`. The WPA password exists only in a local environment variable during provisioning. Do not commit it. If the Wi-Fi interface or subnet changes, validate collision-free addressing before applying the AP profile.

This deployment is prepared but has not been physically tested on the Pi 4.
