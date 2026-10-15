import socket, time
def test_endpoint():
  for _ in range(30):
    try:
      s = socket.create_connection(('127.0.0.1', 5760), timeout=1.0)
      try:
        data = s.recv(1)
        if not data: raise ConnectionAbortedError('EOF')
        print('Got data:', data)
        s.close()
        return True
      except TimeoutError:
        print('Timeout, which means connection stayed open! Ready.')
        s.close()
        return True
    except Exception as e:
      print('Not ready:', e)
      time.sleep(1)
test_endpoint()
