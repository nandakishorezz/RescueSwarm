import { useEffect } from "react";

import { websocketUrl } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";
import type { RealtimeEnvelope } from "../types/domain";

export function useMissionSocket(missionId: string | undefined): void {
  const applyEnvelope = useGcsStore((state) => state.applyEnvelope);
  const setState = useGcsStore((state) => state.setWebsocketState);
  const setError = useGcsStore((state) => state.setError);
  const refresh = useGcsStore((state) => state.refreshLinkStates);

  useEffect(() => {
    if (!missionId) return;
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let stopped = false;
    let attempt = 0;

    const connect = () => {
      if (stopped) return;
      setState(attempt > 0 ? "RECONNECTING" : "CONNECTING");
      socket = new WebSocket(websocketUrl(missionId));
      socket.onopen = () => { attempt = 0; setState("CONNECTED"); setError(null); };
      socket.onmessage = (event) => {
        try {
          applyEnvelope(JSON.parse(event.data) as RealtimeEnvelope);
        } catch {
          setError("REALTIME MESSAGE INVALID");
        }
      };
      socket.onerror = () => setError("WEBSOCKET CONNECTION FAILED");
      socket.onclose = () => {
        if (stopped) return;
        setState("DISCONNECTED");
        const delay = Math.min(8000, 500 * 2 ** attempt++);
        retryTimer = window.setTimeout(connect, delay);
      };
    };
    connect();
    const staleTimer = window.setInterval(() => refresh(), 1000);
    return () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      window.clearInterval(staleTimer);
      socket?.close();
    };
  }, [missionId, applyEnvelope, refresh, setError, setState]);
}
