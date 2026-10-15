from __future__ import annotations

import time
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from rescue_core.vision.camera import CameraSource, ImageDirectoryCameraSource, SyntheticCameraSource
from rescue_core.vision.agent import VisionAgent
from rescue_core.vision.detectors import MockDetectorBackend, decode_yolo_output
from rescue_core.vision.manifest import ModelEntry
from rescue_core.vision.models import Detection, FramePacket
from rescue_core.vision.preprocess import (
    RawDetection, VisionPreprocessor, non_maximum_suppression, tile_regions, to_rgb,
)
from rescue_core.vision.queue import FrameProducer, LatestFrameQueue


def packet(image: np.ndarray, pixel_format: str = "Y8") -> FramePacket:
    return FramePacket(
        "frame", "source", time.monotonic_ns(), time.time_ns(), 0,
        image.shape[1], image.shape[0], pixel_format, image,
    )


def model() -> ModelEntry:
    return ModelEntry(
        id="mock", path=None, architecture="test", input_size=(640, 640), classes=["person", "car"],
        backend="mock", sha256=None, source="test", license="test", commercial_notes="test",
        exported_at=None, available=True,
    )


def test_queue_drops_oldest_and_never_exceeds_bound() -> None:
    queue = LatestFrameQueue(2)
    for sequence in range(20):
        item = packet(np.zeros((2, 2), dtype=np.uint8))
        item.sequence = sequence
        item.frame_id = str(sequence)
        queue.put(item)
        assert len(queue) <= 2
    latest = queue.get_latest()
    assert latest and latest.sequence == 19
    assert queue.dropped_total == 19
    assert queue.max_observed_depth == 2


def test_monochrome_is_replicated_not_invented_as_color() -> None:
    gray = np.array([[0, 64], [128, 255]], dtype=np.uint8)
    rgb = to_rgb(gray, "Y8")
    assert rgb.shape == (2, 2, 3)
    assert np.array_equal(rgb[:, :, 0], gray)
    assert np.array_equal(rgb[:, :, 0], rgb[:, :, 2])


def test_letterbox_restores_bbox_to_original_1280x800_pixels() -> None:
    frame = packet(np.zeros((800, 1280), dtype=np.uint8))
    preprocessor = VisionPreprocessor(640, 640)
    prepared = preprocessor.prepare(frame)[0]
    # Original [200,100,600,700] -> scale .5 and 120 vertical letterbox padding.
    restored = preprocessor.restore(RawDetection(0, .9, 100, 170, 300, 470), prepared, frame, "person")
    assert (restored.x1, restored.y1, restored.x2, restored.y2) == (200, 100, 600, 700)


def test_tiled_restoration_adds_original_tile_offset() -> None:
    frame = packet(np.zeros((600, 1000), dtype=np.uint8))
    preprocessor = VisionPreprocessor(
        640, 640, strategy="tiled", tile_width=640, tile_height=600, tile_overlap=.25
    )
    prepared = preprocessor.prepare(frame)
    assert [item.transform.tile_x for item in prepared] == [0, 360]
    second = prepared[1]
    # Tile is 640x600 letterboxed with 20 vertical pixels; local [10,20,110,220].
    restored = preprocessor.restore(RawDetection(0, .8, 10, 40, 110, 240), second, frame, "person")
    assert (restored.x1, restored.y1, restored.x2, restored.y2) == (370, 20, 470, 220)


def test_tile_regions_cover_final_edges_deterministically() -> None:
    assert tile_regions(1000, 600, 640, 600, .25) == [(0, 0, 640, 600), (360, 0, 640, 600)]


def test_nms_merges_tiled_duplicates_and_preserves_separate_people() -> None:
    boxes = [
        Detection(0, "person", .9, 10, 10, 50, 90, 200, 100),
        Detection(0, "person", .8, 12, 10, 51, 90, 200, 100),
        Detection(0, "person", .7, 120, 10, 160, 90, 200, 100),
    ]
    kept = non_maximum_suppression(boxes, .5)
    assert [item.confidence for item in kept] == [.9, .7]


def test_detector_filters_nonhuman_and_low_confidence_and_propagates_timestamp() -> None:
    source = packet(np.zeros((480, 640), dtype=np.uint8))
    outputs = lambda _: [RawDetection(0, .8, 100, 100, 200, 300), RawDetection(1, .9, 1, 1, 10, 10), RawDetection(0, .1, 1, 1, 10, 10)]
    detector = MockDetectorBackend(
        model=model(), preprocessor=VisionPreprocessor(640, 640), confidence_threshold=.25,
        person_class_ids={0}, nms_iou_threshold=.45, output_factory=outputs,
    )
    detector.load()
    result = detector.detect(source)
    assert len(result.detections) == 1
    assert result.detections[0].class_name == "person"
    assert result.capture_timestamp_monotonic_ns == source.capture_timestamp_monotonic_ns
    assert result.inference_started_ns >= source.capture_timestamp_monotonic_ns


def test_image_directory_replay_order_is_deterministic(tmp_path: Path) -> None:
    Image.fromarray(np.full((4, 4), 2, dtype=np.uint8)).save(tmp_path / "b.png")
    Image.fromarray(np.full((4, 4), 1, dtype=np.uint8)).save(tmp_path / "A.png")
    orders = []
    for _ in range(2):
        source = ImageDirectoryCameraSource(tmp_path)
        source.start()
        orders.append([source.read().camera_metadata.extra["replay_path"], source.read().camera_metadata.extra["replay_path"]])
        assert source.read() is None
        source.stop()
    assert orders == [["A.png", "b.png"], ["A.png", "b.png"]]


def test_synthetic_source_produces_monotonic_sequences() -> None:
    source = SyntheticCameraSource(width=8, height=6, fps=1000)
    source.start()
    first, second = source.read(), source.read()
    source.stop()
    assert first.sequence == 0 and second.sequence == 1
    assert second.capture_timestamp_monotonic_ns > first.capture_timestamp_monotonic_ns


def test_yolo_decoder_supports_n_by_six_output() -> None:
    decoded = decode_yolo_output(np.array([[1, 2, 10, 20, .75, 0]], dtype=np.float32))
    assert len(decoded) == 1
    assert decoded[0].class_id == 0
    assert abs(decoded[0].confidence - .75) < 1e-6


def test_independent_vision_agent_emits_normalized_local_events() -> None:
    detector = MockDetectorBackend(
        model=model(), preprocessor=VisionPreprocessor(64, 64), confidence_threshold=.25,
        person_class_ids={0}, nms_iou_threshold=.45,
    )
    events = []
    agent = VisionAgent(
        SyntheticCameraSource(width=64, height=48, fps=100), detector,
        queue_size=1, no_frame_timeout_s=.05, event_callback=events.append,
    )
    thread = threading.Thread(target=agent.run)
    thread.start()
    time.sleep(.08)
    agent.stop()
    thread.join(2)
    assert not thread.is_alive()
    assert {event.type for event in events} >= {"vision.health", "vision.detection", "vision.frame_stats"}
    assert all("latitude" not in str(event.as_dict()).casefold() for event in events)
    assert agent.status()["frames_processed"] > 0


def test_camera_recovery_attempts_are_bounded_and_backed_off() -> None:
    class RecoveringSource(CameraSource):
        def __init__(self):
            super().__init__("recovering")
            self.starts = 0
            self.delivered = False

        def start(self):
            self.starts += 1
            if self.starts < 3:
                raise RuntimeError("temporary camera error")

        def read(self):
            if self.delivered:
                return None
            self.delivered = True
            return self._packet(np.zeros((2, 2), dtype=np.uint8), "Y8")

        def stop(self):
            return None

    source = RecoveringSource()
    queue = LatestFrameQueue(1)
    producer = FrameProducer(source, queue, restart_attempts=2, restart_backoff_s=.001)
    producer.start()
    producer.join(1)
    assert not producer.running
    assert source.starts == 3
    assert producer.capture_errors == 2
    assert queue.get_latest().frame_id.startswith("recovering:")


def test_existing_positional_agent_configuration_remains_correct(tmp_path):
    agent = VisionAgent(
        SyntheticCameraSource(width=2, height=2), None,
        2, 2, None, None, tmp_path / "status.json", 3, 2,
    )
    assert agent.status_path == tmp_path / "status.json"
    assert agent.producer.restart_attempts == 3
    assert callable(agent.result_callback)


def test_producer_stop_interrupts_blocked_camera_read():
    entered, released = threading.Event(), threading.Event()

    class BlockingSource(CameraSource):
        def start(self):
            pass

        def read(self):
            entered.set()
            released.wait(3)
            return None

        def stop(self):
            released.set()

    producer = FrameProducer(BlockingSource("blocked"), LatestFrameQueue(2))
    producer.start()
    assert entered.wait(1)
    producer.stop(.5)
    assert not producer.running


def test_status_reader_lock_does_not_kill_inference(monkeypatch, tmp_path):
    agent = VisionAgent(SyntheticCameraSource(width=2, height=2), None, status_path=tmp_path / "status.json")
    def locked_replace(*args):
        raise PermissionError("Windows reader holds destination")
    monkeypatch.setattr(Path, "replace", locked_replace)
    agent._write_status(None)
    assert agent.status()["status_write_failures"] == 1
