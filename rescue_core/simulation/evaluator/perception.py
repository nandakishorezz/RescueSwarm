"""Post-inference comparison against matched rendered segmentation pixels.

Nothing in this module creates a Detection, TargetObservation, or target.  Its
types carry the WorldTruth prefix to prevent accidental DTO reuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from rescue_core.simulation.perception.camera_source import GazeboCameraSource
from rescue_core.vision.models import Detection


@dataclass(slots=True, frozen=True)
class WorldTruthImageBBox:
    evaluator_label: int
    x1: int
    y1: int
    x2: int
    y2: int
    frame_width: int
    frame_height: int
    visible_pixels: int
    classification: str = "SIMULATION_GROUND_TRUTH"

    @property
    def area_px(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)

    @property
    def edge_clipped(self) -> bool:
        return self.x1 == 0 or self.y1 == 0 or self.x2 == self.frame_width or self.y2 == self.frame_height


@dataclass(slots=True, frozen=True)
class WorldTruthSegmentationFrame:
    frame_id: str
    sim_capture_time_ns: int
    width: int
    height: int
    label_image: np.ndarray
    checksum_sha256: str
    classification: str = "SIMULATION_ONLY_GROUND_TRUTH"


@dataclass(slots=True, frozen=True)
class WorldTruthVisibilityFrame:
    frame_id: str
    sim_capture_time_ns: int
    boxes: tuple[WorldTruthImageBBox, ...]
    classification: str = "SIMULATION_ONLY_GROUND_TRUTH"


@dataclass(slots=True, frozen=True)
class DetectionTruthMatch:
    detection_index: int
    truth_index: int
    iou: float
    center_error_px: float


@dataclass(slots=True)
class PerceptionFrameEvaluation:
    matches: list[DetectionTruthMatch] = field(default_factory=list)
    false_positive_indices: list[int] = field(default_factory=list)
    false_negative_indices: list[int] = field(default_factory=list)

    @property
    def true_positives(self) -> int:
        return len(self.matches)


class GazeboSegmentationSource:
    """Evaluator-only wrapper that never returns a Stage-4 FramePacket."""

    def __init__(self, topic: str, *, width: int = 1280, height: int = 800) -> None:
        self._source = GazeboCameraSource(
            topic.rstrip("/") + "/labels_map",
            source_id="evaluator_segmentation",
            camera_profile_id="EVALUATOR_ONLY_MATCHED_OPTICS",
            expected_width=width,
            expected_height=height,
            convert_to_mono=False,
        )

    @property
    def metrics(self):
        return self._source.metrics

    def start(self) -> None:
        self._source.start()

    def read(self) -> WorldTruthSegmentationFrame:
        frame = self._source.read()
        return WorldTruthSegmentationFrame(
            frame_id=frame.frame_id,
            sim_capture_time_ns=int(frame.camera_metadata.sensor_timestamp_ns or 0),
            width=frame.width,
            height=frame.height,
            label_image=_decode_label_image(frame.image, frame.pixel_format),
            checksum_sha256=str(frame.camera_metadata.extra["checksum_sha256"]),
        )

    def stop(self) -> None:
        self._source.stop()


def _decode_label_image(image: np.ndarray, pixel_format: str) -> np.ndarray:
    """Decode Gazebo semantic labels without lossy luminance conversion."""
    if image.ndim == 2:
        return image.astype(np.uint32, copy=True)
    if image.shape[2] < 3:
        raise ValueError(f"Segmentation label image has invalid shape {image.shape}")
    channels = image[..., :3].astype(np.uint32)
    if pixel_format == "BGR_INT8":
        channels = channels[..., ::-1]
    # Harmonic semantic labels_map replicates the 8-bit label into RGB.
    # Instance/color maps are different channels and must not be guessed here.
    if not (np.array_equal(channels[..., 0], channels[..., 1]) and np.array_equal(channels[..., 0], channels[..., 2])):
        raise ValueError("Expected replicated grayscale Gazebo semantic labels_map")
    return channels[..., 0].copy()


def semantic_mask_boxes(
    frame: WorldTruthSegmentationFrame, *, minimum_pixels: int = 1,
    survivor_labels: frozenset[int] | None = None,
) -> list[WorldTruthImageBBox]:
    values, counts = np.unique(frame.label_image, return_counts=True)
    if not len(values):
        return []
    boxes: list[WorldTruthImageBBox] = []
    for value, count in zip(values, counts, strict=True):
        label = int(value)
        # Gazebo's unlabeled background is zero, not the largest visible object.
        if label == 0 or int(count) < minimum_pixels or (survivor_labels is not None and label not in survivor_labels):
            continue
        ys, xs = np.nonzero(frame.label_image == value)
        boxes.append(WorldTruthImageBBox(
            evaluator_label=label,
            x1=int(xs.min()), y1=int(ys.min()), x2=int(xs.max()) + 1, y2=int(ys.max()) + 1,
            frame_width=frame.width, frame_height=frame.height, visible_pixels=int(count),
        ))
    return sorted(boxes, key=lambda item: item.evaluator_label)


def compact_visibility(
    frame: WorldTruthSegmentationFrame, *, minimum_pixels: int = 1,
    survivor_labels: frozenset[int] | None = None,
) -> WorldTruthVisibilityFrame:
    """Extract evaluator facts, then permit the megapixel raster to be freed."""
    return WorldTruthVisibilityFrame(
        frame_id=frame.frame_id,
        sim_capture_time_ns=frame.sim_capture_time_ns,
        boxes=tuple(semantic_mask_boxes(frame, minimum_pixels=minimum_pixels, survivor_labels=survivor_labels)),
    )


class PerceptionEvaluator:
    def __init__(self, iou_threshold: float = 0.5) -> None:
        if not 0 < iou_threshold <= 1:
            raise ValueError("IoU threshold must be in (0, 1]")
        self.iou_threshold = iou_threshold

    def evaluate(self, detections: list[Detection], truth: list[WorldTruthImageBBox]) -> PerceptionFrameEvaluation:
        candidates: list[tuple[float, int, int]] = []
        for detection_index, detection in enumerate(detections):
            if detection.class_id != 0 or detection.class_name != "person":
                continue
            for truth_index, box in enumerate(truth):
                candidates.append((_iou(detection, box), detection_index, truth_index))
        used_detections: set[int] = set()
        used_truth: set[int] = set()
        result = PerceptionFrameEvaluation()
        for iou, detection_index, truth_index in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
            if iou < self.iou_threshold or detection_index in used_detections or truth_index in used_truth:
                continue
            detection, box = detections[detection_index], truth[truth_index]
            result.matches.append(DetectionTruthMatch(
                detection_index=detection_index,
                truth_index=truth_index,
                iou=iou,
                center_error_px=float(np.hypot(
                    detection.center_x - (box.x1 + box.x2) / 2,
                    detection.center_y - (box.y1 + box.y2) / 2,
                )),
            ))
            used_detections.add(detection_index); used_truth.add(truth_index)
        result.false_positive_indices = sorted(set(range(len(detections))) - used_detections)
        result.false_negative_indices = sorted(set(range(len(truth))) - used_truth)
        return result


def _iou(detection: Detection, truth: WorldTruthImageBBox) -> float:
    x1, y1 = max(detection.x1, truth.x1), max(detection.y1, truth.y1)
    x2, y2 = min(detection.x2, truth.x2), min(detection.y2, truth.y2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = detection.area_px + truth.area_px - intersection
    return intersection / union if union > 0 else 0.0
