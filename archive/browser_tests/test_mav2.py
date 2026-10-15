from pymavlink import mavutil
m1=mavutil.mavlink_connection('tcp:127.0.0.1:5760')
print('Client 1 heartbeat:', m1.wait_heartbeat(timeout=3))
m2=mavutil.mavlink_connection('tcp:127.0.0.1:5760')
print('Client 2 heartbeat:', m2.wait_heartbeat(timeout=3))
print('Client 1 still alive:', m1.recv_match(type='HEARTBEAT', blocking=True, timeout=2))
