from pymavlink import mavutil
m1=mavutil.mavlink_connection('tcp:127.0.0.1:5760')
m1.wait_heartbeat(timeout=3)
print('Client 1:', getattr(m1, 'target_system', 'None'))
m2=mavutil.mavlink_connection('tcp:127.0.0.1:5760')
m2.wait_heartbeat(timeout=3)
print('Client 2:', getattr(m2, 'target_system', 'None'))
