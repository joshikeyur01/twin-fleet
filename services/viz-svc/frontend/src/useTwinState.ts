// The WebSocket half of the viewer. Frame shape mirrors viz_svc/stream.py
// to_frame() — that Python function is the source of truth.

import { useEffect, useRef, useState } from "react";

export interface JointFrame {
  name: string;
  position_rad: number;
  velocity_rms: number;
}

export interface TwinFrame {
  robot_id: string;
  stamp_ms: number;
  ee: { pos: [number, number, number]; quat: [number, number, number, number] };
  joints: JointFrame[];
}

export type ConnectionStatus = "connecting" | "live" | "reconnecting";

const RECONNECT_DELAY_MS = 1500;

function wsUrl(): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/ws/state`;
}

// The fleet stream interleaves every robot's frames on one socket, keyed by
// robot_id. Messages arrive at (robots x 30 Hz), so we accumulate the latest
// frame per robot in a ref and flush to React state once per animation frame —
// render cost tracks the display, not the fleet size. A killed robot simply
// stops updating; its last pose lingers (the fleet registry and Grafana are
// the source of truth for liveness, not this thin viewer).
export function useTwinState(): {
  frames: Record<string, TwinFrame>;
  status: ConnectionStatus;
} {
  const [frames, setFrames] = useState<Record<string, TwinFrame>>({});
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const buffer = useRef<Record<string, TwinFrame>>({});
  const rafPending = useRef(false);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let disposed = false;

    const flush = () => {
      rafPending.current = false;
      setFrames({ ...buffer.current }); // new reference so React re-renders
    };

    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(wsUrl());
      socket.onopen = () => setStatus("live");
      socket.onmessage = (event) => {
        const frame = JSON.parse(event.data as string) as TwinFrame;
        buffer.current[frame.robot_id] = frame;
        if (!rafPending.current) {
          rafPending.current = true;
          requestAnimationFrame(flush);
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        setStatus("reconnecting");
        timer = window.setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, []);

  return { frames, status };
}
