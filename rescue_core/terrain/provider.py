"""Terrain elevation abstraction for terrain-aware autonomy."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod


class TerrainProvider(ABC):
    """Abstract interface for terrain elevation data."""
    
    source: str
    
    @abstractmethod
    def get_elevation_wgs84(self, latitude: float, longitude: float) -> float | None:
        """Return terrain elevation in meters above Mean Sea Level (MSL) / WGS84 ellipsoid.
        Returns None if elevation data is unavailable for the given coordinates.
        """

    @abstractmethod
    def get_elevation_enu(self, east: float, north: float) -> float | None:
        """Return terrain elevation in meters relative to the local ENU origin.
        Returns None if elevation data is unavailable.
        """


class FlatTerrainProvider(TerrainProvider):
    """Fallback terrain provider representing a flat surface at a constant elevation."""
    
    def __init__(self, elevation_wgs84: float = 0.0, elevation_enu: float = 0.0, source: str = "FLAT_FALLBACK"):
        self.elevation_wgs84 = float(elevation_wgs84)
        self.elevation_enu = float(elevation_enu)
        self.source = source
        
    def get_elevation_wgs84(self, latitude: float, longitude: float) -> float | None:
        return self.elevation_wgs84
        
    def get_elevation_enu(self, east: float, north: float) -> float | None:
        return self.elevation_enu


class SineWaveSimulationTerrain(TerrainProvider):
    """A deterministic, variable terrain surface for SITL testing.
    Creates realistic rolling hills using mathematical functions without external data.
    """
    
    def __init__(self, amplitude_m: float = 10.0, wavelength_m: float = 50.0, origin_wgs84_alt: float = 0.0):
        self.amplitude_m = float(amplitude_m)
        self.wavelength_m = float(wavelength_m)
        self.origin_wgs84_alt = origin_wgs84_alt
        self.source = "DETERMINISTIC_SIMULATION_SINE_TERRAIN"
        
    def get_elevation_wgs84(self, latitude: float, longitude: float) -> float | None:
        # We don't have a strict conversion here without the LocalCoordinateFrame, 
        # so this provider expects to be primarily queried in ENU for simulation.
        # If needed, it returns origin altitude as a fallback.
        return self.origin_wgs84_alt
        
    def get_elevation_enu(self, east: float, north: float) -> float | None:
        # A 2D sine wave: elevation = A * sin(2*pi*x/L) * cos(2*pi*y/L)
        # We shift it so origin (0,0) is at elevation 0.0
        # Actually, sin(0) = 0, so it naturally passes through 0 at the origin!
        elev = self.amplitude_m * math.sin(2 * math.pi * east / self.wavelength_m) * math.cos(2 * math.pi * north / self.wavelength_m)
        return float(elev)
