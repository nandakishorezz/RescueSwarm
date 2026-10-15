# GCS Engineering mode

Engineering mode is a local diagnostic surface. It exposes SITL launch/failure tools already protected by the backend, health freshness, spatial transforms, raw events, replay diagnostics, camera geometry, and vehicle geometry. It does not add physical command authority.

The Spatial Inspector can modify session-only demonstration values for focal length and camera mount translation. Updates are labeled `CONFIGURED_ASSUMPTION`, remain in the browser session, and never write `CameraCalibration` or physical extrinsics records. Hardware evidence stays `PHYSICAL_UNVERIFIED` while H1 is pending.

The measurement tool reports horizontal distance, 3D distance, bearing, and altitude difference between two entered ENU points. The image-pixel tool reports the unprojected ray, world direction, ground hit, range, and incidence angle. These are engineering visualizations; the backend Stage 5 geolocator remains authoritative.

When `competition.enabled=true`, the page and engineering APIs are disabled. The mission operator cannot use it to tag a target, change waypoints/modes, assign a drone, drop payload, or steer an aircraft.
