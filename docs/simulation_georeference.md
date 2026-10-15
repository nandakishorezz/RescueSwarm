# Simulation georeference

Stage 5 mission ENU remains authoritative. `SimulationGeoreference` defines the sole simulator transform:

`Mission ENU (east, north, up) = Gazebo world (x, y, z)`.

WGS84 conversion reuses the existing `LocalCoordinateFrame`. The run stores `T_gazebo_from_enu`, its inverse, and the WGS84 origin. Mission boundary markings, launch zone, vehicle spawn, and survivor models all use this transform.

Round-trip fixtures test `WGS84 → ENU → Gazebo → ENU → WGS84`; coordinate swaps and sign changes are not permitted outside the centralized transform.
