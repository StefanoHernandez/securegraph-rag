import React from "react";

/**
 * Forensic Vault backdrop. A static drafting grid etched into the graphite
 * ground with a soft vignette — the surface of an evidence table, not a
 * particle field. No canvas, no animation, no GPU. Honours reduced-motion
 * trivially (there is nothing to reduce).
 */
export default function VaultBackground() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 -z-50 bg-vault"
      style={{
        backgroundImage: `
          linear-gradient(to right, var(--hairline) 1px, transparent 1px),
          linear-gradient(to bottom, var(--hairline) 1px, transparent 1px),
          radial-gradient( circle at 50% 0%, rgba(232, 178, 58, 0.04), transparent 60%)
        `,
        backgroundSize: "48px 48px, 48px 48px, 100% 100%",
        maskImage:
          "radial-gradient(ellipse 120% 90% at 50% 40%, black 55%, transparent 100%)",
        WebkitMaskImage:
          "radial-gradient(ellipse 120% 90% at 50% 40%, black 55%, transparent 100%)",
      }}
    />
  );
}
