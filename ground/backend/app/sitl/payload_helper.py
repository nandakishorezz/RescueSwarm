import sys
import time
from gz.transport13 import Node
from gz.msgs10.empty_pb2 import Empty
from gz.msgs10.stringmsg_pb2 import StringMsg
import threading

def main():
    node = Node()
    pubs = {}
    state_events = {}
    
    def make_callback(t):
        def cb(msg):
            if msg.data == "detached":
                if t in state_events:
                    state_events[t].set()
        return cb
    
    print("READY", flush=True)
    
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
            
        if line.startswith("PREWARM "):
            topic = line.split(" ")[1]
            if topic not in pubs:
                pubs[topic] = node.advertise(topic, Empty)
                state_topic = topic.replace("detach", "state")
                state_events[topic] = threading.Event()
                node.subscribe(StringMsg, state_topic, make_callback(topic))
            
            # Bounded discovery waiting
            discovered = False
            for _ in range(50):
                if pubs[topic].has_connections():
                    discovered = True
                    break
                time.sleep(0.1)
                
            if not discovered:
                print(f"PREWARMED_NO_CONN {topic}", flush=True)
            else:
                print(f"PREWARMED {topic}", flush=True)
            continue
            
        topic = line
        if topic not in pubs:
            pubs[topic] = node.advertise(topic, Empty)
            state_topic = topic.replace("detach", "state")
            state_events[topic] = threading.Event()
            node.subscribe(StringMsg, state_topic, make_callback(topic))
            
            discovered = False
            for _ in range(50):
                if pubs[topic].has_connections():
                    discovered = True
                    break
                time.sleep(0.1)
                
            if not discovered:
                print(f"ERROR {topic} DETACH_SUBSCRIBER_NOT_DISCOVERED", flush=True)
                continue
            
        state_events[topic].clear()
        
        try:
            success = pubs[topic].publish(Empty())
            if not success:
                print(f"ERROR {topic} PUBLISH_FAILED", flush=True)
                continue
        except Exception as e:
            print(f"ERROR {topic} {e!r}", flush=True)
            continue
            
        print(f"PUBLISHED {topic}", flush=True)
        
        if state_events[topic].wait(timeout=2.0):
            print(f"SUCCESS {topic}", flush=True)
        else:
            print(f"FAILED {topic} ACK_TIMEOUT", flush=True)

if __name__ == "__main__":
    main()
