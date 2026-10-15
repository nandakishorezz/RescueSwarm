"""Replaceable local detector backends with normalized original-pixel output."""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .health import ComponentHealth
from .manifest import ModelEntry
from .models import Detection, DetectionResult, FramePacket
from .preprocess import PreparedInput, RawDetection, VisionPreprocessor, non_maximum_suppression


class DetectorError(RuntimeError):
    pass


class DetectorBackend(ABC):
    def __init__(
        self, model: ModelEntry, preprocessor: VisionPreprocessor,
        confidence_threshold: float, person_class_ids: set[int], nms_iou_threshold: float,
    ):
        self.model = model
        self.preprocessor = preprocessor
        self.confidence_threshold = confidence_threshold
        self.person_class_ids = person_class_ids
        self.nms_iou_threshold = nms_iou_threshold
        self.health = ComponentHealth()
        self.model_load_ms: float | None = None
        self.inferences = 0

    def load(self) -> None:
        started = time.perf_counter_ns()
        self._load()
        self.model_load_ms = (time.perf_counter_ns() - started) / 1e6
        self.health.success()

    def warmup(self, frame: FramePacket, count: int) -> None:
        for _ in range(count):
            self.detect(frame)

    def detect(self, frame: FramePacket) -> DetectionResult:
        preprocess_started = time.perf_counter_ns()
        prepared_inputs = self.preprocessor.prepare(frame)
        preprocess_finished = time.perf_counter_ns()
        inference_started = time.monotonic_ns()
        perf_inference_started = time.perf_counter_ns()
        raw_by_input: list[tuple[PreparedInput, list[RawDetection]]] = []
        try:
            for prepared in prepared_inputs:
                raw_by_input.append((prepared, self._infer(prepared)))
        except Exception as exc:
            self.health.error(f"{type(exc).__name__}: {exc}")
            raise DetectorError(f"{self.model.backend} inference failed: {exc}") from exc
        inference_finished = time.monotonic_ns()
        perf_inference_finished = time.perf_counter_ns()
        post_started = time.perf_counter_ns()
        restored: list[Detection] = []
        for prepared, raw_detections in raw_by_input:
            for raw in raw_detections:
                if raw.class_id not in self.person_class_ids or raw.confidence < self.confidence_threshold:
                    continue
                class_name = self.model.classes[raw.class_id] if raw.class_id < len(self.model.classes) else "person"
                restored.append(self.preprocessor.restore(raw, prepared, frame, class_name))
        detections = non_maximum_suppression(restored, self.nms_iou_threshold)
        self._persist_diagnostic_raw_outputs(frame, raw_by_input, detections)
        post_finished = time.perf_counter_ns()
        self.inferences += 1
        self.health.success(inference_finished)
        return DetectionResult(
            frame_id=frame.frame_id,
            source_id=frame.source_id,
            capture_timestamp_monotonic_ns=frame.capture_timestamp_monotonic_ns,
            capture_timestamp_wall_ns=frame.capture_timestamp_wall_ns,
            inference_started_ns=inference_started,
            inference_finished_ns=inference_finished,
            model_id=self.model.id,
            backend=self.model.backend,
            original_channel_format=frame.pixel_format,
            model_input_format=prepared_inputs[0].model_input_format,
            detections=detections,
            preprocess_ms=(preprocess_finished - preprocess_started) / 1e6,
            inference_ms=(perf_inference_finished - perf_inference_started) / 1e6,
            postprocess_ms=(post_finished - post_started) / 1e6,
            end_to_end_ms=(inference_finished - frame.capture_timestamp_monotonic_ns) / 1e6,
            capture_timestamp_sim_ns=frame.capture_timestamp_sim_ns,
        )

    def _persist_diagnostic_raw_outputs(
        self,
        frame: FramePacket,
        raw_by_input: list[tuple[PreparedInput, list[RawDetection]]],
        detections: list[Detection],
    ) -> None:
        """Persist pre-threshold detector outputs only for an explicitly bounded audit run."""
        configured = os.environ.get("RESCUESWARM_EXACT_INFERENCE_AUDIT_DIR")
        if not configured:
            return
        audit_dir = Path(configured)
        raw_dir = audit_dir / "raw_outputs"
        raw_dir.mkdir(parents=True, exist_ok=True)
        safe_id = frame.frame_id.replace(":", "_").replace("/", "_")
        arrays: dict[str, np.ndarray] = {}
        transforms: list[dict[str, float | int]] = []
        raw_count = 0
        person_count = 0
        person_max = 0.0
        for index, (prepared, rows) in enumerate(raw_by_input):
            values = np.asarray([
                [row.class_id, row.confidence, row.x1, row.y1, row.x2, row.y2]
                for row in rows
            ], dtype=np.float32).reshape((-1, 6))
            arrays[f"input_{index}"] = values
            raw_count += len(rows)
            person_scores = values[values[:, 0] == 0, 1] if len(values) else np.empty(0, dtype=np.float32)
            person_count += int(person_scores.size)
            if person_scores.size:
                person_max = max(person_max, float(person_scores.max()))
            transform = prepared.transform
            transforms.append({
                "original_width": transform.original_width,
                "original_height": transform.original_height,
                "input_width": transform.input_width,
                "input_height": transform.input_height,
                "scale": transform.scale,
                "pad_x": transform.pad_x,
                "pad_y": transform.pad_y,
                "tile_x": transform.tile_x,
                "tile_y": transform.tile_y,
            })
        np.savez_compressed(raw_dir / f"{safe_id}.npz", **arrays)
        row = {
            "frame_id": frame.frame_id,
            "source_id": frame.source_id,
            "capture_timestamp_sim_ns": frame.capture_timestamp_sim_ns,
            "raw_output_file": str((raw_dir / f"{safe_id}.npz").name),
            "transforms": transforms,
            "raw_output_count": raw_count,
            "raw_person_output_count": person_count,
            "raw_person_max_confidence": person_max,
            "confidence_threshold": self.confidence_threshold,
            "passed_current_threshold": any(item.class_id in self.person_class_ids for item in detections),
            "filtered_detections": [item.as_dict() for item in detections],
        }
        audit_dir.mkdir(parents=True, exist_ok=True)
        with (audit_dir / "raw_output_index.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")

    def close(self) -> None:
        self._close()

    @abstractmethod
    def _load(self) -> None: ...

    @abstractmethod
    def _infer(self, prepared: PreparedInput) -> list[RawDetection]: ...

    def _close(self) -> None:
        return None


class MockDetectorBackend(DetectorBackend):
    """Deterministic contract test backend; it is not a trained person model."""

    def __init__(self, *args: Any, output_factory: Callable[[PreparedInput], list[RawDetection]] | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.output_factory = output_factory
        self.loaded = False

    def _load(self) -> None:
        self.loaded = True

    def _infer(self, prepared: PreparedInput) -> list[RawDetection]:
        if not self.loaded:
            raise DetectorError("Mock detector is not loaded")
        if self.output_factory:
            return self.output_factory(prepared)
        width, height = prepared.transform.input_width, prepared.transform.input_height
        return [RawDetection(0, 0.8, width * 0.4, height * 0.25, width * 0.6, height * 0.75)]


class ONNXDetectorBackend(DetectorBackend):
    def __init__(self, artifact: Path, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.artifact = artifact
        self.session: Any = None
        self.input_name: str | None = None
        self.output_name: str | None = None

    def _load(self) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise DetectorError("ONNX Runtime is not installed; provision the optional vision-onnx extra") from exc
        self.session = ort.InferenceSession(str(self.artifact), providers=["CPUExecutionProvider"])
        self.input_name = self.model.input_name or self.session.get_inputs()[0].name
        self.output_name = self.model.output_name or self.session.get_outputs()[0].name

    def _infer(self, prepared: PreparedInput) -> list[RawDetection]:
        if self.session is None or self.input_name is None:
            raise DetectorError("ONNX detector is not loaded")
        outputs = self.session.run([self.output_name] if self.output_name else None, {self.input_name: prepared.tensor})
        return decode_yolo_output(np.asarray(outputs[0]))

    def _close(self) -> None:
        self.session = None


class NCNNDetectorBackend(DetectorBackend):
    def __init__(self, artifact: Path, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.artifact = artifact
        self.net: Any = None
        self.input_name = self.model.input_name or "in0"
        self.output_name = self.model.output_name or "out0"

    def _load(self) -> None:
        try:
            import ncnn
        except ImportError as exc:
            raise DetectorError("NCNN Python bindings are not installed on this host") from exc
        param_files = sorted(self.artifact.glob("*.param")) if self.artifact.is_dir() else []
        bin_files = sorted(self.artifact.glob("*.bin")) if self.artifact.is_dir() else []
        if len(param_files) != 1 or len(bin_files) != 1:
            raise DetectorError("NCNN artifact directory must contain exactly one .param and one .bin file")
        self.net = ncnn.Net()
        if self.net.load_param(str(param_files[0])) != 0 or self.net.load_model(str(bin_files[0])) != 0:
            raise DetectorError("NCNN failed to load the local model files")

    def _infer(self, prepared: PreparedInput) -> list[RawDetection]:
        if self.net is None:
            raise DetectorError("NCNN detector is not loaded")
        import ncnn
        extractor = self.net.create_extractor()
        if extractor.input(self.input_name, ncnn.Mat(prepared.tensor[0])) != 0:
            raise DetectorError(f"NCNN rejected input tensor '{self.input_name}'")
        result, output = extractor.extract(self.output_name)
        if result != 0:
            raise DetectorError(f"NCNN failed to extract output '{self.output_name}'")
        return decode_yolo_output(np.asarray(output))

    def _close(self) -> None:
        self.net = None


def decode_yolo_output(output: np.ndarray) -> list[RawDetection]:
    values = np.asarray(output)
    while values.ndim > 2 and values.shape[0] == 1:
        values = values[0]
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.ndim != 2:
        raise DetectorError(f"Unsupported detector output shape: {output.shape}")
    if values.shape[1] == 6:
        return [RawDetection(int(row[5]), float(row[4]), *map(float, row[:4])) for row in values]
    if values.shape[0] < values.shape[1] and values.shape[0] <= 256:
        values = values.T
    if values.shape[1] < 5:
        raise DetectorError(f"Detector output has too few columns: {values.shape}")
    results: list[RawDetection] = []
    for row in values:
        scores = row[4:]
        class_id = int(np.argmax(scores))
        confidence = float(scores[class_id])
        center_x, center_y, width, height = map(float, row[:4])
        results.append(RawDetection(
            class_id, confidence,
            center_x - width / 2, center_y - height / 2,
            center_x + width / 2, center_y + height / 2,
        ))
    return results
