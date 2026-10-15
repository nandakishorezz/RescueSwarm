"""Traceable local model manifests with strict artifact hashing and no downloads."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ModelArtifactError(RuntimeError):
    pass


class ModelEntry(BaseModel):
    id: str
    path: str | None
    architecture: str
    input_size: tuple[int, int]
    classes: list[str]
    backend: str
    sha256: str | None
    source: str
    license: str
    commercial_notes: str
    exported_at: str | None
    available: bool
    role: str = "BASELINE_GENERIC_PERSON_DETECTOR"
    input_name: str | None = None
    output_name: str | None = None


class ModelManifest(BaseModel):
    schema_version: int = Field(ge=1)
    models: list[ModelEntry]

    def require(self, model_id: str) -> ModelEntry:
        matches = [model for model in self.models if model.id == model_id]
        if len(matches) != 1:
            raise ModelArtifactError(f"Model ID is missing or duplicated: {model_id}")
        return matches[0]


def load_model_manifest(path: str | Path) -> ModelManifest:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ModelManifest.model_validate(raw)


def validate_model_artifact(entry: ModelEntry, manifest_path: str | Path) -> Path | None:
    if entry.backend == "mock":
        return None
    if not entry.available:
        raise ModelArtifactError(
            f"Model '{entry.id}' is not provisioned. Export/copy it during development; runtime downloads are forbidden."
        )
    if not entry.path:
        raise ModelArtifactError(f"Model '{entry.id}' has no local path")
    artifact = (Path(manifest_path).resolve().parent / entry.path).resolve()
    if not artifact.exists():
        raise ModelArtifactError(f"Local model artifact is missing: {artifact}")
    if not entry.sha256:
        raise ModelArtifactError(f"Model '{entry.id}' has no recorded SHA-256")
    actual = artifact_sha256(artifact)
    if actual.casefold() != entry.sha256.casefold():
        raise ModelArtifactError(f"Model hash mismatch for '{entry.id}': expected {entry.sha256}, got {actual}")
    return artifact


def artifact_sha256(path: str | Path) -> str:
    artifact = Path(path)
    digest = hashlib.sha256()
    paths = [artifact] if artifact.is_file() else sorted(item for item in artifact.rglob("*") if item.is_file())
    if not paths:
        raise ModelArtifactError(f"Model artifact contains no files: {artifact}")
    for item in paths:
        if artifact.is_dir():
            digest.update(item.relative_to(artifact).as_posix().encode("utf-8"))
            digest.update(b"\0")
        with item.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()
