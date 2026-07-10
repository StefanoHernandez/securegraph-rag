"use client";

import React, { useLayoutEffect, useMemo, useRef } from "react";
import { Canvas, type ThreeEvent } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { toTier } from "@/components/forensic/classification";
import type { AtlasPoint } from "./atlasPoint";

const TIER_HEX = {
  public: "#B8B6AE",
  internal: "#CFCDC6",
  restricted: "#C8503C",
} as const;

const HIGHLIGHT_HEX = "#E8B23A";
const SELECTED_HEX = "#E8E6DF";

interface LayoutItem {
  point: AtlasPoint;
  position: [number, number, number];
}

interface VectorScene3DProps {
  points: AtlasPoint[];
  highlightedIds?: Set<string>;
  selectedId?: string | null;
  onSelect?: (point: AtlasPoint | null) => void;
}

function tierHex(classification: string, highlighted: boolean, selected: boolean): string {
  if (selected) return SELECTED_HEX;
  if (highlighted) return HIGHLIGHT_HEX;
  return TIER_HEX[toTier(classification)];
}

function layout3D(points: AtlasPoint[]): LayoutItem[] {
  if (!points.length) return [];

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y ?? 0);
  const zs = points.map((p) => p.z ?? 0);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const minZ = Math.min(...zs);
  const maxZ = Math.max(...zs);
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const centerZ = (minZ + maxZ) / 2;
  const span = Math.max(maxX - minX, maxY - minY, maxZ - minZ, 1e-6);
  const scale = 18 / span;

  return points.map((point) => ({
    point,
    position: [
      (point.x - centerX) * scale,
      ((point.y ?? 0) - centerY) * scale,
      ((point.z ?? 0) - centerZ) * scale,
    ],
  }));
}

function InstancedPoints({
  layout,
  highlightedIds,
  selectedId,
  onSelect,
}: {
  layout: LayoutItem[];
  highlightedIds?: Set<string>;
  selectedId?: string | null;
  onSelect?: (point: AtlasPoint | null) => void;
}) {
  const meshRef = useRef<THREE.InstancedMesh>(null);

  useLayoutEffect(() => {
    const mesh = meshRef.current;
    if (!mesh || !layout.length) return;

    const temp = new THREE.Object3D();
    const color = new THREE.Color();
    layout.forEach((item, index) => {
      const highlighted = highlightedIds?.has(item.point.id) ?? false;
      const selected = selectedId === item.point.id;
      const radius = selected || highlighted ? 0.22 : 0.15;
      const [x, y, z] = item.position;
      temp.position.set(x, y, z);
      temp.scale.setScalar(radius / 0.15);
      temp.updateMatrix();
      mesh.setMatrixAt(index, temp.matrix);
      color.set(tierHex(item.point.classification, highlighted, selected));
      mesh.setColorAt(index, color);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [layout, highlightedIds, selectedId]);

  const handleClick = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    const index = event.instanceId;
    if (index == null) return;
    onSelect?.(layout[index]?.point ?? null);
  };

  if (!layout.length) return null;

  return (
    <instancedMesh
      ref={meshRef}
      args={[undefined, undefined, layout.length]}
      onClick={handleClick}
    >
      <sphereGeometry args={[0.15, 14, 14]} />
      <meshStandardMaterial vertexColors />
    </instancedMesh>
  );
}

export default function VectorScene3D({
  points,
  highlightedIds,
  selectedId,
  onSelect,
}: VectorScene3DProps) {
  const layout = useMemo(() => layout3D(points), [points]);

  return (
    <div className="h-full w-full bg-vault-sunk">
      <Canvas camera={{ position: [0, 0, 28], fov: 50 }} dpr={[1, 1.5]}>
        <color attach="background" args={["#121210"]} />
        <ambientLight intensity={0.65} />
        <directionalLight position={[10, 14, 8]} intensity={0.85} />
        <InstancedPoints
          layout={layout}
          highlightedIds={highlightedIds}
          selectedId={selectedId}
          onSelect={onSelect}
        />
        <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
      </Canvas>
    </div>
  );
}
