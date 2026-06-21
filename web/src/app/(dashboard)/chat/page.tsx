"use client";

import React from "react";
import { MessageSquare } from "lucide-react";

export default function ChatPage() {
  return (
    <div className="flex h-[70vh] flex-col items-center justify-center text-center">
      <div className="p-4 bg-accent-indigo/10 rounded-full border border-accent-indigo/20 text-accent-indigo mb-4">
        <MessageSquare className="h-10 w-10 animate-bounce" />
      </div>
      <h2 className="text-xl font-bold text-foreground">Chat / RAG Query (Phase 2)</h2>
      <p className="text-sm text-foreground/50 max-w-sm mt-2">
        This view will allow querying the framework with customizable options (depth, threshold, RRF fusion).
      </p>
    </div>
  );
}
