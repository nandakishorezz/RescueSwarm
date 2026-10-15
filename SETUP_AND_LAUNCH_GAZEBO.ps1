[CmdletBinding()]
param()
$WSL_USER = "nandakishore7"
$AP_HOME = "/home/$WSL_USER/ardupilot"
$APGZ_BUILD = "/home/$WSL_USER/ardupilot_gazebo/build"
$APGZ_MODELS = "/home/$WSL_USER/ardupilot_gazebo/models"
$APGZ_WORLDS = "/home/$WSL_USER/ardupilot_gazebo/worlds"

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  RescueSwarm -- Gazebo Simulation Setup and Launch" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# STEP 1: WSL check
Write-Host "[1/6] Checking WSL availability..."
$wslOut = wsl.exe -e bash -c "echo WSL_OK" 2>&1
if ($wslOut -notlike "*WSL_OK*") {
    Write-Host "  ERROR: WSL not available. Install Ubuntu from Microsoft Store." -ForegroundColor Red; exit 1
}
Write-Host "  OK -- WSL is running." -ForegroundColor Green

# STEP 2: Gazebo check
Write-Host "[2/6] Checking Gazebo Harmonic in WSL..."
$gzVer = wsl.exe -e bash -c "gz sim --versions 2>/dev/null || echo NOT_INSTALLED"
if ($gzVer -like "*NOT_INSTALLED*") {
    Write-Host "  ERROR: Gazebo Harmonic not found. Run:" -ForegroundColor Red
    Write-Host "  wsl.exe -u root -e bash scripts/install_stage13sim_wsl.sh" -ForegroundColor Yellow; exit 1
}
Write-Host "  OK -- Gazebo $($gzVer.Trim())" -ForegroundColor Green

# STEP 3: ArduCopter SITL binary
Write-Host "[3/6] Checking ArduCopter SITL binary..."
$apBin = wsl.exe -e bash -c "test -f $AP_HOME/build/sitl/bin/arducopter && echo FOUND || echo NOT_FOUND"
if ($apBin -like "*NOT_FOUND*") {
    Write-Host "  ERROR: arducopter not built. In WSL run:" -ForegroundColor Red
    Write-Host "  cd ~/ardupilot && ./waf configure --board sitl && ./waf copter" -ForegroundColor Yellow; exit 1
}
Write-Host "  OK -- arducopter SITL binary found." -ForegroundColor Green

# STEP 4: ardupilot_gazebo plugin
Write-Host "[4/6] Checking ardupilot_gazebo plugin..."
$pluginOk = wsl.exe -e bash -c "test -f $APGZ_BUILD/libArduPilotPlugin.so && echo FOUND || echo NOT_FOUND"
if ($pluginOk -like "*NOT_FOUND*") {
    Write-Host "  ERROR: ardupilot_gazebo plugin not built. Run:" -ForegroundColor Red
    Write-Host "  wsl.exe -u root -e bash scripts/install_stage13sim_wsl.sh" -ForegroundColor Yellow; exit 1
}
Write-Host "  OK -- libArduPilotPlugin.so found." -ForegroundColor Green

# STEP 5: Write WSL env file
Write-Host "[5/6] Configuring WSL environment variables..."
$envLines = "#!/usr/bin/env bash`n"
$envLines += "# RescueSwarm Gazebo env -- auto-generated`n"
$envLines += "export ARDUPILOT_HOME=`"$AP_HOME`"`n"
$envLines += "export GZ_SIM_SYSTEM_PLUGIN_PATH=`"$APGZ_BUILD`"`n"
$envLines += "export GZ_SIM_RESOURCE_PATH=`"$APGZ_MODELS`:$APGZ_WORLDS`"`n"
$envLines += "export PATH=`"`$PATH:$AP_HOME/build/sitl/bin`"`n"
$tmpEnv = "$env:TEMP\rescueswarm_gz_env.sh"
[System.IO.File]::WriteAllText($tmpEnv, $envLines)
wsl.exe -u $WSL_USER -e bash -c "cp /mnt/c/Users/nanda/AppData/Local/Temp/rescueswarm_gz_env.sh ~/rescueswarm_gz_env.sh && chmod +x ~/rescueswarm_gz_env.sh && grep -q rescueswarm_gz_env ~/.bashrc || echo 'source ~/rescueswarm_gz_env.sh' >> ~/.bashrc"
Write-Host "  OK -- GZ_SIM_SYSTEM_PLUGIN_PATH, GZ_SIM_RESOURCE_PATH, ARDUPILOT_HOME set in WSL." -ForegroundColor Green

# STEP 6: Write launch script to WSL and open terminal
Write-Host "[6/6] Writing Gazebo+SITL launch script and opening terminal..."
$launch = "#!/usr/bin/env bash`nset -u`nsource ~/rescueswarm_gz_env.sh`n"
$launch += "WORLD=`"$APGZ_WORLDS/iris_runway.sdf`"`n"
$launch += "echo ''; echo '=================================================='; echo '  RescueSwarm Stage 13SIM -- Gazebo + SITL'; echo '=================================================='; echo ''"
$launch += "`necho `"  Plugin path:   `$GZ_SIM_SYSTEM_PLUGIN_PATH`"`n"
$launch += "echo `"  Resource path: `$GZ_SIM_RESOURCE_PATH`"`n"
$launch += "echo `"  ArduPilot:     `$ARDUPILOT_HOME`"`n"
$launch += "echo ''"
$launch += "`necho '[SITL] Starting ArduCopter SITL on UDP 14550...'"
$launch += "`nmkdir -p ~/sitl_run && cd ~/sitl_run"
$launch += "`n$AP_HOME/build/sitl/bin/arducopter --model gazebo-iris --speedup 1 --defaults $AP_HOME/Tools/autotest/default_params/gazebo-iris.parm -I0 --home -35.363261,149.165230,584,353 > ~/sitl_run/sitl.log 2>&1 &"
$launch += "`nSITL_PID=`$!"
$launch += "`necho `"  SITL PID=`$SITL_PID  |  log: ~/sitl_run/sitl.log`""
$launch += "`nsleep 3"
$launch += "`necho '[GZ] Starting Gazebo Harmonic (iris_runway)...'"
$launch += "`necho '     Wait 15-30 seconds for GUI to appear.'"
$launch += "`ngz sim --verbose 1 `"`$WORLD`" &"
$launch += "`nGZ_PID=`$!"
$launch += "`necho ''"
$launch += "`necho '=================================================='"
$launch += "`necho '  CHECKLIST -- verify these once Gazebo opens:'"
$launch += "`necho '=================================================='"
$launch += "`necho '  1. Gazebo GUI shows Iris drone on runway'"
$launch += "`necho '  2. Open a new WSL tab and run:'"
$launch += "`necho '       source ~/rescueswarm_gz_env.sh'"
$launch += "`necho '       gz topic -l'"
$launch += "`necho '     Should list /world/... and /model/iris/... topics'"
$launch += "`necho '  3. Check SITL log:'"
$launch += "`necho '       tail -f ~/sitl_run/sitl.log'"
$launch += "`necho '     Look for: EKF2 IMU0 is using GPS, ready to arm'"
$launch += "`necho '  4. MAVLink heartbeat test (in WSL):'"
$launch += "`necho '       python3 -c ""from pymavlink import mavutil; m=mavutil.mavlink_connection(chr(39)+'udpin:0.0.0.0:14551'+chr(39)); m.wait_heartbeat(); print(chr(39)+'HEARTBEAT OK'+chr(39), m.flightmode)""'"
$launch += "`necho '=================================================='"
$launch += "`necho '  Ctrl+C to stop.'; echo ''"
$launch += "`ntrap 'kill `$GZ_PID `$SITL_PID 2>/dev/null; exit 0' INT TERM"
$launch += "`nwait `$GZ_PID"
$tmpLaunch = "$env:TEMP\rescueswarm_launch_gz.sh"
[System.IO.File]::WriteAllText($tmpLaunch, $launch)
wsl.exe -u $WSL_USER -e bash -c "cp /mnt/c/Users/nanda/AppData/Local/Temp/rescueswarm_launch_gz.sh ~/rescueswarm_launch_gz.sh && chmod +x ~/rescueswarm_launch_gz.sh"
Write-Host "  OK -- Launch script ready at ~/rescueswarm_launch_gz.sh" -ForegroundColor Green

# Open Windows Terminal; fallback to cmd
try { Start-Process wt.exe -ArgumentList "wsl.exe -u $WSL_USER -- bash ~/rescueswarm_launch_gz.sh" } catch { Start-Process cmd.exe -ArgumentList "/c start wsl.exe -u $WSL_USER -- bash ~/rescueswarm_launch_gz.sh" }

Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host "  ALL SET! Simulation is starting in the new window." -ForegroundColor Green
Write-Host "  Wait 15-30 seconds for the Gazebo GUI to appear." -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host ""
