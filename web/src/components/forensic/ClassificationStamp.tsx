import * as React from "react";
import { cn } from "@/lib/utils";
import { toTier, tierColor, tierLabel } from "./classification";

interface ClassificationStampProps extends React.ComponentProps<"span"> {
  level?: string | null;
}

/**
 * Monospace bordered stamp (PUBLIC / INTERNAL / RESTRICTED). The ink colour is
 * the tier's signal colour. Used in panel gutters, the top bar (session
 * clearance), and document rows.
 */
export function ClassificationStamp({
  level,
  className,
  ...props
}: ClassificationStampProps) {
  const tier = toTier(level);
  return (
    <span
      data-slot="classification-stamp"
      className={cn("stamp", className)}
      style={{ color: tierColor(tier) }}
      {...props}
    >
      {tier === "restricted" ? "▣" : tier === "internal" ? "◆" : "○"}{" "}
      {tierLabel(level)}
    </span>
  );
}
