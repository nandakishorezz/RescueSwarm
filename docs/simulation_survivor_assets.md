# Simulation Survivor Assets

Existing local assets were preserved; no downloads or scale changes were made.

| Asset | Pose | Source / License | SHA256 |
| --- | --- | --- | --- |
| `simulation/assets/gazebo_actor/meshes/stand.dae` | standing | Gazebo Fuel Mingfei actor v1 / CC-BY-4.0 | `ba1e23e637389ab10dfb029ebf9e5bef9771562a15cf0ff444b88e27043989be` |
| `simulation/assets/gazebo_actor/meshes/sitting.dae` | sitting | same / CC-BY-4.0 | `f0c3a122133152e62c5eadff0fb19b0cb87d865178cf199d5a2c6bf7dbe26afe` |
| `simulation/assets/survivors/CesiumMan.glb` | reference mesh | Khronos sample assets, Cesium / CC-BY-4.0 | manifest: `b7001eaeea8254bd44773bcd247e78696d94169388fbb2a1800fc69434e777d9` |

Standing and sitting hashes were independently checked during recovery. The
Cesium hash is quoted from its preserved manifest, not newly verified.
License notices and manifests are locally packaged with generated runs.

The final recovery scenario uses two standing actors and one sitting actor,
mesh scale `1 1 1`, declared metric units. Numeric deformed-mesh adult-height
validation has not been completed. No 2-3x enlargement, fluorescent material,
poster, or billboard was added. Cesium's manifest gives native height 1.50655 m;
that is not validation of the separate actor assets used in the flight.

Lying rotation support exists in the world builder but has not been qualified
for physical ground contact or detection. The UI still prepares procedural
orange survivors by default; that setup must not be presented as a realistic
gold scenario. The CLI run explicitly selects licensed actor variants.

Live eye-level engineering control: 20/20 frames with a person detection,
semantic label 101, 3,331 visible pixels in the final sampled mask. This proves
the local mesh renders and the baseline detector can respond, not aerial
accuracy. No inference threshold or preprocessing was tuned to obtain this.
