#!/usr/bin/env bash
set -euo pipefail

# WSL may inherit Windows Anaconda paths.  Those CMake package files select a
# Windows/newer protobuf ABI and make native Gazebo headers fail to compile.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

plugin_root="${ARDUPILOT_GAZEBO_HOME:-/home/nandakishore7/ardupilot_gazebo}"
build_dir="${plugin_root}/build"
cmake --fresh -S "${plugin_root}" -B "${build_dir}" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build "${build_dir}" --parallel "${STAGE13SIM_BUILD_JOBS:-2}"
echo "PLUGIN_COMMIT=$(git -C "${plugin_root}" rev-parse HEAD)"
find "${build_dir}" -maxdepth 2 -type f -name '*ArduPilotPlugin*' -print
