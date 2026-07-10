"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toTier, tierColor } from "@/components/forensic/classification";
import type { AtlasPoint } from "./atlasPoint";

export type { AtlasPoint };

interface VectorAtlasPlotProps {
  points: AtlasPoint[];
  highlightedIds?: Set<string>;
  selectedId?: string | null;
  onSelect?: (point: AtlasPoint | null) => void;
  reducedMotion?: boolean;
}

const PLOT_PADDING = 32;
const POINT_RADIUS = 5;
const HIGHLIGHT_RADIUS = 7;

function resolveColor(classification: string, highlighted: boolean, selected: boolean): string {
  if (selected || highlighted) return "var(--signal-amber)";
  return tierColor(toTier(classification));
}

export default function VectorAtlasPlot({
  points,
  highlightedIds,
  selectedId,
  onSelect,
  reducedMotion = false,
}: VectorAtlasPlotProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 640, height: 420 });
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setSize({
        width: Math.max(320, entry.contentRect.width),
        height: Math.max(280, entry.contentRect.height),
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const layout = useMemo(() => {
    if (!points.length) {
      return { mapped: [] as Array<AtlasPoint & { px: number; py: number }>, bounds: null };
    }
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y ?? 0);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;
    const innerW = size.width - PLOT_PADDING * 2;
    const innerH = size.height - PLOT_PADDING * 2;
    const mapped = points.map((p) => ({
      ...p,
      px: PLOT_PADDING + ((p.x - minX) / spanX) * innerW,
      py: PLOT_PADDING + (((p.y ?? 0) - minY) / spanY) * innerH,
    }));
    return { mapped, bounds: { minX, maxX, minY, maxY } };
  }, [points, size.height, size.width]);

  const pickPoint = useCallback(
    (clientX: number, clientY: number) => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return null;
      const x = clientX - rect.left;
      const y = clientY - rect.top;
      let best: (AtlasPoint & { px: number; py: number }) | null = null;
      let bestDist = Infinity;
      for (const point of layout.mapped) {
        const dx = point.px - x;
        const dy = point.py - y;
        const dist = Math.hypot(dx, dy);
        if (dist < POINT_RADIUS * 3 && dist < bestDist) {
          best = point;
          bestDist = dist;
        }
      }
      return best;
    },
    [layout.mapped]
  );

  const handlePointer = (event: React.PointerEvent<SVGSVGElement>) => {
    const hit = pickPoint(event.clientX, event.clientY);
    setHoveredId(hit?.id ?? null);
    if (event.type === "click") {
      onSelect?.(hit ?? null);
    }
  };

  if (reducedMotion) {
    return (
      <div className="h-full overflow-auto custom-scrollbar border border-hairline">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-vault-raised border-b border-hairline">
            <tr className="mono-meta normal-case text-left">
              <th className="px-3 py-2">chunk</th>
              <th className="px-3 py-2">x</th>
              <th className="px-3 py-2">y</th>
              <th className="px-3 py-2">z</th>
              <th className="px-3 py-2">classification</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr
                key={p.id}
                className="border-b border-hairline/60 hover:bg-[rgba(232,230,223,0.03)] cursor-pointer"
                onClick={() => onSelect?.(p)}
              >
                <td className="px-3 py-2 font-mono text-bone truncate max-w-[14rem]">
                  {p.chunk_id}
                </td>
                <td className="px-3 py-2 font-mono text-bone-dim tabular-nums">{p.x.toFixed(3)}</td>
                <td className="px-3 py-2 font-mono text-bone-dim tabular-nums">
                  {(p.y ?? 0).toFixed(3)}
                </td>
                <td className="px-3 py-2 font-mono text-bone-dim tabular-nums">
                  {(p.z ?? 0).toFixed(3)}
                </td>
                <td className="px-3 py-2 text-bone-dim">{p.classification}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative h-full w-full bg-vault-sunk border border-hairline">
      <svg
        width={size.width}
        height={size.height}
        className="block"
        onPointerMove={handlePointer}
        onPointerLeave={() => setHoveredId(null)}
        onClick={handlePointer}
        role="img"
        aria-label="Vector atlas scatter plot"
      >
        <rect width={size.width} height={size.height} fill="transparent" />
        {layout.mapped.map((point) => {
          const highlighted = highlightedIds?.has(point.id) ?? false;
          const selected = selectedId === point.id;
          const hovered = hoveredId === point.id;
          const radius = selected || highlighted ? HIGHLIGHT_RADIUS : POINT_RADIUS;
          return (
            <circle
              key={point.id}
              cx={point.px}
              cy={point.py}
              r={hovered ? radius + 1.5 : radius}
              fill={resolveColor(point.classification, highlighted, selected)}
              opacity={selected || highlighted || hovered ? 1 : 0.82}
              stroke={selected ? "var(--bone)" : "transparent"}
              strokeWidth={selected ? 1 : 0}
            />
          );
        })}
      </svg>
      {hoveredId && (
        <div className="pointer-events-none absolute left-3 bottom-3 max-w-sm border border-hairline bg-vault-raised/95 px-3 py-2 text-xs text-bone">
          {layout.mapped.find((p) => p.id === hoveredId)?.text_preview}
        </div>
      )}
    </div>
  );
}
