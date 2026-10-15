#!/usr/bin/env bash
set -u

echo "== OS =="
cat /etc/os-release 2>/dev/null || true
echo "== KERNEL =="
uname -a
echo "== TOOLS =="
for tool in gz gazebo glxinfo gst-launch-1.0 cmake python3; do
  printf '%s=' "$tool"
  command -v "$tool" || echo NOT_FOUND
done
gz sim --versions 2>/dev/null || true
gazebo --version 2>/dev/null || true
glxinfo -B 2>/dev/null || true
gst-launch-1.0 --version 2>/dev/null || true
cmake --version 2>/dev/null | head -1 || true
python3 --version 2>/dev/null || true

echo "== ARDUPILOT =="
find /home -maxdepth 3 -type d -name ardupilot 2>/dev/null | while IFS= read -r tree; do
  echo "TREE=$tree"
  git -C "$tree" rev-parse HEAD 2>/dev/null || true
  git -C "$tree" status --short 2>/dev/null | head -20 || true
  "$tree/build/sitl/bin/arducopter" --version 2>/dev/null | head -5 || true
done

echo "== ARDUPILOT_GAZEBO =="
find /home -maxdepth 3 -type d -name ardupilot_gazebo 2>/dev/null | while IFS= read -r tree; do
  echo "TREE=$tree"
  git -C "$tree" rev-parse HEAD 2>/dev/null || true
  git -C "$tree" status --short 2>/dev/null | head -20 || true
done
