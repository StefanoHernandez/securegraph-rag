import * as React from "react";
import { cn } from "@/lib/utils";

interface EvidenceTagProps extends React.ComponentProps<"span"> {
  /** Channel marker letter, e.g. E (evidence), V (vector), G (graph). */
  marker?: string;
  index: number | string;
  redacted?: boolean;
}

/**
 * Forensic citation tag — [E-01]. Renders denied evidence in seal-red, with a
 * lock glyph; authorized in bone. Used for citations and evidence rows.
 */
export function EvidenceTag({
  marker = "E",
  index,
  redacted = false,
  className,
  ...props
}: EvidenceTagProps) {
  const id =
    typeof index === "number" ? String(index).padStart(2, "0") : index;
  return (
    <span
      data-slot="evidence-tag"
      className={cn(
        "inline-flex items-center gap-1 border border-hairline px-1.5 py-0.5 font-mono text-[0.6875rem] tracking-wider",
        redacted ? "text-seal-red border-seal-red/40" : "text-bone",
        className
      )}
      {...props}
    >
      {redacted && <span aria-hidden>▮</span>}[{marker}-{id}]
    </span>
  );
}
