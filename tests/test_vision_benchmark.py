from __future__ import annotations

import hashlib
import time
from pathlib import Path

import numpy as np
import pytest
import yaml

from rescue_core.vision.benchmark import BenchmarkRunner, VisionRunWriter, latency_summary, percentile
from rescue_core.vision.camera import SyntheticCameraSource
from rescue_core.vision.detectors import MockDetectorBackend
from rescue_core.vision.evaluation import average_precision, evaluate_boxes
from rescue_core.vision.evidence import EvidenceRecorder
from rescue_core.vision.manifest import (
    ModelArtifactError, ModelEntry, artifact_sha256, load_model_manifest, validate_model_artifact,
)
from rescue_core.vision.models import Detection, FramePacket
from rescue_core.vision.preprocess import VisionPreprocessor


def mock_model() -> ModelEntry:
    return ModelEntry(
        id="mock", path=None, architecture="test", input_size=(64, 64), classes=["person"],
        backend="mock", sha256=None, source="test", license="test", commercial_notes="test",
        exported_at=None, available=True,
    )


def mock_detector() -> MockDetectorBackend:
    return MockDetectorBackend(
        model=mock_model(), preprocessor=VisionPreprocessor(64, 64), confidence_threshold=.25,
        person_class_ids={0}, nms_iou_threshold=.45,
    )


def test_percentiles_include_interpolation_and_empty_state() -> None:
    assert percentile([], 95) is None
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([0, 10], 95) == 9.5
    assert latency_summary([1, 2, 10])["max_ms"] == 10


def test_model_manifest_and_hash_validation(tmp_path: Path) -> None:
    artifact = tmp_path / "model.onnx"
    artifact.write_bytes(b"local-model")
    digest = hashlib.sha256(b"local-model").hexdigest()
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump({"schema_version": 1, "models": [{
        "id": "local", "path": "model.onnx", "architecture": "test", "input_size": [64, 64],
        "classes": ["person"], "backend": "onnx", "sha256": digest, "source": "local",
        "license": "test", "commercial_notes": "test", "exported_at": None, "available": True,
    }]}), encoding="utf-8")
    entry = load_model_manifest(manifest_path).require("local")
    assert validate_model_artifact(entry, manifest_path) == artifact
    assert artifact_sha256(artifact) == digest
    entry.sha256 = "0" * 64
    with pytest.raises(ModelArtifactError, match="hash mismatch"):
        validate_model_artifact(entry, manifest_path)


def test_missing_model_fails_without_downloading(tmp_path: Path) -> None:
    entry = mock_model().model_copy(update={"id": "missing", "backend": "ncnn", "available": False, "path": "x"})
    with pytest.raises(ModelArtifactError, match="not provisioned"):
        validate_model_artifact(entry, tmp_path / "manifest.yaml")


def test_evidence_recorder_enforces_cooldown_and_maximum(tmp_path: Path) -> None:
    recorder = EvidenceRecorder(tmp_path, True, .5, cooldown_s=10, sample_interval_s=10, max_images=3)
    source = SyntheticCameraSource(width=20, height=20, fps=1000)
    source.start()
    frame = source.read()
    result = mock_detector()
    result.load()
    detection = result.detect(frame)
    assert recorder.record_detection(frame, detection)
    assert recorder.saved_images == 3
    assert not recorder.record_detection(frame, detection)
    assert len(list((tmp_path / "samples").glob("*.png"))) == 3


def test_evaluation_logic_threshold_sweep_without_claiming_model_accuracy() -> None:
    truth = Detection(0, "person", 1, 10, 10, 30, 40, 100, 100)
    correct = Detection(0, "person", .8, 10, 10, 30, 40, 100, 100)
    false = Detection(0, "person", .2, 60, 10, 80, 40, 100, 100)
    metrics = evaluate_boxes({"image": [correct, false]}, {"image": [truth]}, [.1, .5])
    assert metrics[0].precision == .5 and metrics[0].recall == 1
    assert metrics[1].precision == 1 and metrics[1].recall == 1
    assert average_precision(metrics) is not None
    assert evaluate_boxes({}, {}, [.5]) == []


def test_short_synthetic_benchmark_writes_complete_artifacts(tmp_path: Path) -> None:
    writer = VisionRunWriter(tmp_path, "test")
    summary = BenchmarkRunner(
        SyntheticCameraSource(width=64, height=48, fps=100), mock_detector(), writer,
        queue_size=1, warmup_frames=1, resource_interval_s=.01,
    ).run(.08, "end_to_end")
    assert summary.status == "PASS"
    assert summary.frames_captured > 0 and summary.frames_inferred > 0
    assert summary.frames_dropped <= summary.frames_captured
    for name in (
        "run_manifest.json", "system.json", "camera.json", "model.json", "frame_stats.ndjson",
        "detections.ndjson", "benchmark_summary.json", "benchmark_report.md",
    ):
        assert (writer.run_directory / name).is_file()
