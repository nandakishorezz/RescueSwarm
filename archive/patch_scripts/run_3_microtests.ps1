python scripts/run_stage13sim_phase4.py --run-id phase4_payload_microtest_7 --phase4-service --single-survivor --experiment-stop-at-rtl --sensor-profile REFERENCE_NOMINAL
python scripts/run_stage13sim_phase4.py --run-id phase4_payload_microtest_8 --phase4-service --single-survivor --experiment-stop-at-rtl --sensor-profile REFERENCE_NOMINAL
python scripts/run_stage13sim_phase4.py --run-id phase4_payload_microtest_9 --phase4-service --single-survivor --experiment-stop-at-rtl --sensor-profile REFERENCE_NOMINAL
python verify_microtests.py
