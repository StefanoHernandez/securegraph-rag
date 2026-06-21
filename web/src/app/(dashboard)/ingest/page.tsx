"use client";

import React from "react";
import { UploadCloud } from "lucide-react";

export default function IngestPage() {
  return (
    <div className="flex h-[70vh] flex-col items-center justify-center text-center">
      <div className="p-4 bg-accent-indigo/10 rounded-full border border-accent-indigo/20 text-accent-indigo mb-4">
        <UploadCloud className="h-10 w-10 animate-pulse" />
      </div>
      <h2 className="text-xl font-bold text-foreground">Document Ingestion (Phase 2)</h2>
      <p className="text-sm text-foreground/50 max-w-sm mt-2">
        This view will allow drag-and-drop file upload with custom metadata classification tags (e.g. secret, top-secret).
      </p>
    </div>
  );
}
