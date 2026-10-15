import socket, time, threading
from pymavlink import mavutil
def start_server():
  time.sleep(2)
  s = socket.socket()
  s.bind(('127.0.0.1', 5760))
  s.listen(1)
  c, _ = s.accept()
  print('Server accepted')
  time.sleep(1)
  c.close()
th=threading.Thread(target=start_server)
th.start()
print('Connecting')
m=mavutil.mavlink_connection('tcp:127.0.0.1:5760')
m.wait_heartbeat(timeout=5)
th.join()
