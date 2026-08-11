// The fleet scene: one thin arm per robot, laid out on a grid, camera orbit
// (AGENTS.md — instances of the same thin scene, no per-robot controls). Each
// arm is a stylized primitive chain, not a mesh model: each nesting level
// applies one DH row (Rot_z(θ) · Trans_z(d) · Trans_x(a) · Rot_x(α)), driven by
// streamed joint angles.
//
// The orange marker is NOT attached to the arm: it renders the end-effector
// pose streamed from state-svc's forward kinematics. If a marker ever detaches
// from its arm tip, that robot's client chain and the server FK disagree — the
// display audits itself, per robot.

import { OrbitControls } from "@react-three/drei";
import { useMemo } from "react";
import * as THREE from "three";
import type { TwinFrame } from "./useTwinState";

// Mirrors state_svc/kinematics.py — the published UR5 DH parameters.
const DH = [
  { a: 0, d: 0.089159, alpha: Math.PI / 2 },
  { a: -0.425, d: 0, alpha: 0 },
  { a: -0.39225, d: 0, alpha: 0 },
  { a: 0, d: 0.10915, alpha: Math.PI / 2 },
  { a: 0, d: 0.09465, alpha: -Math.PI / 2 },
  { a: 0, d: 0.0823, alpha: 0 },
] as const;

const GRID_SPACING = 1.3; // metres between adjacent robots

const UP = new THREE.Vector3(0, 1, 0);

function Limb({ to }: { to: readonly [number, number, number] }) {
  const { quat, length, mid } = useMemo(() => {
    const v = new THREE.Vector3(to[0], to[1], to[2]);
    const length = v.length();
    const quat =
      length > 1e-6
        ? new THREE.Quaternion().setFromUnitVectors(UP, v.clone().normalize())
        : new THREE.Quaternion();
    return { quat, length, mid: v.multiplyScalar(0.5) };
  }, [to]);
  if (length < 1e-6) return null;
  return (
    <mesh position={mid} quaternion={quat}>
      <cylinderGeometry args={[0.028, 0.028, length, 16]} />
      <meshStandardMaterial color="#8fa3bf" />
    </mesh>
  );
}

function JointBall() {
  return (
    <mesh>
      <sphereGeometry args={[0.045, 24, 24]} />
      <meshStandardMaterial color="#4a6fa5" />
    </mesh>
  );
}

function ArmChain({ angles, index = 0 }: { angles: number[]; index?: number }) {
  if (index === DH.length) {
    return (
      <mesh>
        <boxGeometry args={[0.05, 0.05, 0.05]} />
        <meshStandardMaterial color="#d8dee9" />
      </mesh>
    );
  }
  const row = DH[index];
  const link = [row.a, 0, row.d] as const;
  return (
    <group rotation={[0, 0, angles[index] ?? 0]}>
      <JointBall />
      <Limb to={link} />
      <group position={[row.a, 0, row.d]} rotation={[row.alpha, 0, 0]}>
        <ArmChain angles={angles} index={index + 1} />
      </group>
    </group>
  );
}

function EEMarker({ pos }: { pos: [number, number, number] }) {
  return (
    <mesh position={pos}>
      <sphereGeometry args={[0.022, 24, 24]} />
      <meshStandardMaterial color="#ff6b35" emissive="#ff6b35" emissiveIntensity={0.6} />
    </mesh>
  );
}

// One robot's arm + end-effector marker, in its own Z-up frame.
function Robot({ frame }: { frame: TwinFrame }) {
  const angles = frame.joints.map((j) => j.position_rad);
  return (
    // Robot frames are Z-up; the display is Y-up. One rotation fixes each.
    <group rotation={[-Math.PI / 2, 0, 0]}>
      <ArmChain angles={angles} />
      <EEMarker pos={frame.ee.pos} />
    </group>
  );
}

// Grid position for robot `i` of `count`, centred on the origin.
function gridPosition(i: number, count: number): [number, number, number] {
  const cols = Math.max(1, Math.ceil(Math.sqrt(count)));
  const rows = Math.ceil(count / cols);
  const col = i % cols;
  const row = Math.floor(i / cols);
  const x = (col - (cols - 1) / 2) * GRID_SPACING;
  const z = (row - (rows - 1) / 2) * GRID_SPACING;
  return [x, 0, z];
}

export function Scene({ frames }: { frames: Record<string, TwinFrame> }) {
  const ids = Object.keys(frames).sort(); // stable layout as robots come and go
  const gridSize = Math.max(4, Math.ceil(Math.sqrt(ids.length)) * GRID_SPACING + GRID_SPACING);
  return (
    <>
      <ambientLight intensity={0.5} />
      <directionalLight position={[3, 5, 2]} intensity={1.2} />
      <gridHelper args={[gridSize, Math.round(gridSize * 5), "#2c3242", "#1a1f2b"]} />
      {ids.map((id, i) => (
        <group key={id} position={gridPosition(i, ids.length)}>
          <Robot frame={frames[id]} />
        </group>
      ))}
      <OrbitControls makeDefault target={[0, 0.3, 0]} />
    </>
  );
}
