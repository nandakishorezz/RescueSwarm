"""Fail-closed safety checks for Stage 3 command-capable connections."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass


class SITLSafetyError(PermissionError):
    """Raised before any connection is opened when SITL authority is not proven."""


@dataclass(frozen=True)
class ApprovedSITLEndpoint:
    transport: str
    host: str
    port: int

    @property
    def connection_string(self) -> str:
        return f"{self.transport}:{self.host}:{self.port}"


class SITLSafetyGuard:
    """Allow commands only to explicit loopback MAVLink network endpoints.

    Both semantic authority labels and the endpoint must pass. Serial devices,
    Windows COM ports, wildcard listeners, hostnames, and non-loopback addresses
    are rejected before pymavlink is called.
    """

    _ENDPOINT = re.compile(r"^(tcp|udp|udpin|udpout):(\[[^]]+\]|[^:]+):(\d{1,5})$", re.IGNORECASE)

    def __init__(self, environment: str, command_authority: str):
        self.environment = environment
        self.command_authority = command_authority

    def validate(self, connection: str) -> ApprovedSITLEndpoint:
        if self.environment != "sitl":
            raise SITLSafetyError("Stage 3 commands require environment=sitl")
        if self.command_authority != "simulation_only":
            raise SITLSafetyError("Stage 3 commands require command_authority=simulation_only")
        value = connection.strip()
        if not value:
            raise SITLSafetyError("SITL connection is empty")
        if re.match(r"^COM\d+$", value, re.IGNORECASE) or value.startswith(("/dev/", "serial:")):
            raise SITLSafetyError(f"Serial hardware connection is forbidden for Stage 3 commands: {value}")
        match = self._ENDPOINT.fullmatch(value)
        if match is None:
            raise SITLSafetyError("SITL commands require tcp/udp/udpin/udpout with an explicit loopback IP")
        transport, raw_host, raw_port = match.groups()
        host = raw_host.strip("[]")
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise SITLSafetyError("SITL host must be an explicit loopback IP, not a hostname") from exc
        if not address.is_loopback:
            raise SITLSafetyError(f"Non-loopback SITL command endpoint is forbidden: {host}")
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise SITLSafetyError("SITL port must be between 1 and 65535")
        normalized_host = f"[{host}]" if address.version == 6 else host
        return ApprovedSITLEndpoint(transport.lower(), normalized_host, port)
