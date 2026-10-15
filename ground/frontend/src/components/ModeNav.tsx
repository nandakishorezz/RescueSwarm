import { useGcsStore } from "../stores/gcsStore";
import type { ApplicationMode } from "../types/domain";

const modes: ApplicationMode[] = ["SETUP", "MISSION", "SIMULATION", "MULTIDRONE", "ENGINEERING", "REVIEW"];

export function ModeNav() {
  const mode = useGcsStore((state) => state.mode);
  const setMode = useGcsStore((state) => state.setMode);
  const competition = useGcsStore((state) => state.competitionMode);
  return (
    <nav className="mode-nav" aria-label="Application mode">
      {modes.filter((item) => !(competition && ["ENGINEERING", "SIMULATION", "MULTIDRONE"].includes(item))).map((item) => (
        <button key={item} className={mode === item ? "active" : ""} onClick={() => { setMode(item); window.history.replaceState(null, "", `?mode=${item.toLowerCase()}`); }}>
          {item}
        </button>
      ))}
    </nav>
  );
}
