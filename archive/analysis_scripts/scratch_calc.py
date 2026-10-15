import math

# Drone position
current_east = 17.2830
current_north = -4.6785
altitude = 12.0 # assuming
terrain_z = 0.0

# Target position (assuming from geofence)
target_east = 17.26
target_north = -0.42

# Vector from target to drone (initial drone position)
# Assuming drone was at (17.26, -4.84) initially
initial_east = 17.26
initial_north = -4.84
vec_e = initial_east - target_east
vec_n = initial_north - target_north
dist = math.hypot(vec_e, vec_n)
vec_e /= dist
vec_n /= dist

print(f"vec_e: {vec_e}, vec_n: {vec_n}")

# Safe offset (simulating _compute_safe_delivery_offset)
r = 2.6
best_score = float('inf')
best_dx, best_dy = 0, 0
for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
    rad = math.radians(angle)
    dx = r * math.sin(rad)
    dy = r * math.cos(rad)
    
    # assuming max_slope = 0
    dot = (dx/r) * vec_e + (dy/r) * vec_n
    score = 0 + (1.0 - dot) * 2.0
    if score < best_score:
        best_score = score
        best_dx = dx
        best_dy = dy

print(f"safe_offset: {best_dx}, {best_dy}")

# Alignment error
target_altitude = 8.0
height = altitude - terrain_z
scale = height / target_altitude

desired_east = target_east + best_dx * scale
desired_north = target_north + best_dy * scale

print(f"desired_east: {desired_east}, desired_north: {desired_north}")
print(f"current_east: {current_east}, current_north: {current_north}")

error_e = desired_east - current_east
error_n = desired_north - current_north
error = math.hypot(error_e, error_n)

print(f"alignment_error_m: {error}")
