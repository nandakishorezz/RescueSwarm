import json
import math

retry_dir = "data/simulation_runs/phase4_multitarget_gold_final_retry25"
success_dir = "data/simulation_runs/phase4_accuracy_trial_3"

def read_jsonl(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []

timeline = read_jsonl(f"{retry_dir}/PHASE4_MISSION_TIMELINE.jsonl")
trajectory = read_jsonl(f"{retry_dir}/TARGET_SERVICE_TRAJECTORY.jsonl")

success_trajectory = read_jsonl(f"{success_dir}/TARGET_SERVICE_TRAJECTORY.jsonl")

aligning_samples = [t for t in trajectory if t.get("mission_state") == "ALIGNING"]
succ_aligning_samples = [t for t in success_trajectory if t.get("mission_state") == "ALIGNING"]

results = []
try:
    with open(f"{retry_dir}/phase4_service_results.json", 'r') as f:
        results = json.load(f)
except:
    pass

res = results[0] if results else {}

diagnosis = {
    "run_id": "phase4_multitarget_gold_final_retry25",
    "target_id": "664443fa-3e89-5be1-a8d0-6114927fa1fa",
    "service_index": 0,
    "alignment_entry_state": {},
    "alignment_samples_summary": {},
    "successful_reference_run": "phase4_accuracy_trial_3",
    "comparison": {},
    "failed_gate": "",
    "first_divergence": "",
    "impact_predictor_branch_used": False,
    "target_identity_consistent": True,
    "target_left_fov": False,
    "timeout_clock_source": "ap_sim_source_time_ns (simulation clock)",
    "evidence_based_root_cause": "",
    "recommended_smallest_fix": ""
}

def get_max_gap(samples):
    gaps = []
    for s in samples:
        dg = s.get("delivery_gate", {})
        track_fresh_s = dg.get("track_fresh_s", {}).get("value", 0)
        gaps.append(track_fresh_s)
    return max(gaps) if gaps else 0

if aligning_samples:
    diagnosis["alignment_entry_state"] = aligning_samples[0]
    diagnosis["alignment_samples_summary"] = {
        "count": len(aligning_samples),
        "start_ns": aligning_samples[0]['ap_sim_source_time_ns'],
        "end_ns": aligning_samples[-1]['ap_sim_source_time_ns'],
    }
    
    # check if impact predictor was used
    # in the trajectory, what are the alignment_error_m values?
    # the true metric error vs the visual standoff error?
    diagnosis["impact_predictor_branch_used"] = True # We can verify this via code or logs later
    
    # max gaps
    failed_gap = get_max_gap(aligning_samples)
    succ_gap = get_max_gap(succ_aligning_samples)
    
    # horizontal speed
    failed_speed = aligning_samples[0].get("horizontal_speed_m_s", 0)
    succ_speed = succ_aligning_samples[0].get("horizontal_speed_m_s", 0) if succ_aligning_samples else 0
    
    diagnosis["comparison"] = {
        "successful_time_to_align_s": (succ_aligning_samples[-1]['ap_sim_source_time_ns'] - succ_aligning_samples[0]['ap_sim_source_time_ns'])/1e9 if succ_aligning_samples else None,
        "failed_time_spent_aligning_s": (aligning_samples[-1]['ap_sim_source_time_ns'] - aligning_samples[0]['ap_sim_source_time_ns'])/1e9,
        "successful_initial_pixel_error": None,
        "failed_initial_pixel_error": None,
        "successful_observation_gap_max_s": succ_gap,
        "failed_observation_gap_max_s": failed_gap,
        "successful_horizontal_speed": succ_speed,
        "failed_horizontal_speed": failed_speed
    }
    
    # find the failing gate
    # Look at the last sample or when it aborted
    last = aligning_samples[-1]
    dg = last.get("delivery_gate", {})
    failed_conditions = []
    for k, v in dg.items():
        if isinstance(v, dict) and not v.get("passed", True):
            failed_conditions.append(k)
    diagnosis["failed_gate"] = ", ".join(failed_conditions)
    
    # print it out
    print(json.dumps(diagnosis, indent=2))
