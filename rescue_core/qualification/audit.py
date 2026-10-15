"""Local-only release evidence, runtime socket, and secret audits."""

from __future__ import annotations

import hashlib
import json
import re
import socket
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import psutil


SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".json", ".yaml", ".yml", ".md", ".sql", ".service"}
EXCLUDED_PARTS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "release", "data"}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "generic_api_key": re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*['\"](?!UNKNOWN|NONE|NOT_TESTED)[^'\"]{12,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    roots = [root / "rescue_core", root / "ground" / "backend" / "app", root / "ground" / "frontend" / "src", root / "config", root / "scripts", root / "tools"]
    for source_root in roots:
        if not source_root.is_dir():
            continue
        for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
            relative = path.relative_to(root)
            if any(part in EXCLUDED_PARTS for part in relative.parts) or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            files.append({"path": relative.as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size})
    aggregate = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "rescueswarm_commit": "UNKNOWN",
        "file_count": len(files),
        "aggregate_sha256": aggregate,
        "files": files,
    }


def secret_audit(root: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for base in (root / "rescue_core", root / "ground", root / "config", root / "scripts", root / "tools"):
        if not base.is_dir():
            continue
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            relative = path.relative_to(root)
            if any(part in EXCLUDED_PARTS for part in relative.parts) or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(text):
                    findings.append({"path": relative.as_posix(), "pattern": name, "value": "REDACTED"})
    return {"passed": not findings, "finding_count": len(findings), "findings": findings}


def runtime_socket_audit(process_id: int | None = None) -> dict[str, Any]:
    """Classifies this process' sockets. Mission runtime public peers are a failure."""
    pid = process_id or psutil.Process().pid
    records: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    try:
        connections = psutil.Process(pid).net_connections(kind="inet")
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return {"status": "NOT_TESTED", "reason": "Socket inspection unavailable", "connections": []}
    for connection in connections:
        remote_host = connection.raddr.ip if connection.raddr else None
        if remote_host is None:
            classification = "LOCAL_LISTENER_OR_UNCONNECTED"
        else:
            try:
                address = ip_address(remote_host)
                classification = "LOOPBACK" if address.is_loopback else "LOCAL_PRIVATE" if address.is_private else "EXTERNAL"
            except ValueError:
                classification = "UNKNOWN_HOST"
        record = {
            "family": socket.AddressFamily(connection.family).name,
            "type": socket.SocketKind(connection.type).name,
            "local": f"{connection.laddr.ip}:{connection.laddr.port}" if connection.laddr else None,
            "remote": f"{connection.raddr.ip}:{connection.raddr.port}" if connection.raddr else None,
            "status": connection.status,
            "classification": classification,
        }
        records.append(record)
        if classification in {"EXTERNAL", "UNKNOWN_HOST"}:
            violations.append(record)
    return {"status": "PASS" if not violations else "FAIL", "process_id": pid, "connections": records, "violations": violations}

