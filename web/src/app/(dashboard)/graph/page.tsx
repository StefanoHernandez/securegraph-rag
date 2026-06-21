"use client";

import React from "react";
import { Network } from "lucide-react";

export default function GraphPage() {
  return (
    <div className="flex h-[70vh] flex-col items-center justify-center text-center">
      <div className="p-4 bg-accent-indigo/10 rounded-full border border-accent-indigo/20 text-accent-indigo mb-4">
        <Network className="h-10 w-10 animate-bounce" />
      </div>
      <h2 className="text-xl font-bold text-foreground">Knowledge Graph Explorer (Phase 4)</h2>
      <p className="text-sm text-foreground/50 max-w-sm mt-2">
        This view will render an interactive 3D force-directed node-link diagram of the Neo4j Graph Database.
      </p>
    </div>
  );
}
