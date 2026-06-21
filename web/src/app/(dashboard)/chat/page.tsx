"use client";

import React, { useState, useEffect, useRef } from "react";
import { 
  Send, 
  Settings2, 
  Sparkles, 
  Trash2, 
  HelpCircle,
  Database,
  Hash,
  GitMerge,
  Globe,
  Sliders,
  ChevronDown,
  ChevronUp,
  Shield,
  Loader2
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
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
  "Retrieve the consensus report for the Qwen stability metrics.",
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

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState(0); // 0: routing, 1: retrieval, 2: consensus, 3: synthesis
  const [user, setUser] = useState<any>(null);
  const [tenantId, setTenantId] = useState("tenant_a");
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
        setTenantId(storedTenant || parsed.tenant_id || "tenant_a");
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
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 max-w-7xl mx-auto h-[calc(100vh-140px)]">
      
      {/* Left Area: Chat messages and Input (3 cols) */}
      <div className="lg:col-span-3 flex flex-col h-full bg-[#12121a]/30 border border-border/40 rounded-2xl overflow-hidden glassmorphism relative">
        
        {/* Chat Header */}
        <div className="p-4 border-b border-border/10 flex items-center justify-between bg-black/20">
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-xl bg-accent-indigo/15 border border-accent-indigo/30 flex items-center justify-center text-accent-indigo">
              <Sparkles className="h-4.5 w-4.5" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-foreground">SecureGraph Query Engine</h2>
              <p className="text-[10px] text-foreground/40 font-mono">
                Tenant: <span className="text-accent-cyan font-bold">{tenantId}</span> • SecurSubject: <span className="text-accent-emerald font-bold">{userId}</span>
              </p>
            </div>
          </div>
          {messages.length > 0 && (
            <Button
              variant="ghost"
              size="icon"
              onClick={clearChat}
              className="h-8 w-8 text-foreground/50 hover:text-accent-rose hover:bg-accent-rose/10 rounded-lg transition"
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
              <div className="p-4 bg-accent-indigo/10 border border-accent-indigo/20 rounded-2xl text-accent-indigo animate-pulse">
                <Sparkles className="h-10 w-10" />
              </div>
              <div className="space-y-2">
                <h3 className="text-lg font-black text-foreground">Inizia una sessione di Query Protetta</h3>
                <p className="text-xs text-foreground/50 leading-relaxed">
                  Interroga il Knowledge Graph ibrido con sicurezza integrata. Tutte le query vengono analizzate dall'engine ReBAC di OpenFGA per potare i dati non autorizzati, e validate attraverso una NLI consensus pipeline per ridurre le allucinazioni.
                </p>
              </div>
              <div className="w-full grid grid-cols-1 gap-2 pt-4">
                {SUGGESTED_PROMPTS.map((prompt, idx) => (
                  <button
                    key={idx}
                    onClick={() => handleSend(prompt)}
                    className="text-left text-xs p-3.5 bg-black/40 border border-border/30 hover:border-accent-indigo/40 hover:bg-accent-indigo/5 text-foreground/80 hover:text-foreground rounded-xl transition duration-200"
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
                  <div className={`text-[10px] font-mono text-foreground/35 mb-1`}>
                    {msg.role === "user" ? "You" : "SecurGraph Engine"} • {msg.timestamp.toLocaleTimeString()}
                  </div>
                  {msg.role === "user" ? (
                    <div className="max-w-[85%] px-4 py-3 bg-gradient-to-r from-accent-indigo/25 to-accent-indigo/15 border border-accent-indigo/35 text-foreground text-sm rounded-2xl rounded-tr-none shadow-md">
                      {msg.content}
                    </div>
                  ) : (
                    <div className="w-full max-w-[95%] p-5 bg-black/30 border border-border/50 rounded-2xl rounded-tl-none shadow-lg glassmorphism-card">
                      {msg.response ? (
                        <AnswerCard response={msg.response} />
                      ) : (
                        // Markdown-only response for errors/simple texts
                        <div className="prose prose-invert prose-sm max-w-none text-foreground/90 leading-relaxed font-sans">
                          {msg.content}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Premium step-by-step loading state */}
          {loading && (
            <div className="flex flex-col items-start space-y-2">
              <span className="text-[10px] font-mono text-foreground/35">SecurGraph Engine • Elaborazione in corso...</span>
              <div className="w-full max-w-md p-5 bg-black/40 border border-accent-indigo/20 rounded-2xl rounded-tl-none shadow-lg glassmorphism">
                <div className="space-y-4">
                  <div className="flex items-center gap-2">
                    <Loader2 className="h-4 w-4 text-accent-indigo animate-spin" />
                    <span className="text-xs font-mono font-bold text-foreground">LangGraph Workflow Running...</span>
                  </div>

                  {/* Flow pipeline visualizer */}
                  <div className="space-y-3 pt-2">
                    {/* Step 1: Routing */}
                    <div className="flex items-center gap-3">
                      <div className={`h-4.5 w-4.5 rounded-full flex items-center justify-center text-[10px] font-bold font-mono transition-colors duration-300 ${
                        loadingStep === 0 
                          ? "bg-accent-indigo text-black border border-accent-indigo animate-pulse" 
                          : loadingStep > 0 
                            ? "bg-accent-emerald/20 text-accent-emerald border border-accent-emerald/30" 
                            : "bg-white/5 text-foreground/30 border border-white/10"
                      }`}>
                        {loadingStep > 0 ? "✓" : "1"}
                      </div>
                      <div className="flex-1">
                        <p className={`text-xs font-semibold ${loadingStep >= 0 ? "text-foreground" : "text-foreground/30"}`}>
                          Query Classification & Scope Routing
                        </p>
                        {loadingStep === 0 && (
                          <p className="text-[10px] text-foreground/45 animate-pulse font-mono">Intent parsing (granite4.1)...</p>
                        )}
                      </div>
                    </div>

                    {/* Step 2: Retrieval */}
                    <div className="flex items-center gap-3">
                      <div className={`h-4.5 w-4.5 rounded-full flex items-center justify-center text-[10px] font-bold font-mono transition-colors duration-300 ${
                        loadingStep === 1 
                          ? "bg-accent-indigo text-black border border-accent-indigo animate-pulse" 
                          : loadingStep > 1 
                            ? "bg-accent-emerald/20 text-accent-emerald border border-accent-emerald/30" 
                            : "bg-white/5 text-foreground/30 border border-white/10"
                      }`}>
                        {loadingStep > 1 ? "✓" : "2"}
                      </div>
                      <div className="flex-1">
                        <p className={`text-xs font-semibold ${loadingStep >= 1 ? "text-foreground" : "text-foreground/30"}`}>
                          Dual-Lane Retrieval & FGA Pruning
                        </p>
                        {loadingStep === 1 && (
                          <p className="text-[10px] text-foreground/45 animate-pulse font-mono flex items-center gap-2">
                            <span className="flex items-center gap-0.5 text-accent-indigo"><Database className="h-3 w-3" /> Vector</span>
                            <span className="text-foreground/30">•</span>
                            <span className="flex items-center gap-0.5 text-accent-cyan"><GitMerge className="h-3 w-3" /> Neo4j Graph</span>
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Step 3: Consensus */}
                    <div className="flex items-center gap-3">
                      <div className={`h-4.5 w-4.5 rounded-full flex items-center justify-center text-[10px] font-bold font-mono transition-colors duration-300 ${
                        loadingStep === 2 
                          ? "bg-accent-indigo text-black border border-accent-indigo animate-pulse" 
                          : loadingStep > 2 
                            ? "bg-accent-emerald/20 text-accent-emerald border border-accent-emerald/30" 
                            : "bg-white/5 text-foreground/30 border border-white/10"
                      }`}>
                        {loadingStep > 2 ? "✓" : "3"}
                      </div>
                      <div className="flex-1">
                        <p className={`text-xs font-semibold ${loadingStep >= 2 ? "text-foreground" : "text-foreground/30"}`}>
                          Cross-Lane Consensus Audit (NLI Judge)
                        </p>
                        {loadingStep === 2 && (
                          <p className="text-[10px] text-foreground/45 animate-pulse font-mono">Consensus checking (qwen3.5:2b)...</p>
                        )}
                      </div>
                    </div>

                    {/* Step 4: Synthesis */}
                    <div className="flex items-center gap-3">
                      <div className={`h-4.5 w-4.5 rounded-full flex items-center justify-center text-[10px] font-bold font-mono transition-colors duration-300 ${
                        loadingStep === 3 
                          ? "bg-accent-indigo text-black border border-accent-indigo animate-pulse" 
                          : "bg-white/5 text-foreground/30 border border-white/10"
                      }`}>
                        {loadingStep === 3 ? "⌛" : "4"}
                      </div>
                      <div className="flex-1">
                        <p className={`text-xs font-semibold ${loadingStep >= 3 ? "text-foreground" : "text-foreground/30"}`}>
                          Secure Synthesis & Lineage Anchoring
                        </p>
                        {loadingStep === 3 && (
                          <p className="text-[10px] text-foreground/45 animate-pulse font-mono">Hashing and persisting trace...</p>
                        )}
                      </div>
                    </div>

                  </div>
                </div>
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
          className="p-4 border-t border-border/10 bg-black/35 flex items-center gap-3"
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Chiedi al framework (es. Quali sono le policy di sicurezza...)"
            className="flex-1 bg-black/40 border-border/40 text-foreground text-sm focus-visible:ring-accent-indigo/50 h-11"
            disabled={loading}
            autoComplete="off"
          />
          <Button
            type="submit"
            disabled={loading || !input.trim()}
            className="bg-accent-indigo text-black font-bold hover:opacity-90 transition px-5 h-11 shadow-md cursor-pointer"
          >
            <Send className="h-4.5 w-4.5" />
          </Button>
        </form>
      </div>

      {/* Right Area: Query Engine Settings Sidebar (1 col) */}
      <div className="flex flex-col gap-6 h-full overflow-y-auto custom-scrollbar">
        
        {/* Configuration Card */}
        <Card className="glassmorphism border-border/50 text-foreground flex-shrink-0">
          <CardHeader className="pb-3 border-b border-border/10">
            <CardTitle className="text-sm font-bold flex items-center gap-2">
              <Settings2 className="h-4 w-4 text-accent-indigo" />
              Engine Config
            </CardTitle>
            <CardDescription className="text-[10px]">
              Tweak SecurGraph hybrid pipeline behaviors.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-4 space-y-4 text-xs">
            
            {/* Retrieval Mode */}
            <div className="space-y-1.5">
              <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                Retrieval Lane Mode
              </label>
              <select
                value={retrievalMode}
                onChange={(e) => setRetrievalMode(e.target.value as RetrievalMode)}
                className="w-full bg-black/45 border border-border/40 rounded-lg p-2 text-foreground font-sans focus:outline-none focus:border-accent-indigo"
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
              <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                Query Strategy
              </label>
              <select
                value={queryType}
                onChange={(e) => setQueryType(e.target.value as QueryType)}
                className="w-full bg-black/45 border border-border/40 rounded-lg p-2 text-foreground font-sans focus:outline-none focus:border-accent-indigo"
              >
                <option value="standard">Standard RAG (Entity focused)</option>
                <option value="global">Global Graph Summary</option>
              </select>
            </div>

            {/* Max Graph Depth */}
            <div className="space-y-1.5">
              <div className="flex justify-between items-center">
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Max Graph Depth
                </label>
                <span className="font-mono text-accent-cyan font-bold">{maxGraphDepth} hops</span>
              </div>
              <input
                type="range"
                min="1"
                max="4"
                step="1"
                value={maxGraphDepth}
                onChange={(e) => setMaxGraphDepth(Number(e.target.value))}
                className="w-full h-1.5 bg-black/45 rounded-lg appearance-none cursor-pointer accent-accent-cyan"
              />
            </div>

            {/* Consensus Threshold */}
            <div className="space-y-1.5">
              <div className="flex justify-between items-center">
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Consensus Threshold
                </label>
                <span className="font-mono text-accent-emerald font-bold">{(consensusThreshold * 100).toFixed(0)}%</span>
              </div>
              <input
                type="range"
                min="0.0"
                max="1.0"
                step="0.05"
                value={consensusThreshold}
                onChange={(e) => setConsensusThreshold(Number(e.target.value))}
                className="w-full h-1.5 bg-black/45 rounded-lg appearance-none cursor-pointer accent-accent-emerald"
              />
              <p className="text-[9px] text-foreground/40 leading-tight">
                Agreement score limit required to trigger the "High Trust" UX badge.
              </p>
            </div>

            {/* Top K */}
            <div className="space-y-1.5">
              <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                Top K Context Chunks
              </label>
              <Input
                type="number"
                min={1}
                max={100}
                value={topK}
                onChange={(e) => setTopK(Math.max(1, Math.min(100, Number(e.target.value) || 10)))}
                className="bg-black/45 border-border/40 text-foreground text-xs focus-visible:ring-accent-indigo/50 h-8"
              />
            </div>

          </CardContent>
        </Card>

        {/* Security Scope Boundaries */}
        <Card className="glassmorphism border-border/50 text-foreground">
          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="w-full p-4 flex items-center justify-between font-bold text-sm hover:bg-white/5 transition"
          >
            <span className="flex items-center gap-2">
              <Shield className="h-4 w-4 text-accent-cyan" />
              Advanced Scopes
            </span>
            {showAdvanced ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>

          {showAdvanced && (
            <CardContent className="p-4 pt-0 space-y-4 text-xs border-t border-border/10">
              
              {/* Max Classification Level */}
              <div className="space-y-1.5 pt-4">
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Max Classification
                </label>
                <select
                  value={scopeMaxClassification}
                  onChange={(e) => setScopeMaxClassification(e.target.value)}
                  className="w-full bg-black/45 border border-border/40 rounded-lg p-2 text-foreground font-sans focus:outline-none focus:border-accent-cyan"
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
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Limit to Collections
                </label>
                <Input
                  value={scopeCollections}
                  onChange={(e) => setScopeCollections(e.target.value)}
                  placeholder="e.g. policy, finance"
                  className="bg-black/45 border-border/40 text-foreground text-xs focus-visible:ring-accent-cyan/50 h-8"
                />
                <p className="text-[9px] text-foreground/40 font-mono">
                  Comma-separated list of collection names.
                </p>
              </div>

              {/* Scope Document IDs */}
              <div className="space-y-1.5">
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Limit to Document IDs
                </label>
                <Input
                  value={scopeDocumentIds}
                  onChange={(e) => setScopeDocumentIds(e.target.value)}
                  placeholder="e.g. doc_123, doc_456"
                  className="bg-black/45 border-border/40 text-foreground text-xs focus-visible:ring-accent-cyan/50 h-8"
                />
                <p className="text-[9px] text-foreground/40 font-mono">
                  Comma-separated list of unique doc IDs.
                </p>
              </div>

            </CardContent>
          )}
        </Card>

      </div>

    </div>
  );
}
