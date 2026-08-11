import { Canvas } from "@react-three/fiber";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Scene } from "./Scene";
import { useTwinState, type ConnectionStatus } from "./useTwinState";

const STATUS_COLORS: Record<ConnectionStatus, string> = {
  connecting: "#e5c07b",
  live: "#98c379",
  reconnecting: "#e06c75",
};

// Degradation must be visible: the pill is the frontend's /healthz. It also
// reports the fleet size so an empty or shrinking fleet is obvious at a glance.
function StatusPill({ status, count }: { status: ConnectionStatus; count: number }) {
  return (
    <div
      style={{
        position: "fixed",
        top: 12,
        left: 12,
        padding: "4px 10px",
        borderRadius: 999,
        fontFamily: "ui-monospace, monospace",
        fontSize: 12,
        color: "#0b0e14",
        background: STATUS_COLORS[status],
        userSelect: "none",
      }}
    >
      {status} · {count} {count === 1 ? "robot" : "robots"}
    </div>
  );
}

function App() {
  const { frames, status } = useTwinState();
  const count = Object.keys(frames).length;
  return (
    <>
      <Canvas camera={{ position: [3.5, 2.6, 3.5], fov: 45 }}>
        <color attach="background" args={["#0b0e14"]} />
        <Scene frames={frames} />
      </Canvas>
      <StatusPill status={status} count={count} />
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
