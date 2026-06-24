import * as React from "react";
import { cn } from "@/lib/utils";

interface TrustVerdictProps extends React.ComponentProps<"span"> {
  highTrust: boolean;
  /** Optional agreement score 0–1 appended in mono. */
  score?: number;
}

/**
 * Consensus verdict stamp. High trust = amber sealed; standard = bone neutral.
 * Amber appears only when the answer is actually verified — never decoratively.
 */
export function TrustVerdict({
  highTrust,
  score,
  className,
  ...props
}: TrustVerdictProps) {
  return (
    <span
      data-slot="trust-verdict"
      className={cn(
        "stamp",
        highTrust ? "text-signal-amber" : "text-bone-dim",
        className
      )}
      {...props}
    >
      {highTrust ? "✓ HIGH TRUST" : "STANDARD CONSENSUS"}
      {typeof score === "number" && (
        <span className="opacity-70">· {(score * 100).toFixed(0)}%</span>
      )}
    </span>
  );
}
