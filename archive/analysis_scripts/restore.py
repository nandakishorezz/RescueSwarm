import sys
content = open('scripts/run_stage13sim_phase4.py').read()

target = """        def collect_segmentation() -> None:
            assert segmentation is not None
            try:
                segmentation.start()
                while not segmentation_stop.is_set():
            "frame_count": 0,
            "max_frame_gap_s": 0.0,
            "last_frame_host_time": None,
        }
        seg_metrics = {
            "process_start_host_time": None,
            "connected_host_time": None,
            "first_frame_host_time": None,
            "ready_host_time": None,
            "ready_latency_s": None,
            "frame_count": 0,
            "max_frame_gap_s": 0.0,
            "last_frame_host_time": None,
        }"""

replacement = """        def collect_segmentation() -> None:
            assert segmentation is not None
            try:
                segmentation.start()
                while not segmentation_stop.is_set():
                    frame = segmentation.read()
                    segmentation_frames.append(compact_visibility(
                        frame,
                        survivor_labels=frozenset(101 + index for index in range(len(manager.definition.survivors.survivors))),
                    ))
                    if len(segmentation_frames) > 5000:
                        del segmentation_frames[:1000]
            except Exception as exc:  # evaluator failure cannot alter autonomy
                if not segmentation_stop.is_set():
                    segmentation_errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                segmentation.stop()

        gazebo_source.start()
        while gazebo_source.metrics.frames_received == 0:
            time.sleep(0.1)

        adapter.prepare_for_arm(); adapter.arm(); adapter.start_auto_mission()
        # Perception is a search-phase service.  Starting it only once the
        # aircraft is airborne prevents launch-pad views from becoming mission
        # candidates while preserving the live camera-only causal path.
        perception_started = False
        final_sequence = mission.items[-1].seq
        confirmation_seen_at: float | None = None
        
        rgb_metrics = {
            "process_start_host_time": None,
            "connected_host_time": None,
            "first_frame_host_time": None,
            "ready_host_time": None,
            "ready_latency_s": None,
            "frame_count": 0,
            "max_frame_gap_s": 0.0,
            "last_frame_host_time": None,
        }
        seg_metrics = {
            "process_start_host_time": None,
            "connected_host_time": None,
            "first_frame_host_time": None,
            "ready_host_time": None,
            "ready_latency_s": None,
            "frame_count": 0,
            "max_frame_gap_s": 0.0,
            "last_frame_host_time": None,
        }"""

content = content.replace(target, replacement)
with open('scripts/run_stage13sim_phase4.py', 'w') as f:
    f.write(content)
print("File restored")
