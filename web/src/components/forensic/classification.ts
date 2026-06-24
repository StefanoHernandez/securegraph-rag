/**
 * Classification taxonomy for the Forensic Vault language.
 *
 * The backend uses a small set of labels; the UI collapses them into three
 * visual tiers, each tied to one of the two signal colours (or neutral bone).
 * Structure encodes the access state — no rainbow.
 */

export type ClassificationTier = "public" | "internal" | "restricted";

const TIER_OF: Record<string, ClassificationTier> = {
  public: "public",
  internal: "internal",
  confidential: "restricted",
  restricted: "restricted",
  secret: "restricted",
  top_secret: "restricted",
};

export function toTier(raw?: string | null): ClassificationTier {
  if (!raw) return "internal";
  return TIER_OF[raw.toLowerCase().replace(/[\s-]+/g, "_")] ?? "internal";
}

/** CSS colour var for a tier — used for stamp ink and classification gutters. */
export function tierColor(tier: ClassificationTier): string {
  switch (tier) {
    case "public":
      return "var(--bone-dim)";
    case "internal":
      return "var(--signal-amber)";
    case "restricted":
      return "var(--seal-red)";
  }
}

export function tierLabel(raw?: string | null): string {
  return (raw ?? "internal").toUpperCase().replace(/[\s-]+/g, "_");
}
