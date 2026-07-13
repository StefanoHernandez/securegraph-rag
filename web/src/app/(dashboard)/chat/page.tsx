"use client";

import React, { useState, useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { Send, Trash2, ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SuggestInput } from "@/components/ui/suggest-input";
import type { Suggestion } from "@/components/ui/suggest-input";
import { Panel } from "@/components/forensic";
import { queryApi } from "@/lib/api";
import { Classification, QueryRequest, QueryResponse, RetrievalMode, QueryType } from "@/lib/types";
import AnswerCard from "@/components/chat/AnswerCard";
import { getErrorMessage } from "@/lib/errors";
import { useTenant } from "@/context/TenantProvider";
import { useSessionUser } from "@/lib/session";
import {
  appendCsvValue,
  loadTenantDocumentTypeSuggestions,
  searchTenantDocumentIds,
} from "@/lib/suggestions";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: QueryResponse;
  retrievalMode?: RetrievalMode;
  queryText?: string;
  timestamp: Date;
}

const CLASSIFICATION_LEVELS = [
  { value: "", label: "Inherit User Level" },
  { value: "public", label: "Public" },
  { value: "internal", label: "Internal" },
  { value: "confidential", label: "Confidential" },
  { value: "restricted", label: "Restricted" },
];

export default function ChatPage() {
  const t = useTranslations("chatPage");
  const { tenantId: selectedTenantId } = useTenant();
  const sessionUser = useSessionUser();
  const tenantId = selectedTenantId ?? "default";
  const userId = sessionUser?.id || sessionUser?.username || "anonymous";
  const suggestedPrompts = [
    t("suggestionSecurity"),
    t("suggestionConsensus"),
    t("suggestionDenied"),
    t("suggestionGraph"),
  ];
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState(0); // 0: routing, 1: retrieval, 2: consensus, 3: synthesis
  // Query Engine Options
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("auto");
  const [showAdvancedModes, setShowAdvancedModes] = useState(false);
  const [queryType, setQueryType] = useState<QueryType>("standard");
  const [maxGraphDepth, setMaxGraphDepth] = useState(2);
  const [topK, setTopK] = useState(10);
  const [consensusThreshold, setConsensusThreshold] = useState(0.7);

  // Scope settings
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [scopeMaxClassification, setScopeMaxClassification] = useState<Classification | "">("");
  const [scopeCollections, setScopeCollections] = useState("");
  const [scopeDocumentIds, setScopeDocumentIds] = useState("");
  const [collectionPicker, setCollectionPicker] = useState("");
  const [documentPicker, setDocumentPicker] = useState("");
  const [collectionSuggestions, setCollectionSuggestions] = useState<Suggestion[]>([]);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const pipelineSteps = [
    { label: t("pipelineRouting"), detail: t("pipelineRoutingDetail") },
    { label: t("pipelineRetrieval"), detail: t("pipelineRetrievalDetail") },
    { label: t("pipelineConsensus"), detail: t("pipelineConsensusDetail") },
    { label: t("pipelineSynthesis"), detail: t("pipelineSynthesisDetail") },
  ];

  useEffect(() => {
    // Scroll to bottom when messages list changes
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    let cancelled = false;
    const loadScopeCatalog = async () => {
      try {
        const types = await loadTenantDocumentTypeSuggestions(tenantId);
        if (!cancelled) setCollectionSuggestions(types);
      } catch {
        if (!cancelled) setCollectionSuggestions([]);
      }
    };
    void loadScopeCatalog();
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  // Loading animation step simulator
  useEffect(() => {
    let timer: NodeJS.Timeout;
    if (loading) {
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
        retrievalMode,
        queryText: textToSend,
        timestamp: new Date()
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error: unknown) {
      const detail = getErrorMessage(error, t("error"));
      const errorMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: t("queryError", { detail }),
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
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">{t("eyebrow")}</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            {t("title")}
          </h1>
        </div>
        <p className="mono-meta normal-case">
          {t("tenantOperator", { tenantId, userId })}
        </p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 h-[calc(100vh-220px)]">

        <div className="lg:col-span-3 flex flex-col h-full panel overflow-hidden">

          <div className="px-4 py-2.5 border-b border-hairline flex items-center justify-between">
            <span className="mono-meta">{t("session")}</span>
            {messages.length > 0 && (
              <Button
                variant="ghost"
                size="icon"
                onClick={clearChat}
                className="h-8 w-8 text-bone-dim hover:text-seal-red"
                title={t("clear")}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
          </div>

          <div
            className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 custom-scrollbar"
            role="log"
            aria-live="polite"
            aria-relevant="additions"
            aria-label={t("session")}
          >
            {messages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center max-w-lg mx-auto text-center space-y-6 py-8">
                <div className="space-y-2">
                  <p className="mono-meta">{t("emptyMeta")}</p>
                  <h3 className="font-display text-lg font-bold text-bone">
                    {t("emptyTitle")}
                  </h3>
                  <p className="text-xs text-bone-dim leading-relaxed">
                    {t("emptyCopy")}
                  </p>
                </div>
                <div className="w-full grid grid-cols-1 gap-2 pt-4">
                  {suggestedPrompts.map((prompt, idx) => (
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
                      {msg.role === "user" ? t("operator") : t("engine")} ·{" "}
                      {msg.timestamp.toLocaleTimeString()}
                    </div>
                    {msg.role === "user" ? (
                      <div className="max-w-[85%] px-4 py-3 bg-vault-raised border border-hairline text-bone text-sm">
                        {msg.content}
                      </div>
                    ) : (
                      <div className="w-full max-w-[95%]">
                        {msg.response ? (
                          <AnswerCard
                            response={msg.response}
                            retrievalMode={msg.retrievalMode}
                            queryText={msg.queryText}
                            tenantId={tenantId}
                          />
                        ) : (
                          <div
                            role="alert"
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

            {loading && (
              <div className="flex flex-col items-start space-y-2" role="status" aria-live="polite" aria-label={t("workflow")}>
                <span className="mono-meta normal-case">{t("processing")}</span>
                <div className="w-full max-w-md panel px-5 py-4">
                  <div className="flex items-center gap-2 pb-3 border-b border-hairline">
                    <Loader2 className="h-3.5 w-3.5 text-signal-amber animate-spin" />
                    <span className="mono-meta">{t("workflow")}</span>
                  </div>

                  <ol className="pt-4 space-y-0">
                    {pipelineSteps.map((step, idx) => {
                      const isActive = loadingStep === idx;
                      const isDone = loadingStep > idx;
                      const isLast = idx === pipelineSteps.length - 1;
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

          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend(input);
            }}
            className="p-4 border-t border-hairline bg-vault-raised flex items-center gap-3"
          >
            <Input
              id="chat-query"
              aria-label={t("queryInput")}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={t("placeholder")}
              className="flex-1 bg-vault-sunk border-hairline text-bone text-sm focus-visible:border-signal-amber h-11"
              disabled={loading}
              autoComplete="off"
            />
            <Button
              type="submit"
              aria-label={t("sendQuery")}
              disabled={loading || !input.trim()}
              className="px-5 h-11"
            >
              <Send className="h-4 w-4" />
            </Button>
          </form>
        </div>

        <div className="flex flex-col gap-6 h-full overflow-y-auto custom-scrollbar">

          <Panel label={t("engineConfig")} className="flex-shrink-0">
            <div className="px-4 py-4 space-y-4 text-xs">

              <div className="space-y-1.5">
                <label className="mono-meta block" htmlFor="retrieval-mode">{t("retrievalMode")}</label>
                <select
                  id="retrieval-mode"
                  value={showAdvancedModes ? retrievalMode : (retrievalMode === "auto" || retrievalMode === "hybrid" ? retrievalMode : "hybrid")}
                  onChange={(e) => setRetrievalMode(e.target.value as RetrievalMode)}
                  className="w-full bg-vault-raised border border-hairline px-2.5 py-1.5 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                >
                  <option value="auto">{t("modeAuto")}</option>
                  <option value="hybrid">{t("modeHybrid")}</option>
                  {showAdvancedModes && (
                    <>
                      <option value="vector">{t("modeVector")}</option>
                      <option value="lexical">{t("modeLexical")}</option>
                      <option value="graph">{t("modeGraph")}</option>
                    </>
                  )}
                </select>
                <button
                  type="button"
                  onClick={() => {
                    if (showAdvancedModes && retrievalMode !== "auto" && retrievalMode !== "hybrid") {
                      setRetrievalMode("auto");
                    }
                    setShowAdvancedModes(!showAdvancedModes);
                  }}
                  className="inline-flex items-center gap-1 mono-meta text-bone-dim hover:text-signal-amber transition-colors"
                >
                  {showAdvancedModes ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                  {t("advancedModes")}
                </button>
                {showAdvancedModes && (
                  <p className="font-mono text-[0.6875rem] leading-relaxed text-seal-red/90">
                    ⚠ {t("singleLaneWarning")}
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <label className="mono-meta block" htmlFor="query-strategy">{t("queryStrategy")}</label>
                <select
                  id="query-strategy"
                  value={queryType}
                  onChange={(e) => setQueryType(e.target.value as QueryType)}
                  className="w-full bg-vault-raised border border-hairline px-2.5 py-1.5 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                >
                  <option value="standard">{t("queryTypeStandard")}</option>
                  <option value="global">{t("queryTypeGlobal")}</option>
                </select>
              </div>

              <div className="space-y-1.5">
                <div className="flex justify-between items-center">
                  <label className="mono-meta block" htmlFor="max-graph-depth">{t("maxGraphDepth")}</label>
                  <span className="font-mono text-signal-amber">
                    {t("hops", { count: maxGraphDepth })}
                  </span>
                </div>
                <input
                  id="max-graph-depth"
                  type="range"
                  min="1"
                  max="4"
                  step="1"
                  value={maxGraphDepth}
                  onChange={(e) => setMaxGraphDepth(Number(e.target.value))}
                  className="w-full h-1.5 bg-vault-sunk appearance-none cursor-pointer accent-signal-amber"
                />
              </div>

              <div className="space-y-1.5">
                <div className="flex justify-between items-center">
                  <label className="mono-meta block" htmlFor="consensus-threshold">{t("consensusThreshold")}</label>
                  <span className="font-mono text-signal-amber">{(consensusThreshold * 100).toFixed(0)}%</span>
                </div>
                <input
                  id="consensus-threshold"
                  type="range"
                  min="0.0"
                  max="1.0"
                  step="0.05"
                  value={consensusThreshold}
                  onChange={(e) => setConsensusThreshold(Number(e.target.value))}
                  className="w-full h-1.5 bg-vault-sunk appearance-none cursor-pointer accent-signal-amber"
                />
                <p className="text-[0.625rem] text-bone-dim leading-tight">
                  {t("thresholdHelp")}
                </p>
              </div>

              <div className="space-y-1.5">
                <label className="mono-meta block" htmlFor="top-k">{t("topK")}</label>
                <Input
                  id="top-k"
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

          <Panel>
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              aria-expanded={showAdvanced}
              aria-controls="query-scopes"
              className="w-full px-4 py-3 flex items-center justify-between mono-meta hover:text-bone transition-colors"
            >
              <span>{t("advancedScopes")}</span>
              {showAdvanced ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>

            {showAdvanced && (
              <div id="query-scopes" className="px-4 pb-4 space-y-4 text-xs border-t border-hairline">

                <div className="space-y-1.5 pt-4">
                  <label className="mono-meta block" htmlFor="scope-max-classification">{t("maxClassification")}</label>
                  <select
                    id="scope-max-classification"
                    value={scopeMaxClassification}
                    onChange={(e) =>
                      setScopeMaxClassification(e.target.value as Classification | "")
                    }
                    className="w-full bg-vault-raised border border-hairline px-2.5 py-1.5 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                  >
                    {CLASSIFICATION_LEVELS.map((level) => (
                      <option key={level.value} value={level.value}>
                        {level.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1.5">
                  <label className="mono-meta block" htmlFor="scope-collections">{t("collections")}</label>
                  <Input
                    id="scope-collections"
                    value={scopeCollections}
                    onChange={(e) => setScopeCollections(e.target.value)}
                    placeholder={t("collectionPlaceholder")}
                    className="bg-vault-raised border-hairline text-bone text-xs focus-visible:border-signal-amber h-8"
                  />
                  <SuggestInput
                    value={collectionPicker}
                    onChange={setCollectionPicker}
                    suggestions={collectionSuggestions}
                    onPick={(item) => {
                      setScopeCollections((current) => appendCsvValue(current, item.value));
                      setCollectionPicker("");
                    }}
                    placeholder={t("addCollection")}
                    inputClassName="bg-vault-raised border-hairline text-bone text-xs focus-visible:border-signal-amber h-8"
                    emptyLabel={t("noCollectionMatches")}
                  />
                  <p className="mono-meta normal-case">
                    {t("collectionsHelp")}
                  </p>
                </div>

                <div className="space-y-1.5">
                  <label className="mono-meta block" htmlFor="scope-documents">{t("documents")}</label>
                  <Input
                    id="scope-documents"
                    value={scopeDocumentIds}
                    onChange={(e) => setScopeDocumentIds(e.target.value)}
                    placeholder={t("documentPlaceholder")}
                    className="bg-vault-raised border-hairline text-bone text-xs focus-visible:border-signal-amber h-8"
                  />
                  <SuggestInput
                    value={documentPicker}
                    onChange={setDocumentPicker}
                    onQuery={(query) => searchTenantDocumentIds(tenantId, query)}
                    onPick={(item) => {
                      setScopeDocumentIds((current) => appendCsvValue(current, item.value));
                      setDocumentPicker("");
                    }}
                    placeholder={t("addDocument")}
                    inputClassName="bg-vault-raised border-hairline text-bone text-xs focus-visible:border-signal-amber h-8"
                    emptyLabel={t("noDocumentMatches")}
                  />
                  <p className="mono-meta normal-case">
                    {t("documentsHelp")}
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
