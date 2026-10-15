import time, threading
from pymavlink import mavutil
def connect_and_wait(id):
  m = mavutil.mavlink_connection('tcp:127.0.0.1:5760')
  print(f'Client {id} connected')
  while True:
    msg = m.recv_match(blocking=True, timeout=1)
    if msg:
      print(f'Client {id} received', msg.get_type())
      break
t1 = threading.Thread(target=connect_and_wait, args=(1,))
t2 = threading.Thread(target=connect_and_wait, args=(2,))
t1.start(); time.sleep(1); t2.start()
t1.join(); t2.join()
