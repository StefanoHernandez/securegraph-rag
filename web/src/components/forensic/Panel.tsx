import * as React from "react";
import { cn } from "@/lib/utils";
import { toTier, tierColor, type ClassificationTier } from "./classification";

interface PanelProps extends React.ComponentProps<"section"> {
  /** Optional classification — renders the left security gutter. */
  classification?: string | ClassificationTier;
  /** Eyebrow label rendered in mono at the panel head. */
  label?: React.ReactNode;
  /** Right-aligned head content (status, actions). */
  headRight?: React.ReactNode;
}

/**
 * Matte evidence panel. Replaces the glassmorphism Card. Hairline rule, no
 * blur, no glow. When given a classification it grows a coloured security
 * gutter on its left edge so the access state is structural, not decorative.
 */
export function Panel({
  className,
  classification,
  label,
  headRight,
  children,
  style,
  ...props
}: PanelProps) {
  const gutter = classification
    ? { ["--gutter-color" as string]: tierColor(toTier(String(classification))) }
    : undefined;

  return (
    <section
      data-slot="panel"
      className={cn(
        "panel",
        classification && "classification-gutter",
        className
      )}
      style={{ ...gutter, ...style }}
      {...props}
    >
      {(label || headRight) && (
        <header className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-2.5">
          {label ? <span className="mono-meta">{label}</span> : <span />}
          {headRight}
        </header>
      )}
      {children}
    </section>
  );
}
