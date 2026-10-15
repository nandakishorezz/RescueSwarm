"""
Deterministic controller microtest - no Gazebo, pure math.
Simulates the controller for representative scenarios.
"""
import math
import sys

KP = 0.45
MAX_SPEED = 1.5
KD = 0.85
DEADBAND = 0.10
DAMPING_RANGE = 2.0

def command(east, north, ve, vn):
    error = math.hypot(east, north)
    kd_eff = KD * max(0.0, 1.0 - error / DAMPING_RANGE)
    if error <= DEADBAND:
        cmd_e = -ve * kd_eff
        cmd_n = -vn * kd_eff
    else:
        scale = min(KP, MAX_SPEED / error)
        cmd_e = east * scale - ve * kd_eff
        cmd_n = north * scale - vn * kd_eff
    spd = math.hypot(cmd_e, cmd_n)
    if spd > MAX_SPEED:
        cmd_e = (cmd_e / spd) * MAX_SPEED
        cmd_n = (cmd_n / spd) * MAX_SPEED
    return cmd_e, cmd_n

def simulate(label, east0, north0, ve0, vn0, dt=0.2, steps=100):
    e, n, ve, vn = east0, north0, ve0, vn0
    print(f"\n--- {label} ---")
    print(f"  Initial: err={math.hypot(e,n):.3f} spd={math.hypot(ve,vn):.3f}")
    for i in range(steps):
        ce, cn = command(e, n, ve, vn)
        ve = ce; vn = cn  # vehicle immediately follows (simplified)
        e -= ve * dt; n -= vn * dt
        err = math.hypot(e, n)
        spd = math.hypot(ve, vn)
        if math.isnan(err) or math.isinf(err):
            print(f"  FAIL at step {i}: NaN/Inf")
            return False
        if i % 10 == 0:
            print(f"  t={i*dt:.1f}s: err={err:.3f} spd={spd:.3f} cmd={math.hypot(ce,cn):.3f}")
    print(f"  Final:   err={err:.3f} spd={spd:.3f}")
    return err, spd

passed = True
results = []

# Scenario 1: Far target, stationary
r = simulate("FAR err=3.0 vel=0", 3.0, 0.0, 0.0, 0.0)
if r:
    err, spd = r
    assert err < 0.5, f"FAR: should converge, err={err:.3f}"
    print("  FAR: PASS - converged")
    results.append(True)
else:
    passed = False; results.append(False)

# Scenario 2: 1m error, moving toward target
r = simulate("MID err=1.0 vel=0.5 toward", 1.0, 0.0, 0.5, 0.0, steps=50)
if r:
    err, spd = r
    assert err < 0.5, f"MID: should converge, err={err:.3f}"
    print("  MID: PASS - converged")
    results.append(True)
else:
    passed = False; results.append(False)

# Scenario 3: Near target, high speed (retry16-style)
r = simulate("NEAR err=0.4 vel=2.0 toward", 0.4, 0.0, 2.0, 0.0, steps=50)
if r:
    err, spd = r
    # Should brake, not fly through wildly
    print(f"  NEAR+highspd: err={err:.3f} spd={spd:.3f}")
    assert spd < 2.5, f"Should not accelerate: spd={spd:.3f}"
    print("  NEAR+highspd: PASS - does not explode")
    results.append(True)
else:
    passed = False; results.append(False)

# Scenario 4: Near target, low speed, settling
r = simulate("NEAR err=0.2 vel=0.3", 0.2, 0.0, 0.3, 0.0, steps=50)
if r:
    err, spd = r
    assert math.hypot(err, 0) < 1.0 and spd < 1.0, f"Settling: err={err:.3f} spd={spd:.3f}"
    print("  Settling: PASS")
    results.append(True)
else:
    passed = False; results.append(False)

print(f"\n{'='*40}")
print(f"MICROTEST: {'PASS' if all(results) else 'FAIL'}")
sys.exit(0 if all(results) else 1)
