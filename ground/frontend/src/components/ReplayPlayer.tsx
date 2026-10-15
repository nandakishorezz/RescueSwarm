import { useEffect, useMemo, useState } from "react";

import { useGcsStore } from "../stores/gcsStore";
import { DigitalTwinView } from "./DigitalTwinView";

const speeds = [.25, .5, 1, 2, 4];

export function ReplayPlayer() {
  const replay = useGcsStore((state) => state.replay);
  const selectedTargetId = useGcsStore((state) => state.selectedTargetId);
  const [playing, setPlaying] = useState(false); const [speed, setSpeed] = useState(1); const [timeMs, setTimeMs] = useState(0);
  const start = replay?.start_time ? Date.parse(replay.start_time) : 0;
  const end = replay?.end_time ? Date.parse(replay.end_time) : start;
  const duration = Math.max(0, end - start);
  useEffect(() => { setTimeMs(start); setPlaying(false); }, [start]);
  useEffect(() => {
    if (!playing || !duration) return;
    let previous = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now(); const delta = (now - previous) * speed; previous = now;
      setTimeMs((value) => { const next = value + delta; if (next >= end) { setPlaying(false); return end; } return next; });
    }, 33);
    return () => window.clearInterval(timer);
  }, [playing, speed, end, duration]);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      const element = event.target as HTMLElement | null;
      if (event.code !== "Space" || ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(element?.tagName ?? "")) return;
      event.preventDefault(); setPlaying((value) => !value);
    };
    window.addEventListener("keydown", keyboard); return () => window.removeEventListener("keydown", keyboard);
  }, []);
  const events = useMemo(() => replay?.events ?? [], [replay]);
  const seekEvent = (direction: -1 | 1) => {
    const candidates = events.map((event) => Date.parse(event.timestamp)).filter((value) => direction > 0 ? value > timeMs + 1 : value < timeMs - 1).sort((a, b) => direction > 0 ? a - b : b - a);
    if (candidates[0]) setTimeMs(candidates[0]);
  };
  const targetEvents = selectedTargetId ? events.filter((event) => event.target_id === selectedTargetId) : [];
  const seekTarget = (kinds: string[]) => {
    const event = targetEvents.find((item) => kinds.some((kind) => item.type.includes(kind)));
    if (event) setTimeMs(Date.parse(event.timestamp));
  };
  const elapsedMs = Math.max(0, timeMs - start);
  const elapsedLabel = `${String(Math.floor(elapsedMs / 60000)).padStart(2, "0")}:${String(Math.floor(elapsedMs / 1000) % 60).padStart(2, "0")}`;
  if (!replay || !replay.samples.length) return <section className="replay-player panel"><h2>3D REPLAY</h2><p className="empty">NO TIMESTAMPED TELEMETRY AVAILABLE FOR REPLAY</p></section>;
  return <section className="replay-player">
    <div className="replay-heading"><div><span className="simulation-badge">RECORDED REPLAY</span><b>{selectedTargetId ? `TARGET ${selectedTargetId}` : "MISSION TIMELINE"}</b></div><div><span>ELAPSED {elapsedLabel}</span><time>{new Date(timeMs).toLocaleString()}</time></div></div>
    <div className="replay-stage"><DigitalTwinView replayTimeMs={timeMs} /></div>
    <div className="replay-controls">
      <button onClick={() => setPlaying((value) => !value)}>{playing ? "PAUSE" : "PLAY"}</button>
      <button onClick={() => seekEvent(-1)}>PREVIOUS EVENT</button><button onClick={() => seekEvent(1)}>NEXT EVENT</button>
      <input aria-label="Replay position" type="range" min={start} max={end} value={timeMs} onChange={(event) => { setPlaying(false); setTimeMs(Number(event.target.value)); }} />
      <time title={new Date(timeMs).toISOString()}>{new Date(timeMs).toLocaleTimeString()}</time>
      <div className="speed-buttons">{speeds.map((value) => <button className={speed === value ? "active" : ""} onClick={() => setSpeed(value)} key={value}>{value}×</button>)}</div>
    </div>
    {selectedTargetId && <div className="target-replay-shortcuts"><button disabled={!targetEvents.some((event) => event.type.includes("DETECTION"))} onClick={() => seekTarget(["DETECTION", "CANDIDATE"])}>FIRST DETECTION</button><button disabled={!targetEvents.some((event) => event.type.includes("CONFIRM"))} onClick={() => seekTarget(["CONFIRM"])}>CONFIRMATION</button><button disabled={!targetEvents.some((event) => event.type.includes("DELIVER"))} onClick={() => seekTarget(["DELIVER", "DROP"])}>DELIVERY</button></div>}
    <div className="replay-event-strip">{events.map((event) => <button key={event.event_id} className={event.severity !== "INFO" ? "warning" : ""} onClick={() => setTimeMs(Date.parse(event.timestamp))}><time>{new Date(event.timestamp).toLocaleTimeString()}</time><b>{event.type.replaceAll("_", " ")}</b><span>{event.vehicle_id ?? "FLEET"}</span></button>)}</div>
  </section>;
}
