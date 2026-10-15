# Mission review and deterministic replay

Review reconstructs a run from persisted telemetry, normalized mission events, target observations, payload events, and report artifacts. No synthetic random generator executes during playback.

The player uses recorded timestamps, provides play/pause, seek, previous/next event, and 0.25x, 0.5x, 1x, 2x, and 4x rates. Position is linearly interpolated for display and orientation uses quaternion SLERP. A gap beyond the freshness threshold yields `STALE`; the UI does not extrapolate indefinitely.

Replay synchronizes aircraft pose, altitude, mode, camera pose/frustum/footprint, targets, uncertainty, payload state, and mission events. Clicking an event seeks to its timestamp. Local report links use a backend allowlist, so normal UI does not expose arbitrary filesystem paths.

Review evidence keeps the execution-source badge. SITL/synthetic replay can never be mistaken for physical flight evidence.
