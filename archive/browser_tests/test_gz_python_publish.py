import sys
import time
import inspect
from gz.transport13 import Node
from gz.msgs10.empty_pb2 import Empty

def main():
    print("Python version:", sys.version)
    import gz.transport13
    print("gz.transport file:", gz.transport13.__file__)
    
    node = Node()
    print("Node attributes:", dir(node))
    
    topic = "/rescueswarm/payload/slot_1/detach"
    print(f"Advertising {topic}...")
    pub = node.advertise(topic, Empty)
    print("Publisher attributes:", dir(pub))
    
    print("Waiting for subscribers...")
    subscribers = 0
    for _ in range(50):
        # We need a way to check subscribers. Does Node have topic_info or something?
        # Since I don't know the exact API, I will just list attributes of node
        time.sleep(0.1)
        
    print("Publishing Empty...")
    try:
        ret = pub.publish(Empty())
        print("Publish returned:", ret)
    except Exception as e:
        print("Publish exception:", repr(e))

if __name__ == '__main__':
    main()
