"""Deterministic Stage-13SIM Phase-3 sensor-realism contracts.

The ArduPilot parameters in these profiles act in the SITL sensor backends,
before EKF3.  They are reference presets, not claims about measured hardware.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .profiles import Provenance


class SensorProfileId(StrEnum):
    IDEAL_CONTROL = "IDEAL_CONTROL"
    REFERENCE_NOMINAL = "REFERENCE_NOMINAL"
    REFERENCE_CHALLENGING = "REFERENCE_CHALLENGING"


class GpsRealism(BaseModel):
    horizontal_noise_m: float = Field(ge=0, le=20)
    vertical_noise_m: float = Field(ge=0, le=30)
    update_rate_hz: float = Field(gt=0, le=20)
    lag_ms: float = Field(ge=0, le=1000)
    vertical_drift_m: float = Field(ge=0, le=10)
    accuracy_m: float = Field(gt=0, le=20)
    provenance: Provenance


class ImuRealism(BaseModel):
    gyro_motor_noise: float = Field(ge=0, le=2)
    accelerometer_motor_noise: float = Field(ge=0, le=10)
    accelerometer_bias_mps2: tuple[float, float, float]
    provenance: Provenance


class CompassRealism(BaseModel):
    noise_mgauss: float = Field(ge=0, le=100)
    bias_mgauss: tuple[float, float, float]
    delay_ms: float = Field(ge=0, le=500)
    provenance: Provenance


class BarometerRealism(BaseModel):
    noise_m: float = Field(ge=0, le=20)
    drift_mps: float = Field(ge=0, le=1)
    delay_ms: float = Field(ge=0, le=1000)
    provenance: Provenance


class CameraRealism(BaseModel):
    latency_ms: float = Field(ge=0, le=2000)
    jitter_ms: float = Field(ge=0, le=500)
    gaussian_noise_stddev_dn: float = Field(ge=0, le=50)
    brightness_gain: float = Field(gt=0, le=3)
    contrast_gain: float = Field(gt=0, le=3)
    exposure_preset: Literal["NORMAL_DAY", "DIMMER_DAY", "BRIGHT_DAY"]
    exposure_time_us: float = Field(ge=0, le=100_000)
    max_pending_frames: int = Field(ge=2, le=64)
    motion_blur_model: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    rolling_shutter_model: Literal["NOT_APPLICABLE_TO_REFERENCE_CAMERA"] = (
        "NOT_APPLICABLE_TO_REFERENCE_CAMERA"
    )
    provenance: Provenance

    @model_validator(mode="after")
    def queue_covers_nominal_delay(self) -> "CameraRealism":
        if self.max_pending_frames < 2:
            raise ValueError("camera latency queue must be bounded and non-trivial")
        return self


class SensorSeeds(BaseModel):
    sensor_seed: int = 31003
    wind_seed: int = 84521
    scenario_seed: int = 7117


class SensorRealismProfile(BaseModel):
    schema_version: Literal["3.0"] = "3.0"
    profile_id: SensorProfileId
    label: str
    physical_calibration: Literal[False] = False
    gps: GpsRealism
    imu: ImuRealism
    compass: CompassRealism
    barometer: BarometerRealism
    camera: CameraRealism
    isolation_component: Literal["GPS", "IMU", "COMPASS", "BAROMETER", "CAMERA"] | None = None

    def ardupilot_sensor_parameters(self) -> dict[str, float]:
        """SITL measurement-layer parameters; no EKF-output mutation."""
        ax, ay, az = self.imu.accelerometer_bias_mps2
        mx, my, mz = self.compass.bias_mgauss
        parameters = {
            "SIM_GPS1_HNSE": self.gps.horizontal_noise_m,
            "SIM_GPS1_NOISE": self.gps.vertical_noise_m,
            "SIM_GPS1_HZ": self.gps.update_rate_hz,
            "SIM_GPS1_LAG_MS": self.gps.lag_ms,
            "SIM_GPS1_DRFTALT": self.gps.vertical_drift_m,
            "SIM_GPS1_ACC": self.gps.accuracy_m,
            "SIM_GYR1_RND": self.imu.gyro_motor_noise,
            "SIM_ACC1_RND": self.imu.accelerometer_motor_noise,
            "SIM_ACC1_BIAS_X": ax,
            "SIM_ACC1_BIAS_Y": ay,
            "SIM_ACC1_BIAS_Z": az,
            "SIM_MAG_RND": self.compass.noise_mgauss,
            "SIM_MAG_DELAY": self.compass.delay_ms,
            "SIM_BARO_RND": self.barometer.noise_m,
            "SIM_BARO_DRIFT": self.barometer.drift_mps,
            "SIM_BARO_DELAY": self.barometer.delay_ms,
        }
        # An all-zero SITL compass offset is interpreted by ArduPilot as an
        # uncalibrated compass. Preserve the already-qualified vehicle default
        # for ideal/other-sensor-only controls; non-zero Phase-3 bias is
        # explicitly injected for compass experiments.
        if any((mx, my, mz)):
            parameters.update({
                "SIM_MAG1_OFS_X": mx, "SIM_MAG1_OFS_Y": my, "SIM_MAG1_OFS_Z": mz,
            })
        return parameters

    def parameter_file(self) -> str:
        lines = [
            "# Stage 13SIM Phase 3 sensor-measurement realism.",
            "# REFERENCE/ENGINEERING values; PHYSICAL_UNVERIFIED.",
            "# Applied by ArduPilot SITL sensor backends before EKF3.",
        ]
        lines.extend(f"{name} {value:.9g}" for name, value in self.ardupilot_sensor_parameters().items())
        return "\n".join(lines) + "\n"


def sensor_realism_profiles() -> dict[SensorProfileId, SensorRealismProfile]:
    ref = Provenance.REFERENCE_PRESET
    estimate = Provenance.ENGINEERING_ESTIMATE
    sim = Provenance.SIMULATION_ONLY
    return {
        SensorProfileId.IDEAL_CONTROL: SensorRealismProfile(
            profile_id=SensorProfileId.IDEAL_CONTROL, label="Ideal control",
            gps=GpsRealism(horizontal_noise_m=0, vertical_noise_m=0, update_rate_hz=5,
                           lag_ms=100, vertical_drift_m=0, accuracy_m=0.3, provenance=sim),
            imu=ImuRealism(gyro_motor_noise=0, accelerometer_motor_noise=0,
                           accelerometer_bias_mps2=(0, 0, 0), provenance=sim),
            compass=CompassRealism(noise_mgauss=0, bias_mgauss=(0, 0, 0), delay_ms=0, provenance=sim),
            barometer=BarometerRealism(noise_m=.2, drift_mps=0, delay_ms=0, provenance=sim),
            camera=CameraRealism(latency_ms=0, jitter_ms=0, gaussian_noise_stddev_dn=0,
                                 brightness_gain=2.0, contrast_gain=1, exposure_preset="NORMAL_DAY",
                                 exposure_time_us=1000, max_pending_frames=16, provenance=sim),
        ),
        SensorProfileId.REFERENCE_NOMINAL: SensorRealismProfile(
            profile_id=SensorProfileId.REFERENCE_NOMINAL, label="Reference nominal",
            gps=GpsRealism(horizontal_noise_m=0.8, vertical_noise_m=1.2, update_rate_hz=5,
                           lag_ms=120, vertical_drift_m=0.05, accuracy_m=0.8, provenance=ref),
            imu=ImuRealism(gyro_motor_noise=0.02, accelerometer_motor_noise=0.06,
                           accelerometer_bias_mps2=(0.015, -0.010, 0.020), provenance=estimate),
            compass=CompassRealism(noise_mgauss=2.0, bias_mgauss=(4.0, -3.0, 2.0),
                                   delay_ms=20, provenance=estimate),
            barometer=BarometerRealism(noise_m=0.30, drift_mps=0.002, delay_ms=20, provenance=ref),
            camera=CameraRealism(latency_ms=100, jitter_ms=8, gaussian_noise_stddev_dn=2.5,
                                 brightness_gain=3.0, contrast_gain=1.0, exposure_preset="NORMAL_DAY",
                                 exposure_time_us=1000, max_pending_frames=16, provenance=ref),
        ),
        SensorProfileId.REFERENCE_CHALLENGING: SensorRealismProfile(
            profile_id=SensorProfileId.REFERENCE_CHALLENGING, label="Reference challenging",
            gps=GpsRealism(horizontal_noise_m=1.8, vertical_noise_m=2.8, update_rate_hz=5,
                           lag_ms=220, vertical_drift_m=0.15, accuracy_m=1.8, provenance=ref),
            imu=ImuRealism(gyro_motor_noise=0.06, accelerometer_motor_noise=0.15,
                           accelerometer_bias_mps2=(0.04, -0.03, 0.05), provenance=estimate),
            compass=CompassRealism(noise_mgauss=6.0, bias_mgauss=(12.0, -9.0, 5.0),
                                   delay_ms=45, provenance=estimate),
            barometer=BarometerRealism(noise_m=0.80, drift_mps=0.006, delay_ms=50, provenance=ref),
            camera=CameraRealism(latency_ms=200, jitter_ms=18, gaussian_noise_stddev_dn=6,
                                 brightness_gain=3.0, contrast_gain=0.92, exposure_preset="DIMMER_DAY",
                                 exposure_time_us=2000, max_pending_frames=16, provenance=ref),
        ),
    }


def get_sensor_realism_profile(profile_id: SensorProfileId | str) -> SensorRealismProfile:
    key = SensorProfileId(profile_id)
    return sensor_realism_profiles()[key].model_copy(deep=True)


def get_isolated_sensor_profile(
    profile_id: SensorProfileId | str,
    component: Literal["GPS", "IMU", "COMPASS", "BAROMETER", "CAMERA"],
) -> SensorRealismProfile:
    """Return an explicit engineering experiment with only one non-ideal sensor."""
    selected = get_sensor_realism_profile(profile_id)
    isolated = get_sensor_realism_profile(SensorProfileId.IDEAL_CONTROL)
    field = component.lower()
    setattr(isolated, field, getattr(selected, field).model_copy(deep=True))
    isolated.profile_id = selected.profile_id
    isolated.label = f"{selected.label} / {component}-only engineering isolation"
    isolated.isolation_component = component
    return isolated


def camera_exposure_presets() -> dict[str, dict[str, float | str]]:
    """Simple deterministic grayscale sensitivity presets, not radiometry."""
    return {
        "NORMAL_DAY": {"brightness_gain": 1.0, "contrast_gain": 1.0, "provenance": "REFERENCE_PRESET"},
        "DIMMER_DAY": {"brightness_gain": 3.0, "contrast_gain": 0.92, "provenance": "ENGINEERING_ESTIMATE"},
        "BRIGHT_DAY": {"brightness_gain": 1.18, "contrast_gain": 1.02, "provenance": "ENGINEERING_ESTIMATE"},
    }


def get_sensor_realism_profile(profile_id: str | SensorProfileId) -> SensorRealismProfile:
    return sensor_realism_profiles()[SensorProfileId(profile_id)]
