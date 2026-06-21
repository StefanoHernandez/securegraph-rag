"use client";

import React from "react";
import { HelpCircle } from "lucide-react";

export default function ExplainPage() {
  return (
    <div className="flex h-[70vh] flex-col items-center justify-center text-center">
      <div className="p-4 bg-accent-indigo/10 rounded-full border border-accent-indigo/20 text-accent-indigo mb-4">
        <HelpCircle className="h-10 w-10 animate-pulse" />
      </div>
      <h2 className="text-xl font-bold text-foreground">Provenance & Explainability (Phase 3)</h2>
      <p className="text-sm text-foreground/50 max-w-sm mt-2">
        This view will render interactive W3C PROV-O query lineages using React Flow and redact unauthorized nodes.
      </p>
    </div>
  );
}
