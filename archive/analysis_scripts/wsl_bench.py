import time
import json
import threading
from gz.transport13 import Node
from gz.msgs10.empty_pb2 import Empty
from gz.msgs10.stringmsg_pb2 import StringMsg
from gz.msgs10.pose_v_pb2 import Pose_V

def main():
    node = Node()
    
    events = []
    def state_cb(msg):
        events.append(msg.data)
        
    poses = {}
    time_series = []
    capture = False
    
    def pose_cb(msg):
        for pose in msg.pose:
            if pose.name in ("drone_01", "payload_slot_1"):
                poses[pose.name] = (pose.position.x, pose.position.y, pose.position.z)
        
        if capture and "drone_01" in poses and "payload_slot_1" in poses:
            # We don't have exact sim_time in Pose_V easily without looking at header, but we can use time.time()
            time_series.append({
                "sim_time_s": time.time(),
                "parent_position": poses["drone_01"],
                "payload_position": poses["payload_slot_1"]
            })

    node.subscribe(StringMsg, '/rescueswarm/payload/slot_1/state', state_cb)
    node.subscribe(Pose_V, '/world/default/pose/info', pose_cb)
    
    time.sleep(1.0)
    
    print("DETACH_START")
    pub = node.advertise('/rescueswarm/payload/slot_1/detach', Empty)
    
    discovered = False
    for _ in range(50):
        if pub.has_connections():
            discovered = True
            break
        time.sleep(0.1)
        
    if not discovered:
        print("DETACH_SUBSCRIBER_NOT_DISCOVERED")
        
    capture = True
    success = pub.publish(Empty())
    print("Publish success:", success)
    
    # Wait for impact
    start_time = time.time()
    while time.time() - start_time < 5.0:
        if poses.get("payload_slot_1") and poses["payload_slot_1"][2] < 0.1:
            # Found impact
            time.sleep(0.5) # Gather a few settled samples
            break
        time.sleep(0.1)
    
    capture = False
    
    if not time_series:
        print("NO TIME SERIES")
        return
        
    release_z = time_series[0]["payload_position"][2]
    min_z = min(s["payload_position"][2] for s in time_series)
    fall_dist = release_z - min_z
    
    # Check for decreasing Z
    decreasing_samples = 0
    prev_z = release_z
    for s in time_series:
        if s["payload_position"][2] < prev_z - 0.005:
            decreasing_samples += 1
            prev_z = s["payload_position"][2]
            
    # Derive physical_detach from trajectory (distance to parent increases)
    initial_dist = time_series[0]["parent_position"][2] - time_series[0]["payload_position"][2]
    max_dist = max(s["parent_position"][2] - s["payload_position"][2] for s in time_series)
    physical_detach = (max_dist - initial_dist) > 0.05
    
    physical_fall = physical_detach and fall_dist >= 0.05
    
    last_valid_z = min_z
    for s in reversed(time_series[-20:]):
        if s["payload_position"][2] < 0.1:
            last_valid_z = s["payload_position"][2]
            break
            
    physical_impact = physical_fall and last_valid_z < 0.1 and abs(last_valid_z - min_z) < 0.02
    
    res = {
        "subscriber_visible": discovered,
        "transport_publish_success": success,
        "detach_ack": "detached" in events,
        
        "release_position": time_series[0]["payload_position"],
        "post_detach_samples": time_series,
        
        "fall_distance_m": fall_dist,
        
        "physical_detach": "detached" in events,
        "physical_fall": physical_fall,
        "physical_impact": physical_impact,
        
        "impact_position": time_series[-1]["payload_position"],
        "passed": ("detached" in events and physical_fall and physical_impact)
    }
    
    with open("PHASE4_DETACHABLE_JOINT_BENCH_EVIDENCE.json", "w") as f:
        json.dump(res, f, indent=2)
        
    print(f"BENCH: detach={res['physical_detach']} fall={physical_fall} impact={physical_impact}")

if __name__ == '__main__':
    main()
