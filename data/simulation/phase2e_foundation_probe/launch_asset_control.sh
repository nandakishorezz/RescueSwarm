#!/usr/bin/env bash
set -euo pipefail
export GZ_SIM_RESOURCE_PATH=/mnt/c/Users/nanda/Downloads/SURVIVOR_DETECTION/simulation/assets
echo $$ > /mnt/c/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation/phase2e_foundation_probe/gazebo.pid
exec gz sim -s -r --headless-rendering /mnt/c/Users/nanda/Downloads/SURVIVOR_DETECTION/simulation/worlds/perception_asset_control.sdf
