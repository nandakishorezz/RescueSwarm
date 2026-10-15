# DISK_RECOVERY_MANIFEST.md

Date: 2026-09-07
Stage: 13SIM Phase 2C -- Disk Recovery

## Disk State at Start

| Volume | Total | Used | Free |
|---|---|---|---|
| C: (Windows) | 476 GB | 476 GB | 0 GB |
| /dev/sdd (WSL internal) | 1007 GB | 8.9 GB | 947 GB |

C: drive is 100% full. WSL internal disk has 947 GB free and is NOT on C:.

## Root Cause of Previous WinError 112

The full pytest regression was running a Phase-1 fixture that attempted to copy
a Gazebo mesh file into a temp directory on the C: drive. With C: already near
full from accumulated runs and other user data, the copy exhausted remaining bytes.

## Per-Run Classification

| Run Directory | Size | Decision | Reason |
|---|---|---|---|
| stage13sim_phase1_gold_final_20260906x | 1.5 MB | PRESERVED | Authoritative Phase-1 gold in report |
| stage13sim_phase1_calm_20260906u | 1.1 MB | PRESERVED | In Phase-1 artifacts list |
| stage13sim_phase1_speed2_20260906v | 1.0 MB | PRESERVED | In Phase-1 artifacts list |
| stage13sim_phase1_speed5_20260906w | 868 KB | PRESERVED | In Phase-1 artifacts list |
| stage13sim_phase1_calm_20260905a | 158 MB | PRESERVED | Earliest calm baseline |
| stage13sim_phase1_calm_20260905b | 118 MB | PRESERVED | Second independent calm run |
| stage13sim_phase1_calm_20260905c | 131 MB | REMOVED | Duplicate; same config as a/b; not in final report |
| stage13sim_phase1_calm_20260905d | 131 MB | REMOVED | Duplicate; same config as a/b; not in final report |
| stage13sim_phase1_calm_20260905e | 133 MB | REMOVED | Duplicate; same config as a/b; not in final report |
| stage13sim_phase1_calm_20260905f | 131 MB | REMOVED | Duplicate; same config as a/b; not in final report |
| stage13sim_phase1_gold_20260905h | 300 MB | REMOVED | Superseded by gold_final_20260906x; 299 MB is large JSONL not needed |
| stage13sim_phase2* | 6-13 MB each | PRESERVED | Active Phase-2 evidence |
| stage13sim_phase2b_clock* | 12-13 MB each | PRESERVED | Clock evidence for Phase-2C |

## Items Removed

| Path | Size Freed | Reason |
|---|---|---|
| data/simulation_runs/stage13sim_phase1_calm_20260905c/ | 131 MB | Duplicate intermediate calm run |
| data/simulation_runs/stage13sim_phase1_calm_20260905d/ | 131 MB | Duplicate intermediate calm run |
| data/simulation_runs/stage13sim_phase1_calm_20260905e/ | 133 MB | Duplicate intermediate calm run |
| data/simulation_runs/stage13sim_phase1_calm_20260905f/ | 131 MB | Duplicate intermediate calm run |
| data/simulation_runs/stage13sim_phase1_gold_20260905h/ | 300 MB | Superseded intermediate gold |

Total freed from project: ~826 MB

## WSL Internal Items (NOT on C: drive -- retained)

| Path | Size | Status |
|---|---|---|
| ~/.cache/pip (WSL) | 911 MB | RETAINED -- WSL /dev/sdd (947 GB free); not C: |
| ~/ardupilot/logs (WSL) | 277 MB | RETAINED -- WSL /dev/sdd; useful SITL diagnostics |
| ~/ardupilot/build (WSL) | 112 MB | RETAINED -- needed for SITL |
| ~/ardupilot_gazebo/build (WSL) | 72 MB | RETAINED -- needed for Gazebo plugin |

Note: WSL VHD (11 GB) and Docker VHD (13 GB) are on C: AppData but are
outside this project scope. Additional disk space beyond ~826 MB must come
from Windows Disk Cleanup or moving user files off C:.

## Permanent Disk Safety Fix

Large Gazebo mesh assets must not be copied into data/ for every test run.
Phase-2C test fixture audit will prevent future WinError 112 exhaustion.

## Session Follow-Up Recovery (2026-09-07, same day)

C: had again reached 0.30 GB free (recovery required again before Phase-2C
fixed set examinations could produce their evidence). The following was freed:

| Source | Size Freed | Mechanism |
|---|---|---|
| npm cache (Windows) | ~4.5 GB | `npm cache clean --force` |
| pip cache (Windows) | ~13.4 GB | `pip cache purge` |
| %LOCALAPPDATA%\Temp | large | temporary installer/session files |

Post-cleanup C: free: **~18.33 GB**.

Note: WSL caches (`~/.cache/pip` 911 MB) retained on /dev/sdd as before; they
are not on C: and are not the problem. Windows pip/npm caches were the
accumulating C: consumers this session.

## Live Phase-2C disk policy

- New live runs write to `data/simulation_runs/`; keep per-run size near a few
  MB by persisting only the required evidence artifacts, not full image streams.
- Monitor `%LOCALAPPDATA%\Temp` and Windows pip/npm caches before each live run
  batch; treat <2 GB free on C: as a hard stop for further Gazebo mesh copies.
