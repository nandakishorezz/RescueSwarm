import React, { useEffect, useState, useMemo } from "react";
import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";
import type { FlightParameters, VehicleState } from "../types/domain";

const DEFAULT_PARAMS: FlightParameters = {
  search_altitude_m: 15.0,
  search_speed_m_s: 4.0,
  transit_speed_m_s: 5.0,
  approach_altitude_m: 8.0,
  approach_speed_m_s: 3.0,
  delivery_altitude_m: 4.0,
  delivery_stabilization_s: 3.0,
  safe_drop_offset_m: 2.5,
  search_swath_overlap_percent: 50.0,
  rtl_altitude_m: 15.0,
  rtl_speed_m_s: 4.0,
  payload_slots: 2,
};

export function FlightParametersPanel() {
  const mission = useGcsStore((state) => state.mission);
  const setMission = useGcsStore((state) => state.setMission);
  const vehicles = useGcsStore((state) => state.vehicles);
  const activeVehicle: VehicleState | undefined = Object.values(vehicles)[0];

  const isMissionActive = mission?.state === "RUNNING" || mission?.state === "RETURNING";

  const [form, setForm] = useState<FlightParameters>(() => {
    return mission?.flight_parameters ?? DEFAULT_PARAMS;
  });
  const [activeSnapshot, setActiveSnapshot] = useState<FlightParameters | null>(null);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  // Sync when mission changes or loads
  useEffect(() => {
    if (mission?.flight_parameters) {
      setForm(mission.flight_parameters);
      if (isMissionActive && !activeSnapshot) {
        setActiveSnapshot(mission.flight_parameters);
      }
    } else if (mission?.id) {
      api.flightParameters(mission.id)
        .then((params) => {
          setForm(params);
          if (isMissionActive && !activeSnapshot) {
            setActiveSnapshot(params);
          }
        })
        .catch(() => {});
    }
  }, [mission?.id, isMissionActive]);

  // Snapshot on mission active transition
  useEffect(() => {
    if (isMissionActive && !activeSnapshot) {
      setActiveSnapshot(form);
    } else if (!isMissionActive && activeSnapshot) {
      setActiveSnapshot(null);
    }
  }, [isMissionActive]);

  // Validation rules
  const validationErrors = useMemo(() => {
    const errs: string[] = [];
    if (form.search_altitude_m < 3.0 || form.search_altitude_m > 50.0) {
      errs.push("Search altitude must be between 3.0m and 50.0m");
    }
    if (form.approach_altitude_m < 3.0 || form.approach_altitude_m > 30.0) {
      errs.push("Approach altitude must be between 3.0m and 30.0m");
    }
    if (form.delivery_altitude_m < 1.0 || form.delivery_altitude_m > 10.0) {
      errs.push("Delivery altitude must be between 1.0m and 10.0m");
    }
    if (form.delivery_altitude_m >= form.approach_altitude_m) {
      errs.push(`Delivery altitude (${form.delivery_altitude_m.toFixed(1)}m) must be strictly below approach altitude (${form.approach_altitude_m.toFixed(1)}m)`);
    }
    if (form.delivery_altitude_m >= form.search_altitude_m) {
      errs.push(`Delivery altitude (${form.delivery_altitude_m.toFixed(1)}m) must be strictly below search altitude (${form.search_altitude_m.toFixed(1)}m)`);
    }
    if (form.approach_altitude_m > form.search_altitude_m) {
      errs.push(`Approach altitude (${form.approach_altitude_m.toFixed(1)}m) cannot exceed search altitude (${form.search_altitude_m.toFixed(1)}m)`);
    }
    if (form.safe_drop_offset_m < 1.0 || form.safe_drop_offset_m > 10.0) {
      errs.push("Safe drop standoff offset must be at least 1.0m (never drop directly on survivor) up to 10.0m");
    }
    if (form.search_speed_m_s < 1.0 || form.search_speed_m_s > 15.0) {
      errs.push("Search speed must be between 1.0m/s and 15.0m/s");
    }
    if (form.approach_speed_m_s < 0.5 || form.approach_speed_m_s > 8.0) {
      errs.push("Approach speed must be between 0.5m/s and 8.0m/s");
    }
    if (form.delivery_stabilization_s < 0.5 || form.delivery_stabilization_s > 15.0) {
      errs.push("Delivery stabilization hold must be between 0.5s and 15.0s");
    }
    if (form.search_swath_overlap_percent < 10.0 || form.search_swath_overlap_percent > 90.0) {
      errs.push("Search swath overlap must be between 10% and 90%");
    }
    if (form.rtl_altitude_m < 3.0 || form.rtl_altitude_m > 50.0) {
      errs.push("RTL altitude must be between 3.0m and 50.0m");
    }
    if (form.rtl_speed_m_s < 1.0 || form.rtl_speed_m_s > 15.0) {
      errs.push("RTL speed must be between 1.0m/s and 15.0m/s");
    }
    if (form.payload_slots < 1 || form.payload_slots > 10) {
      errs.push("Payload kit slots must be between 1 and 10");
    }
    return errs;
  }, [form]);

  const isValid = validationErrors.length === 0;

  const handleChange = (field: keyof FlightParameters, value: number) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    setSaveStatus(null);
    setErrorStatus(null);
  };

  const handleSave = async () => {
    if (!mission?.id) {
      setErrorStatus("No mission loaded to configure");
      return;
    }
    if (!isValid) return;

    setIsSaving(true);
    setSaveStatus(null);
    setErrorStatus(null);
    try {
      const updated = await api.updateFlightParameters(mission.id, form);
      setForm(updated);
      setMission({ ...mission, flight_parameters: updated });
      setSaveStatus("Flight parameters saved and active for mission execution");
    } catch (err: any) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorStatus(`Save failed: ${msg}`);
    } finally {
      setIsSaving(false);
    }
  };

  const handleResetDefaults = () => {
    setForm(DEFAULT_PARAMS);
    setSaveStatus("Reset to standard SITL defaults");
    setErrorStatus(null);
  };

  const currentAlt = activeVehicle?.relative_altitude_m ?? 0;
  const currentSpeed = activeVehicle?.ground_speed_m_s ?? 0;

  return (
    <section className="panel flight-parameters-panel" style={{ padding: "12px", fontSize: "0.82rem" }}>
      <div className="panel-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
        <h2 style={{ fontSize: "0.9rem", margin: 0, color: "#6ee7b7", letterSpacing: "0.05em" }}>FLIGHT & DELIVERY PARAMETERS</h2>
        {isMissionActive ? (
          <span style={{ background: "#065f46", color: "#6ee7b7", padding: "2px 6px", borderRadius: "4px", fontSize: "0.72rem", fontWeight: 700 }}>
            LOCKED SNAPSHOT
          </span>
        ) : (
          <span style={{ background: "#1e3a5f", color: "#93c5fd", padding: "2px 6px", borderRadius: "4px", fontSize: "0.72rem" }}>
            CONFIGURABLE
          </span>
        )}
      </div>

      {isMissionActive && (
        <div style={{ background: "#0d2818", border: "1px solid #10b981", borderRadius: "6px", padding: "8px", marginBottom: "10px" }}>
          <div style={{ color: "#34d399", fontWeight: 700, marginBottom: "4px", fontSize: "0.78rem" }}>
            🔒 ACTIVE RUN SNAPSHOT ENFORCED
          </div>
          <div style={{ color: "#a7f3d0", fontSize: "0.75rem", lineHeight: 1.4 }}>
            Setpoints are locked during flight to prevent sudden in-maneuver trajectory jumps. Modifications will apply to the next mission.
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px", marginTop: "6px", fontSize: "0.75rem", background: "#061a10", padding: "6px", borderRadius: "4px" }}>
            <div>Telemetry Alt: <strong style={{ color: "#6ee7b7" }}>{currentAlt.toFixed(1)} m</strong></div>
            <div>Target Search Alt: <strong>{activeSnapshot?.search_altitude_m ?? form.search_altitude_m} m</strong></div>
            <div>Ground Speed: <strong style={{ color: "#6ee7b7" }}>{currentSpeed.toFixed(1)} m/s</strong></div>
            <div>Cruise Speed: <strong>{activeSnapshot?.search_speed_m_s ?? form.search_speed_m_s} m/s</strong></div>
          </div>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        {/* Search Phase Parameters */}
        <div style={{ background: "#111827", borderRadius: "6px", padding: "8px", border: "1px solid #1f2937" }}>
          <div style={{ color: "#93c5fd", fontWeight: 700, fontSize: "0.78rem", marginBottom: "6px" }}>🔍 SEARCH CRUISE</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Search Altitude (m)</span>
              <input
                type="number"
                step="0.5"
                min="3.0"
                max="50.0"
                disabled={isMissionActive}
                value={form.search_altitude_m}
                onChange={(e) => handleChange("search_altitude_m", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Search Speed (m/s)</span>
              <input
                type="number"
                step="0.5"
                min="1.0"
                max="15.0"
                disabled={isMissionActive}
                value={form.search_speed_m_s}
                onChange={(e) => handleChange("search_speed_m_s", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Transit Speed (m/s)</span>
              <input
                type="number"
                step="0.5"
                min="1.0"
                max="15.0"
                disabled={isMissionActive}
                value={form.transit_speed_m_s}
                onChange={(e) => handleChange("transit_speed_m_s", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Swath Overlap (%)</span>
              <input
                type="number"
                step="5.0"
                min="10.0"
                max="90.0"
                disabled={isMissionActive}
                value={form.search_swath_overlap_percent}
                onChange={(e) => handleChange("search_swath_overlap_percent", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
          </div>
        </div>

        {/* Approach & Delivery Parameters */}
        <div style={{ background: "#111827", borderRadius: "6px", padding: "8px", border: "1px solid #1f2937" }}>
          <div style={{ color: "#34d399", fontWeight: 700, fontSize: "0.78rem", marginBottom: "6px" }}>🎯 APPROACH & SAFE DELIVERY</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Approach Altitude (m)</span>
              <input
                type="number"
                step="0.5"
                min="3.0"
                max="30.0"
                disabled={isMissionActive}
                value={form.approach_altitude_m}
                onChange={(e) => handleChange("approach_altitude_m", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Approach Speed (m/s)</span>
              <input
                type="number"
                step="0.5"
                min="0.5"
                max="8.0"
                disabled={isMissionActive}
                value={form.approach_speed_m_s}
                onChange={(e) => handleChange("approach_speed_m_s", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Delivery Hover Alt (m)</span>
              <input
                type="number"
                step="0.5"
                min="1.0"
                max="10.0"
                disabled={isMissionActive}
                value={form.delivery_altitude_m}
                onChange={(e) => handleChange("delivery_altitude_m", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Hold/Stabilization (s)</span>
              <input
                type="number"
                step="0.5"
                min="0.5"
                max="15.0"
                disabled={isMissionActive}
                value={form.delivery_stabilization_s}
                onChange={(e) => handleChange("delivery_stabilization_s", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Safe Drop Offset (m)</span>
              <input
                type="number"
                step="0.5"
                min="1.0"
                max="10.0"
                disabled={isMissionActive}
                value={form.safe_drop_offset_m}
                onChange={(e) => handleChange("safe_drop_offset_m", parseFloat(e.target.value) || 0)}
                title="Standoff distance from survivor body to prevent injury"
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>Payload Kit Slots</span>
              <input
                type="number"
                step="1"
                min="1"
                max="10"
                disabled={isMissionActive}
                value={form.payload_slots}
                onChange={(e) => handleChange("payload_slots", parseInt(e.target.value, 10) || 1)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
          </div>
        </div>

        {/* Return to Launch Parameters */}
        <div style={{ background: "#111827", borderRadius: "6px", padding: "8px", border: "1px solid #1f2937" }}>
          <div style={{ color: "#f59e0b", fontWeight: 700, fontSize: "0.78rem", marginBottom: "6px" }}>🏠 RETURN TO LAUNCH (RTL)</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>RTL Altitude (m)</span>
              <input
                type="number"
                step="0.5"
                min="3.0"
                max="50.0"
                disabled={isMissionActive}
                value={form.rtl_altitude_m}
                onChange={(e) => handleChange("rtl_altitude_m", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ color: "#9ca3af", fontSize: "0.74rem" }}>RTL Speed (m/s)</span>
              <input
                type="number"
                step="0.5"
                min="1.0"
                max="15.0"
                disabled={isMissionActive}
                value={form.rtl_speed_m_s}
                onChange={(e) => handleChange("rtl_speed_m_s", parseFloat(e.target.value) || 0)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: "4px", padding: "4px 8px", fontSize: "0.82rem" }}
              />
            </label>
          </div>
        </div>
      </div>

      {/* Inline Validation Warnings */}
      {validationErrors.length > 0 && (
        <div style={{ marginTop: "10px", background: "#450a0a", border: "1px solid #dc2626", borderRadius: "6px", padding: "8px", color: "#fca5a5", fontSize: "0.75rem" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>VALIDATION ERRORS:</strong>
          <ul style={{ margin: 0, paddingLeft: "16px" }}>
            {validationErrors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Status Messages */}
      {saveStatus && (
        <div style={{ marginTop: "8px", color: "#34d399", fontSize: "0.76rem" }}>
          ✅ {saveStatus}
        </div>
      )}
      {errorStatus && (
        <div style={{ marginTop: "8px", color: "#f87171", fontSize: "0.76rem" }}>
          ⚠ {errorStatus}
        </div>
      )}

      {/* Action Buttons */}
      {!isMissionActive && (
        <div style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
          <button
            onClick={handleSave}
            disabled={!isValid || isSaving || !mission?.id}
            style={{
              flex: 1,
              background: isValid ? "#059669" : "#374151",
              color: isValid ? "#ecfdf5" : "#9ca3af",
              border: "none",
              borderRadius: "4px",
              padding: "6px 12px",
              fontWeight: 700,
              fontSize: "0.78rem",
              cursor: isValid ? "pointer" : "not-allowed",
              transition: "background 0.15s ease",
            }}
          >
            {isSaving ? "SAVING…" : "APPLY & SAVE SETPOINTS"}
          </button>
          <button
            onClick={handleResetDefaults}
            disabled={isSaving}
            style={{
              background: "#1f2937",
              color: "#9ca3af",
              border: "1px solid #374151",
              borderRadius: "4px",
              padding: "6px 10px",
              fontSize: "0.74rem",
              cursor: "pointer",
            }}
          >
            RESET
          </button>
        </div>
      )}
    </section>
  );
}
