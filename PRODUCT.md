# Product

## Register

product

## Users

Forensic investigators, security analysts, compliance officers, and tenant
administrators in regulated environments (legal, finance, government labs).
They work in evidence-grade workflows where every answer must be traceable
to authorized sources, and every access decision must be auditable. Primary
context: a desktop workstation in a controlled-access office, ambient light
is mixed fluorescent + monitor glow, mood is focused and skeptical — they
do not trust answers that cannot be verified. The job is not "find
information" but "build a defensible case from authorized evidence without
leaking across tenants, groups, or classification levels."

## Product Purpose

SecureGraph is a hybrid vector-graph RAG framework with verifiable access
control and cross-retriever consensus. It exists to prove that every chunk,
node, and edge used to derive an answer was authorized for the requester at
query time (C1), that vector and graph retrievers cross-verify their outputs
(C2), and that community detection never leaks across policy-equivalence
classes (C3). Success looks like: an auditor can re-derive the authorization
chain for any answer, the tamper-evident ledger verifies intact, and a
non-owner receives a hedge instead of a forbidden fact — every time.

## Brand Personality

Forensic. Authoritative. Restrained.

The interface should feel like a secure evidence vault, not a SaaS dashboard.
Voice is direct and clinical — no marketing flourish, no enthusiasm, no
"great question." Confidence comes from verifiable structure, not from
visual emphasis. The emotional goal is quiet trust: the user knows the
system is enforcing access control because they can see the provenance
chain, not because the UI tells them it is secure.

## Anti-references

- Not a SaaS analytics dashboard (no gradient hero metrics, no "powered by AI"
  badges, no playful illustrations).
- Not a consumer chat app (no bubbles, no avatars, no typing indicators as
  decoration).
- Not a glassmorphism showcase (no frosted cards, no aurora backgrounds, no
  decorative blur).
- Not a "dark mode for coolness" terminal cosplay — the dark theme is
  evidence-grade, not aesthetic.
- Not Linear/Notion/Vercel pastiche — the Forensic Vault has its own
  visual language, not a borrowed one.

## Design Principles

1. **Show the chain, not the claim.** Every answer surfaces its provenance
   trace; integrity states come from the backend, never inferred client-side.
2. **Restraint is authority.** Two semantic signals (amber = verified,
   seal-red = denied/tampered). No rainbow accents. No decorative motion.
3. **Hairline over shadow.** Document-grade square corners, hairline rules,
   matte surfaces. The vault is built, not floated.
4. **Fail closed visibly.** Denied access produces an explicit hedge, not a
   blank state. Tampered chains show seal-red, not a generic error.
5. **Density without clutter.** Investigators scan many records; the layout
   rewards scanning with mono-spaced metadata, tight rhythm, and clear
   hierarchy — not whitespace for its own sake.

## Accessibility & Inclusion

- WCAG 2.1 AA target. Body text contrast ≥ 4.5:1 against the matte graphite
  ground; large text and semantic signals (amber, seal-red) ≥ 3:1.
- `prefers-reduced-motion: reduce` disables Three.js particle backgrounds and
  all non-essential transitions; crossfades or instant state changes replace
  them.
- No information conveyed by color alone — every semantic signal has a text
  label or icon (amber + "VERIFIED", seal-red + "TAMPERED").
- Keyboard navigation reaches all admin and query surfaces; focus rings use
  the hairline token, not a generic browser outline.
- i18n: `/it` and `/en` locale routes via `next-intl`; nested presentation
  components must be localized or documented as language-neutral.
