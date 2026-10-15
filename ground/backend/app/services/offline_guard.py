"""Runtime offline-compliance evaluation; never changes firewall or networking."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from ..config import GroundConfig


class OfflineGuard:
    def __init__(self, config: GroundConfig, repository_root: Path):
        self.config = config
        self.repository_root = repository_root

    @staticmethod
    def _database_is_local(url: str) -> tuple[bool | None, str]:
        parsed = make_url(url)
        if parsed.drivername.startswith("sqlite"):
            return True, "SQLite local file/in-memory database"
        host = (parsed.host or "").lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return True, "Database host is loopback"
        if host:
            return None, f"Database host '{host}' requires deployment network verification"
        return None, "Database location unknown"

    def status(self, database_health: bool | None = None) -> dict[str, object]:
        database_local, database_reason = self._database_is_local(self.config.database.url)
        source_assets = self.repository_root / "ground" / "frontend" / "src"
        built_assets = self.repository_root / "ground" / "frontend" / "dist"
        local_static = source_assets.is_dir() or built_assets.is_dir()
        external_tiles = self.config.map.external_tile_sources
        external_apis = self.config.external_services.api_dependencies
        external_auth = self.config.external_services.auth_dependencies
        external_required = self.config.network.external_internet_required
        violations: list[str] = []
        if external_required:
            violations.append("Configuration requires external internet")
        if external_tiles:
            violations.append("External tile sources are configured")
        if external_apis:
            violations.append("External API dependencies are configured")
        if external_auth:
            violations.append("External authentication dependencies are configured")
        if not local_static:
            violations.append("Local frontend source/build assets are absent")
        if database_local is False:
            violations.append(database_reason)
        if self.config.competition.enabled and database_local is None:
            violations.append(f"Competition database is not proven local: {database_reason}")
        if self.config.competition.enabled and self.config.competition.external_network_allowed:
            violations.append("Competition configuration allows external networking")
        return {
            "status": "READY" if not violations else "FAILED",
            "external_network_required": external_required,
            "external_tile_sources": len(external_tiles),
            "external_api_dependencies": len(external_apis),
            "external_auth_dependencies": len(external_auth),
            "local_static_assets": "OK" if local_static else "MISSING",
            "local_database": "OK" if database_local and database_health is not False else "UNKNOWN",
            "local_database_reason": database_reason,
            "local_websocket": "OK",
            "competition_mode": self.config.competition.enabled,
            "violations": violations,
            "network_lockdown": "NOT_APPLIED_STAGE_2",
        }

    def enable_competition_network_lockdown(self) -> None:
        raise NotImplementedError(
            "Stage 2 only evaluates configuration; it never modifies firewall/network rules"
        )
