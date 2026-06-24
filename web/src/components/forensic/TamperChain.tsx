"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export type ChainStatus = "unverified" | "intact" | "tampered";

interface TamperChainProps {
  /** Number of links to draw (capped for layout). */
  count: number;
  status: ChainStatus;
  /** Index of the first tampered link (only when status === "tampered"). */
  brokenAt?: number;
  /** Animate links sealing one-by-one (used on verify action). */
  animate?: boolean;
  size?: "sm" | "lg";
  className?: string;
}

/**
 * Chain-of-custody visualizer. The HMAC audit chain drawn as physical links
 * ●─●─●─●. Verified-intact links seal amber; a tampered link breaks in
 * seal-red and everything after it goes cold. The verify action lights links
 * left-to-right (disabled under prefers-reduced-motion).
 */
export function TamperChain({
  count,
  status,
  brokenAt,
  animate = false,
  size = "sm",
  className,
}: TamperChainProps) {
  const max = size === "lg" ? 24 : 14;
  const n = Math.min(Math.max(count, 1), max);
  const dot = size === "lg" ? "h-3 w-3" : "h-2 w-2";
  const conn = size === "lg" ? "w-4" : "w-2.5";

  return (
    <div
      className={cn("flex items-center", className)}
      role="img"
      aria-label={
        status === "intact"
          ? `Chain intact, ${count} links`
          : status === "tampered"
          ? `Chain broken at link ${(brokenAt ?? 0) + 1}`
          : "Chain not yet verified"
      }
    >
      {Array.from({ length: n }).map((_, i) => {
        const broken =
          status === "tampered" && brokenAt !== undefined && i >= brokenAt;
        const isBreakPoint =
          status === "tampered" && brokenAt !== undefined && i === brokenAt;
        const sealed = status === "intact" && !broken;

        const color = broken
          ? "var(--seal-red)"
          : sealed
          ? "var(--signal-amber)"
          : "var(--bone-dim)";

        return (
          <React.Fragment key={i}>
            {i > 0 && (
              <span
                className={cn("h-px", conn, isBreakPoint && "opacity-40")}
                style={{
                  background: broken ? "var(--seal-red)" : "var(--hairline)",
                  borderTop: isBreakPoint
                    ? "1px dashed var(--seal-red)"
                    : undefined,
                  backgroundColor: isBreakPoint ? "transparent" : undefined,
                }}
              />
            )}
            <span
              className={cn(
                "rounded-full ring-1 ring-inset",
                dot,
                animate && status !== "unverified" && "chain-link-sealing"
              )}
              style={{
                background: status === "unverified" ? "transparent" : color,
                ["--tw-ring-color" as string]: color,
                animationDelay: animate ? `${i * 60}ms` : undefined,
              }}
            />
          </React.Fragment>
        );
      })}
    </div>
  );
}
