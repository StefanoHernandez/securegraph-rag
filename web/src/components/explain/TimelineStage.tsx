import * as React from "react";
import { cn } from "@/lib/utils";

interface TimelineStageProps {
  /** 1-based stage number shown in the chain marker. */
  index: number;
  title: React.ReactNode;
  /** Right-aligned header content (verdict, counts, stamp). */
  headRight?: React.ReactNode;
  /** Marker is amber when the stage carries a positive signal. */
  sealed?: boolean;
  /** Hide the connector below the last stage. */
  isLast?: boolean;
  children?: React.ReactNode;
}

/**
 * One band of the custody timeline. A numbered marker sits on a vertical
 * hairline "chain" rail; the body is a matte panel. Structure encodes the
 * query's lifecycle order — the rail is the chain of custody.
 */
export function TimelineStage({
  index,
  title,
  headRight,
  sealed = false,
  isLast = false,
  children,
}: TimelineStageProps) {
  return (
    <li className="relative pl-12 pb-5 last:pb-0">
      {/* connector rail */}
      {!isLast && (
        <span
          aria-hidden
          className="absolute left-[15px] top-8 bottom-0 w-px bg-hairline"
        />
      )}
      {/* marker */}
      <span
        aria-hidden
        className={cn(
          "absolute left-0 top-0 flex h-8 w-8 items-center justify-center border font-mono text-xs",
          sealed
            ? "border-signal-amber text-signal-amber"
            : "border-hairline text-bone-dim"
        )}
      >
        {String(index).padStart(2, "0")}
      </span>

      <div className="panel">
        <header className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-2.5">
          <span className="mono-meta">{title}</span>
          {headRight}
        </header>
        {children}
      </div>
    </li>
  );
}
