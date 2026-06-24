"use client";

import React, { useState, useEffect, useRef } from "react";
import { Send, Trash2, ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/forensic";
import { queryApi } from "@/lib/api";
import { QueryRequest, QueryResponse, RetrievalMode, QueryType } from "@/lib/types";
import AnswerCard from "@/components/chat/AnswerCard";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: QueryResponse;
  timestamp: Date;
}

const SUGGESTED_PROMPTS = [
  "Quali sono le policy di sicurezza per l'accesso ai dati sensibili?",
  "Retrieve the consensus report for the latest high-trust query.",
  "Elenco degli accessi negati per il tenant_a oggi.",
  "Show relationship between OpenFGA policies and Neo4j nodes."
];

const CLASSIFICATION_LEVELS = [
  { value: "", label: "Inherit User Level" },
  { value: "public", label: "Public" },
  { value: "internal", label: "Internal" },
  { value: "confidential", label: "Confidential" },
  { value: "secret", label: "Secret" },
  { value: "top_secret", label: "Top Secret" }
];

const PIPELINE_STEPS = [
  {
    label: "Query Classification & Scope Routing",
    detail: "Intent parsing…",
  },
  {
    label: "Dual-Lane Retrieval & FGA Pruning",
    detail: "vector · lexical · neo4j graph",
  },
  {
    label: "Cross-Lane Consensus Audit (NLI Judge)",
    detail: "Bidirectional NLI consensus…",
  },
  {
    label: "Secure Synthesis & Lineage Anchoring",
    detail: "Hashing and persisting trace…",
  },
];

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState(0); // 0: routing, 1: retrieval, 2: consensus, 3: synthesis
  const [user, setUser] = useState<any>(null);
  const [tenantId, setTenantId] = useState("default");
  const [userId, setUserId] = useState("admin");

  // Query Engine Options
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("auto");
  const [queryType, setQueryType] = useState<QueryType>("standard");
  const [maxGraphDepth, setMaxGraphDepth] = useState(2);
  const [topK, setTopK] = useState(10);
  const [consensusThreshold, setConsensusThreshold] = useState(0.7);

  // Scope settings
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [scopeMaxClassification, setScopeMaxClassification] = useState<string>("");
  const [scopeCollections, setScopeCollections] = useState("");
  const [scopeDocumentIds, setScopeDocumentIds] = useState("");

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Retrieve logged in user and tenant from localStorage
    const userStr = localStorage.getItem("securgraph_user");
    const storedTenant = localStorage.getItem("securgraph_tenant");
    if (userStr) {
      try {
        const parsed = JSON.parse(userStr);
        setUser(parsed);
        setUserId(parsed.id || parsed.username || "admin");
        setTenantId(storedTenant || parsed.tenant_id || "default");
      } catch (e) {
        console.error("Error parsing user from localStorage", e);
      }
    }
  }, []);

  useEffect(() => {
    // Scroll to bottom when messages list changes
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // Loading animation step simulator
  useEffect(() => {
    let timer: NodeJS.Timeout;
    if (loading) {
      setLoadingStep(0);
      const runTimeline = () => {
        timer = setTimeout(() => {
          setLoadingStep((prev) => {
            if (prev < 3) {
              return prev + 1;
            }
            return prev;
          });
          runTimeline();
        }, 1200);
      };
      runTimeline();
    }
    return () => clearTimeout(timer);
  }, [loading]);

  const handleSend = async (textToSend: string) => {
    if (!textToSend.trim() || loading) return;

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: textToSend,
      timestamp: new Date()
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);
    setLoadingStep(0);

    // Build the options dictionary
    const queryRequest: QueryRequest = {
      tenant_id: tenantId,
      user_id: userId,
      query: textToSend,
      options: {
        include_explanation: true, // always retrieve for the UI breakdown
        retrieval_mode: retrievalMode,
        query_type: queryType,
        max_graph_depth: maxGraphDepth,
        top_k: topK,
        consensus_threshold: consensusThreshold
      }
    };

    // Apply scopes if set
    if (scopeMaxClassification) {
      queryRequest.scope_max_classification = scopeMaxClassification;
    }
    if (scopeCollections.trim()) {
      queryRequest.scope_collections = scopeCollections.split(",").map(c => c.trim()).filter(Boolean);
    }
    if (scopeDocumentIds.trim()) {
      queryRequest.scope_document_ids = scopeDocumentIds.split(",").map(id => id.trim()).filter(Boolean);
    }

    try {
      const apiResponse = await queryApi.submitQuery(queryRequest);

      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: apiResponse.answer,
        response: apiResponse,
        timestamp: new Date()
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error: any) {
      const errorMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `**Errore di recupero:** Non è stato possibile completare la query. \n\nDettaglio errore: \`${error?.message || error || "Unknown Error"}\`. \n\nAssicurati che i container del backend e dei database (Postgres, Neo4j, Qdrant) siano attivi.`,
        timestamp: new Date()
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setLoading(false);
    }
  };

  const clearChat = () => {
    setMessages([]);
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">section 01 · query console</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            Query Console
          </h1>
        </div>
        <p className="mono-meta normal-case">
          tenant <span className="text-signal-amber">{tenantId}</span> · operator{" "}
          <span className="text-signal-amber">{userId}</span>
        </p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 h-[calc(100vh-220px)]">

        {/* Left Area: Chat messages and Input (3 cols) */}
        <div className="lg:col-span-3 flex flex-col h-full panel overflow-hidden">

          {/* Console header */}
          <div className="px-4 py-2.5 border-b border-hairline flex items-center justify-between">
            <span className="mono-meta">query engine · live session</span>
            {messages.length > 0 && (
              <Button
                variant="ghost"
                size="icon"
                onClick={clearChat}
                className="h-8 w-8 text-bone-dim hover:text-seal-red"
                title="Clear conversation"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
          </div>

          {/* Message List */}
          <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 custom-scrollbar">
            {messages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center max-w-lg mx-auto text-center space-y-6 py-8">
                <div className="space-y-2">
                  <p className="mono-meta">no findings on record</p>
                  <h3 className="font-display text-lg font-bold text-bone">
                    Open a protected query session
                  </h3>
                  <p className="text-xs text-bone-dim leading-relaxed">
                    Interrogate the hybrid Knowledge Graph with security built in.
                    Every query is pruned by the OpenFGA ReBAC engine and validated
                    through an NLI consensus pipeline before a finding is
                    declassified.
                  </p>
                </div>
                <div className="w-full grid grid-cols-1 gap-2 pt-4">
                  {SUGGESTED_PROMPTS.map((prompt, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleSend(prompt)}
                      className="text-left font-mono text-xs px-3.5 py-3 bg-vault-raised border border-hairline text-bone-dim hover:text-bone hover:border-signal-amber transition-colors"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                {messages.map((msg) => (
                  <div
                    key={msg.id}
                    className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}
                  >
                    <div className="mono-meta normal-case mb-1.5">
                      {msg.role === "user" ? "operator" : "engine"} ·{" "}
                      {msg.timestamp.toLocaleTimeString()}
                    </div>
                    {msg.role === "user" ? (
                      <div className="max-w-[85%] px-4 py-3 bg-vault-raised border border-hairline text-bone text-sm">
                        {msg.content}
                      </div>
                    ) : (
                      <div className="w-full max-w-[95%]">
                        {msg.response ? (
                          <AnswerCard response={msg.response} />
                        ) : (
                          // Markdown-only response for errors/simple texts
                          <div
                            className="classification-gutter panel px-4 py-3 prose prose-invert prose-sm max-w-none text-bone/90 leading-relaxed font-sans"
                            style={{ ["--gutter-color" as string]: "var(--seal-red)" }}
                          >
                            {msg.content}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Forensic processing sequence */}
            {loading && (
              <div className="flex flex-col items-start space-y-2">
                <span className="mono-meta normal-case">engine · processing</span>
                <div className="w-full max-w-md panel px-5 py-4">
                  <div className="flex items-center gap-2 pb-3 border-b border-hairline">
                    <Loader2 className="h-3.5 w-3.5 text-signal-amber animate-spin" />
                    <span className="mono-meta">langgraph workflow running</span>
                  </div>

                  {/* Pipeline visualizer */}
                  <ol className="pt-4 space-y-0">
                    {PIPELINE_STEPS.map((step, idx) => {
                      const isActive = loadingStep === idx;
                      const isDone = loadingStep > idx;
                      const isLast = idx === PIPELINE_STEPS.length - 1;
                      const marker = isDone
                        ? "✓"
                        : isActive
                        ? isLast
                          ? "⌛"
                          : "▸"
                        : String(idx + 1);
                      const markerColor = isActive
                        ? "text-signal-amber border-signal-amber"
                        : isDone
                        ? "text-bone border-hairline"
                        : "text-bone-dim border-hairline";
                      const labelColor = isActive
                        ? "text-signal-amber"
                        : isDone
                        ? "text-bone"
                        : "text-bone-dim";
                      return (
                        <li key={idx} className="relative flex gap-3 pb-4 last:pb-0">
                          {/* connector rule */}
                          {!isLast && (
                            <span className="absolute left-[0.5625rem] top-5 bottom-0 w-px bg-hairline" />
                          )}
                          <span
                            className={`relative z-10 flex-shrink-0 h-[1.125rem] w-[1.125rem] flex items-center justify-center bg-vault-raised border font-mono text-[0.625rem] ${markerColor}`}
                          >
                            {marker}
                          </span>
                          <div className="flex-1 -mt-0.5">
                            <p className={`mono-meta normal-case ${labelColor}`}>
                              {step.label}
                            </p>
                            {isActive && (
                              <p className="mt-1 font-mono text-[0.625rem] text-bone-dim animate-pulse">
                                {step.detail}
                              </p>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input Form */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend(input);
            }}
            className="p-4 border-t border-hairline bg-vault-raised flex items-center gap-3"
          >
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Interrogate the framework (e.g. security access policies…)"
              className="flex-1 bg-vault-sunk border-hairline text-bone text-sm focus-visible:border-signal-amber h-11"
              disabled={loading}
              autoComplete="off"
            />
            <Button
              type="submit"
              disabled={loading || !input.trim()}
              className="px-5 h-11"
            >
              <Send className="h-4 w-4" />
            </Button>
          </form>
        </div>

        {/* Right Area: Query Engine Settings Sidebar (1 col) */}
        <div className="flex flex-col gap-6 h-full overflow-y-auto custom-scrollbar">

          {/* Configuration Panel */}
          <Panel label="engine config" className="flex-shrink-0">
            <div className="px-4 py-4 space-y-4 text-xs">

              {/* Retrieval Mode */}
              <div className="space-y-1.5">
                <label className="mono-meta block">retrieval lane mode</label>
                <select
                  value={retrievalMode}
                  onChange={(e) => setRetrievalMode(e.target.value as RetrievalMode)}
                  className="w-full bg-vault-raised border border-hairline px-2.5 py-1.5 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                >
                  <option value="auto">Auto Router (Intent-based)</option>
                  <option value="vector">Vector Only (Semantic)</option>
                  <option value="lexical">Lexical Only (Keywords)</option>
                  <option value="graph">Graph Only (Relational)</option>
                  <option value="hybrid">Hybrid Lane Fusion</option>
                </select>
              </div>

              {/* Query Type */}
              <div className="space-y-1.5">
                <label className="mono-meta block">query strategy</label>
                <select
                  value={queryType}
                  onChange={(e) => setQueryType(e.target.value as QueryType)}
                  className="w-full bg-vault-raised border border-hairline px-2.5 py-1.5 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                >
                  <option value="standard">Standard RAG (Entity focused)</option>
                  <option value="global">Global Graph Summary</option>
                </select>
              </div>

              {/* Max Graph Depth */}
              <div className="space-y-1.5">
                <div className="flex justify-between items-center">
                  <label className="mono-meta block">max graph depth</label>
                  <span className="font-mono text-signal-amber">{maxGraphDepth} hops</span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="4"
                  step="1"
                  value={maxGraphDepth}
                  onChange={(e) => setMaxGraphDepth(Number(e.target.value))}
                  className="w-full h-1.5 bg-vault-sunk appearance-none cursor-pointer accent-signal-amber"
                />
              </div>

              {/* Consensus Threshold */}
              <div className="space-y-1.5">
                <div className="flex justify-between items-center">
                  <label className="mono-meta block">consensus threshold</label>
                  <span className="font-mono text-signal-amber">{(consensusThreshold * 100).toFixed(0)}%</span>
                </div>
                <input
                  type="range"
                  min="0.0"
                  max="1.0"
                  step="0.05"
                  value={consensusThreshold}
                  onChange={(e) => setConsensusThreshold(Number(e.target.value))}
                  className="w-full h-1.5 bg-vault-sunk appearance-none cursor-pointer accent-signal-amber"
                />
                <p className="text-[0.625rem] text-bone-dim leading-tight">
                  Agreement score limit required to trigger the "High Trust" verdict.
                </p>
              </div>

              {/* Top K */}
              <div className="space-y-1.5">
                <label className="mono-meta block">top k context chunks</label>
                <Input
                  type="number"
                  min={1}
                  max={100}
                  value={topK}
                  onChange={(e) => setTopK(Math.max(1, Math.min(100, Number(e.target.value) || 10)))}
                  className="bg-vault-raised border-hairline text-bone text-xs focus-visible:border-signal-amber h-8"
                />
              </div>

            </div>
          </Panel>

          {/* Security Scope Boundaries */}
          <Panel>
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="w-full px-4 py-3 flex items-center justify-between mono-meta hover:text-bone transition-colors"
            >
              <span>advanced scopes</span>
              {showAdvanced ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>

            {showAdvanced && (
              <div className="px-4 pb-4 space-y-4 text-xs border-t border-hairline">

                {/* Max Classification Level */}
                <div className="space-y-1.5 pt-4">
                  <label className="mono-meta block">max classification</label>
                  <select
                    value={scopeMaxClassification}
                    onChange={(e) => setScopeMaxClassification(e.target.value)}
                    className="w-full bg-vault-raised border border-hairline px-2.5 py-1.5 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                  >
                    {CLASSIFICATION_LEVELS.map((level) => (
                      <option key={level.value} value={level.value}>
                        {level.label}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Scope Collections */}
                <div className="space-y-1.5">
                  <label className="mono-meta block">limit to collections</label>
                  <Input
                    value={scopeCollections}
                    onChange={(e) => setScopeCollections(e.target.value)}
                    placeholder="e.g. policy, finance"
                    className="bg-vault-raised border-hairline text-bone text-xs focus-visible:border-signal-amber h-8"
                  />
                  <p className="mono-meta normal-case">
                    Comma-separated list of collection names.
                  </p>
                </div>

                {/* Scope Document IDs */}
                <div className="space-y-1.5">
                  <label className="mono-meta block">limit to document ids</label>
                  <Input
                    value={scopeDocumentIds}
                    onChange={(e) => setScopeDocumentIds(e.target.value)}
                    placeholder="e.g. doc_123, doc_456"
                    className="bg-vault-raised border-hairline text-bone text-xs focus-visible:border-signal-amber h-8"
                  />
                  <p className="mono-meta normal-case">
                    Comma-separated list of unique doc IDs.
                  </p>
                </div>

              </div>
            )}
          </Panel>

        </div>

      </div>
    </div>
  );
}
