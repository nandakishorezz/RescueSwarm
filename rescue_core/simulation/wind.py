"""Deterministic wind truth schedule; Gazebo remains the force authority."""

from __future__ import annotations

import math
import random
from pydantic import BaseModel

from .profiles import WindMode, WindProfile


class WindSample(BaseModel):
    sim_time_s: float
    east_mps: float
    north_mps: float
    up_mps: float
    gust_active: bool


def generate_wind_schedule(profile: WindProfile, duration_s: float, step_s: float = 0.1) -> list[WindSample]:
    if duration_s <= 0 or step_s <= 0:
        raise ValueError("wind duration and step must be positive")
    rng = random.Random(profile.random_seed)
    values: list[WindSample] = []
    gust_until = -1.0
    next_gust = rng.uniform(4.0, 9.0)
    gust_speed = 0.0
    count = int(math.floor(duration_s / step_s)) + 1
    for index in range(count):
        t = round(index * step_s, 9)
        gust = False
        if profile.enabled and profile.gusts_enabled and t >= next_gust and t >= gust_until:
            gust_until = t + rng.uniform(profile.gust_min_duration_s.value, profile.gust_max_duration_s.value)
            gust_speed = rng.uniform(profile.mean_speed_mps.value, profile.gust_max_speed_mps.value)
            next_gust = gust_until + rng.uniform(5.0, 12.0)
        if t < gust_until:
            gust = True
        speed = profile.mean_speed_mps.value if profile.enabled and profile.mode != WindMode.CALM else 0.0
        direction = profile.mean_direction_deg.value
        if profile.enabled and profile.turbulence_enabled:
            speed += rng.gauss(0.0, profile.turbulence_intensity.value)
            direction += rng.uniform(-profile.direction_variation_deg.value, profile.direction_variation_deg.value)
        if gust:
            speed = max(speed, gust_speed)
        speed = max(0.0, speed)
        radians = math.radians(direction)
        values.append(WindSample(
            sim_time_s=t,
            east_mps=speed * math.sin(radians),
            north_mps=speed * math.cos(radians),
            up_mps=profile.vertical_speed_mps.value if profile.enabled else 0.0,
            gust_active=gust,
        ))
    return values
