export function AttitudeIndicator({ rollDeg, pitchDeg, headingDeg }: { rollDeg?: number | null; pitchDeg?: number | null; headingDeg?: number | null }) {
  const known = rollDeg != null && pitchDeg != null;
  return <div className="attitude-instrument" aria-label="Aircraft attitude indicator">
    <div className="attitude-sphere" style={{ transform: known ? `rotate(${-rollDeg!}deg)` : undefined }}>
      <div className="attitude-horizon" style={{ transform: known ? `translateY(${Math.max(-36, Math.min(36, pitchDeg! * 1.6))}px)` : undefined }}>
        <div className="sky"/><div className="ground"/><i />
      </div>
    </div>
    <div className="attitude-aircraft">—◆—</div>
    <div className="attitude-values"><span>R {rollDeg?.toFixed(1) ?? "—"}°</span><span>P {pitchDeg?.toFixed(1) ?? "—"}°</span><span>HDG {headingDeg?.toFixed(0) ?? "—"}°</span></div>
  </div>;
}

