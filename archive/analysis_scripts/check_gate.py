import math

current_east = 17.909836101405052
current_north = -5.663683682536688
altitude = 1.766

ve = 0.1068282425403595
vn = 0.10579542070627213

target_east = 17.56277735184793
target_north = -3.695086014307946

height = max(0.0, altitude - 0.0)
t_fall = math.sqrt(2 * height / 9.81)

impact_east = current_east + ve * t_fall
impact_north = current_north + vn * t_fall

desired_east = target_east
desired_north = target_north

err_east = desired_east - impact_east
err_north = desired_north - impact_north

error_m = math.hypot(err_east, err_north)

print(f"t_fall: {t_fall}")
print(f"impact: {impact_east}, {impact_north}")
print(f"desired: {desired_east}, {desired_north}")
print(f"err_east: {err_east}, err_north: {err_north}")
print(f"error_m (use_impact_predictor=True): {error_m}")

# Now let's calculate the error with use_impact_predictor=False and standoff=2.0
bearing_east = target_east - current_east
bearing_north = target_north - current_north
distance = max(math.hypot(bearing_east, bearing_north), 1e-6)
current_standoff = 2.0 * (altitude / 2.0) # delivery_altitude_m is probably 2.0

des_east_so = target_east - bearing_east/distance*current_standoff
des_north_so = target_north - bearing_north/distance*current_standoff

err_east_so = des_east_so - current_east
err_north_so = des_north_so - current_north
error_so = math.hypot(err_east_so, err_north_so)
print(f"error_m (standoff=2.0): {error_so}")
