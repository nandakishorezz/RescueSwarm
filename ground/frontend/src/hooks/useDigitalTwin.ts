import { useEffect } from "react";

import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";

export function useDigitalTwin(missionId?: string): void {
  const setDigitalTwin = useGcsStore((state) => state.setDigitalTwin);
  useEffect(() => {
    if (!missionId) { setDigitalTwin(null); return; }
    let active = true;
    const refresh = () => api.digitalTwin(missionId).then((snapshot) => { if (active) setDigitalTwin(snapshot); }).catch(() => undefined);
    refresh();
    const timer = window.setInterval(refresh, 1_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [missionId, setDigitalTwin]);
}
