#!/usr/bin/env bash
source .venv/bin/activate
export PYTHONPATH=.
python3 -u scripts/run_stage13sim_phase4.py --phase4-payload-slots 3 > output_phase4.log 2>&1
