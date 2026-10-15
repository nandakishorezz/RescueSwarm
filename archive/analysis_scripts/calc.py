import json
import math
import os

runs_data = {
  "phase4_microtest_fresh_3": {
    "truth_x": 17.499999999151644,
    "truth_y": -2.9999999988941037,
    "est_x": 17.56277735184793,
    "est_y": -3.695086014307946,
    "release_x": 17.909836101405052,
    "release_y": -5.663683682536688,
    "release_alt": 1.766,
    "vel_x": 0.1068282425403595,
    "vel_y": 0.10579542070627213,
    "vel_z": 0.05089303106069565,
    "first_contact": [18.462206977247437, -5.594812303615612, 0.07354441801257758],
    "settled": [18.46195551915368, -5.5937923767291595, 0.07499996969399642],
    "payload_release": [17.944058153107054, -5.664946922725945, 1.8552630950454658],
    "release_speed": 0.18384776310850234
  },
  "phase4_microtest_fresh_4": {
    "truth_x": 17.499999999151644,
    "truth_y": -2.9999999988941037,
    "est_x": 17.56154376112686,
    "est_y": -3.737602792166091,
    "release_x": 18.037321947548243,
    "release_y": -5.76329957855418,
    "release_alt": 1.852,
    "vel_x": -0.062017422169446945,
    "vel_y": 0.10029496252536774,
    "vel_z": 0.07164197415113449,
    "first_contact": [18.534647004686196, -5.691124072835621, 0.07680969712455357],
    "settled": [18.539103190765168, -5.685014790767829, 0.07499989944958478],
    "payload_release": [18.06906154064331, -5.7668217304467175, 1.9287323720611895],
    "release_speed": 0.12
  },
  "phase4_microtest_fresh_5": {
    "truth_x": 17.499999999151644,
    "truth_y": -2.9999999988941037,
    "est_x": 17.55735063249843,
    "est_y": -3.7457647935450975,
    "release_x": 18.026698116134135,
    "release_y": -5.741162699257216,
    "release_alt": 1.84,
    "vel_x": -0.011479179374873638,
    "vel_y": 0.11915192753076553,
    "vel_z": 0.08003325015306473,
    "first_contact": [18.53674864561427, -5.670185264153018, 0.0741524116539023],
    "settled": [18.53680578087942, -5.67047262404846, 0.07499997360631991],
    "payload_release": [18.05707465878796, -5.744923940288675, 1.8914496788631576],
    "release_speed": 0.13341664064126335
  }
}

def mag(v):
    return math.sqrt(v[0]**2 + v[1]**2)

results = {}

sum_geo = 0
sum_rel = 0
sum_drift = 0
sum_final = 0
final_errors = []

for run_id, d in runs_data.items():
    E_geo = [d["est_x"] - d["truth_x"], d["est_y"] - d["truth_y"]]
    E_release = [d["release_x"] - d["truth_x"], d["release_y"] - d["truth_y"]]
    
    # Payload drift
    E_fall = [d["first_contact"][0] - d["payload_release"][0], d["first_contact"][1] - d["payload_release"][1]]
    
    # Final impact error (using settled as previous evaluator might have used it, we will calc both)
    first_contact_error_vec = [d["first_contact"][0] - d["truth_x"], d["first_contact"][1] - d["truth_y"]]
    settled_error_vec = [d["settled"][0] - d["truth_x"], d["settled"][1] - d["truth_y"]]
    
    # We will use settled for final error to match the 2.77, 2.88, 2.86 given in prompt
    # Wait, prompt says: 
    # run 3: 2.77
    # run 4: 2.88
    # run 5: 2.86
    
    settled_error_m = mag(settled_error_vec)
    first_contact_error_m = mag(first_contact_error_vec)
    
    # Vehicle release -> estimated target offset
    est_rel_offset = mag([d["release_x"] - d["est_x"], d["release_y"] - d["est_y"]])
    
    results[run_id] = {
        "geo_err_v": E_geo,
        "geo_err_m": mag(E_geo),
        "rel_err_v": E_release,
        "rel_err_m": mag(E_release),
        "fall_drift_v": E_fall,
        "fall_drift_m": mag(E_fall),
        "first_contact_m": first_contact_error_m,
        "settled_m": settled_error_m,
        "final_err_v": settled_error_vec, # matching evaluator
        "final_err_m": settled_error_m,
        "release_to_est_target_m": est_rel_offset
    }
    
    sum_geo += mag(E_geo)
    sum_rel += mag(E_release)
    sum_drift += mag(E_fall)
    sum_final += settled_error_m
    final_errors.append(settled_error_m)

mean_final = sum_final / 3
stddev = math.sqrt(sum((x - mean_final)**2 for x in final_errors) / 3)

print(json.dumps(results, indent=2))
print("Aggregates:")
print("mean geo", sum_geo/3)
print("mean rel", sum_rel/3)
print("mean drift", sum_drift/3)
print("mean final", mean_final)
print("stddev final", stddev)

# Heading of mean final error
mean_x = sum([results[r]["final_err_v"][0] for r in runs_data]) / 3
mean_y = sum([results[r]["final_err_v"][1] for r in runs_data]) / 3
heading = math.degrees(math.atan2(mean_y, mean_x))
print("mean heading", heading)

