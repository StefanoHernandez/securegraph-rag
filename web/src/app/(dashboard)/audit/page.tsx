"use client";

import React from "react";
import { FileCode } from "lucide-react";

export default function AuditPage() {
  return (
    <div className="flex h-[70vh] flex-col items-center justify-center text-center">
      <div className="p-4 bg-accent-indigo/10 rounded-full border border-accent-indigo/20 text-accent-indigo mb-4">
        <FileCode className="h-10 w-10 animate-pulse" />
      </div>
      <h2 className="text-xl font-bold text-foreground">Audit Ledger (Phase 3)</h2>
      <p className="text-sm text-foreground/50 max-w-sm mt-2">
        This view will show query history traces and allow administrators to verify table integrity using HMAC-SHA256 hash chains.
      </p>
    </div>
  );
}
