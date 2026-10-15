export function VideoPanel() {
  return (
    <section className="panel video-panel">
      <div className="panel-title"><h2>LIVE VIDEO</h2><span className="sim-tag">SIMULATED</span></div>
      <div className="test-pattern" role="img" aria-label="Digital twin camera view placeholder">
        <div className="reticle" />
        <span>DIGITAL TWIN VIEW · NOT PHYSICAL LIVE VIDEO</span>
      </div>
    </section>
  );
}
