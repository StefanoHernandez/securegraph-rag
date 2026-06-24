"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface RedactionBarProps {
  /** When false, content is redacted (ink bar). When true, it declassifies. */
  authorized: boolean;
  children: React.ReactNode;
  /** Reason shown on the redaction tag, e.g. "policy:deny". */
  reason?: string;
  className?: string;
}

/**
 * The signature mechanic. Unauthorized content is a solid ink bar carrying a
 * mono REDACTED tag — the ReBAC fail-closed decision made visible. Authorized
 * content reveals with a one-shot declassify wipe (disabled under
 * prefers-reduced-motion via the .declassify guard in globals.css).
 */
export function RedactionBar({
  authorized,
  children,
  reason = "policy:deny",
  className,
}: RedactionBarProps) {
  if (authorized) {
    return <span className={cn("declassify", className)}>{children}</span>;
  }

  // Size the bar to the text it hides without exposing it.
  const text = typeof children === "string" ? children : "redacted evidence";
  return (
    <span
      className={cn(
        "redaction-bar align-middle px-1 py-0.5 text-transparent",
        className
      )}
      title={`REDACTED · ${reason}`}
      aria-label={`Redacted: ${reason}`}
    >
      <span aria-hidden>{text}</span>
      <span className="ml-2 align-middle font-mono text-[0.55rem] tracking-widest text-seal-red/80 select-none">
        ▮ REDACTED
      </span>
    </span>
  );
}
