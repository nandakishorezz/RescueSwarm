#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run through WSL as root: wsl.exe -u root -e bash <path>/install_stage13sim_wsl.sh" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates wget gnupg lsb-release git build-essential cmake ninja-build pkg-config

keyring=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
repo=/etc/apt/sources.list.d/gazebo-stable.list
wget -q https://packages.osrfoundation.org/gazebo.gpg -O "$keyring"
echo "deb [arch=$(dpkg --print-architecture) signed-by=$keyring] https://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" > "$repo"
apt-get update
apt-get install -y \
  gz-harmonic libgz-sim8-dev rapidjson-dev \
  libopencv-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
  gstreamer1.0-plugins-bad gstreamer1.0-libav gstreamer1.0-gl \
  mesa-utils

install -d -o nandakishore7 -g nandakishore7 /home/nandakishore7/stage13sim
if [[ ! -d /home/nandakishore7/ardupilot_gazebo/.git ]]; then
  runuser -u nandakishore7 -- git clone https://github.com/ArduPilot/ardupilot_gazebo.git /home/nandakishore7/ardupilot_gazebo
fi

runuser -u nandakishore7 -- bash -lc '
  set -euo pipefail
  export GZ_VERSION=harmonic
  cmake -S /home/nandakishore7/ardupilot_gazebo -B /home/nandakishore7/ardupilot_gazebo/build -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
  cmake --build /home/nandakishore7/ardupilot_gazebo/build
'

echo "Stage 13SIM Gazebo dependencies and official ArduPilot plugin are installed."
