
import time
from gz.transport13 import Node
from gz.msgs10.image_pb2 import Image
import threading

got_image = threading.Event()
def cb(msg):
    print(f'W: {msg.width}, H: {msg.height}, format: {msg.pixel_format_type}, data_len: {len(msg.data)}', flush=True)
    got_image.set()

node = Node()
node.subscribe(Image, '/drone_01/ov9281_reference_simulation/image', cb)
print('waiting...', flush=True)
got_image.wait(5.0)

