"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  MessageSquare,
  UploadCloud,
  ShieldCheck,
  TrendingUp,
  FileText,
  HelpCircle,
  Play,
  ArrowRight,
  Database,
  Search
} from "lucide-react";

export default function DashboardPage() {
  const [stats, setStats] = useState({
    totalDocuments: 12,
    totalQueries: 147,
    avgConfidence: 89.4,
    healthStatus: "Healthy",
  });

  const [recentQueries, setRecentQueries] = useState([
    {
      trace_id: "7d9b23b8-89c0-43ef-ad2d-080b4317be9e",
      query: "Quali sono le policy di sicurezza per l'accesso ai dati sensibili?",
      latency: "1.2s",
      confidence: 0.94,
      timestamp: "5 minuti fa",
      status: "High Trust",
    },
    {
      trace_id: "8c772be4-722a-4311-8fe2-8fcd21bf23ea",
      query: "Retrieve the consensus report for the Qwen stability metrics.",
      latency: "2.1s",
      confidence: 0.82,
      timestamp: "1 ora fa",
      status: "High Trust",
    },
    {
      trace_id: "4df8bc31-d8ea-4fe2-921b-c741ea0289a0",
      query: "Show relationship between OpenFGA policies and Neo4j nodes.",
      latency: "1.8s",
      confidence: 0.71,
      timestamp: "3 ore fa",
      status: "Standard",
    },
    {
      trace_id: "2a9f1430-8911-460d-8ea2-cfdcd21bf3a2",
      query: "Elenco degli accessi negati per il tenant_a oggi.",
      latency: "0.9s",
      confidence: 0.98,
      timestamp: "1 giorno fa",
      status: "High Trust",
    },
  ]);

  return (
    <div className="space-y-8 max-w-6xl mx-auto">
      {/* Welcome Banner */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-border/20 pb-6">
        <div>
          <h1 className="text-2xl md:text-3xl font-black tracking-tight text-foreground">
            Bentornato, <span className="gradient-text">Stefano</span>
          </h1>
          <p className="text-xs text-foreground/50 font-mono mt-1">
            SecurGraph RAG Control Center • Active Session: admin
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link href="/chat">
            <Button className="bg-accent-indigo text-black font-bold hover:opacity-90 transition gap-2 text-xs h-9">
              <MessageSquare className="h-4 w-4" />
              New Query
            </Button>
          </Link>
          <Link href="/ingest">
            <Button variant="outline" className="border-border/50 bg-black/20 text-foreground hover:bg-white/5 gap-2 text-xs h-9">
              <UploadCloud className="h-4 w-4" />
              Ingest Document
            </Button>
          </Link>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        {/* Stat 1 */}
        <Card className="glassmorphism border-border/40 hover:border-accent-indigo/20 transition-all duration-300">
          <CardHeader className="pb-2">
            <CardDescription className="text-xs font-mono uppercase text-foreground/45 flex items-center justify-between">
              Total Documents
              <FileText className="h-4 w-4 text-accent-indigo" />
            </CardDescription>
            <CardTitle className="text-3xl font-black text-foreground">
              {stats.totalDocuments}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-[10px] text-foreground/45">Ingested across all active policies</p>
          </CardContent>
        </Card>

        {/* Stat 2 */}
        <Card className="glassmorphism border-border/40 hover:border-accent-cyan/20 transition-all duration-300">
          <CardHeader className="pb-2">
            <CardDescription className="text-xs font-mono uppercase text-foreground/45 flex items-center justify-between">
              Executed Queries
              <MessageSquare className="h-4 w-4 text-accent-cyan" />
            </CardDescription>
            <CardTitle className="text-3xl font-black text-foreground">
              {stats.totalQueries}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-[10px] text-foreground/45">+12.4% traffic growth this week</p>
          </CardContent>
        </Card>

        {/* Stat 3 */}
        <Card className="glassmorphism border-border/40 hover:border-accent-emerald/20 transition-all duration-300">
          <CardHeader className="pb-2">
            <CardDescription className="text-xs font-mono uppercase text-foreground/45 flex items-center justify-between">
              Avg Consensus Trust
              <TrendingUp className="h-4 w-4 text-accent-emerald" />
            </CardDescription>
            <CardTitle className="text-3xl font-black text-foreground">
              {stats.avgConfidence}%
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-[10px] text-foreground/45">High Agreement threshold: 0.70</p>
          </CardContent>
        </Card>

        {/* Stat 4 */}
        <Card className="glassmorphism border-border/40 hover:border-accent-violet/20 transition-all duration-300">
          <CardHeader className="pb-2">
            <CardDescription className="text-xs font-mono uppercase text-foreground/45 flex items-center justify-between">
              Security Health
              <ShieldCheck className="h-4 w-4 text-accent-violet" />
            </CardDescription>
            <CardTitle className="text-3xl font-black text-foreground">
              {stats.healthStatus}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-[10px] text-foreground/45">OpenFGA policy models: Active</p>
          </CardContent>
        </Card>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Side: Recent Queries */}
        <Card className="lg:col-span-2 glassmorphism border-border/50">
          <CardHeader className="pb-3 border-b border-border/10">
            <CardTitle className="text-base font-bold text-foreground">Recent Queries History</CardTitle>
            <CardDescription className="text-xs">Click on a trace to investigate provenance lineage.</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-border/10">
              {recentQueries.map((item) => (
                <div key={item.trace_id} className="p-4 hover:bg-white/5 transition flex items-start justify-between gap-4">
                  <div className="space-y-1.5 min-w-0">
                    <p className="text-sm font-medium text-foreground truncate">{item.query}</p>
                    <div className="flex items-center gap-3 text-[10px] text-foreground/40 font-mono">
                      <span>Latency: <b className="text-foreground">{item.latency}</b></span>
                      <span>Confidence: <b className="text-foreground">{(item.confidence * 100).toFixed(0)}%</b></span>
                      <span>{item.timestamp}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2.5 flex-shrink-0">
                    <Badge
                      className={
                        item.status === "High Trust"
                          ? "bg-accent-emerald/10 text-accent-emerald border-accent-emerald/20 hover:bg-accent-emerald/20 text-[10px]"
                          : "bg-accent-amber/10 text-accent-amber border-accent-amber/20 hover:bg-accent-amber/20 text-[10px]"
                      }
                    >
                      {item.status}
                    </Badge>
                    <Link href={`/explain?trace_id=${item.trace_id}`}>
                      <Button variant="ghost" size="icon" className="h-7 w-7 rounded-md text-foreground/50 hover:text-foreground hover:bg-white/5">
                        <HelpCircle className="h-4 w-4" />
                      </Button>
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Right Side: Quick Links + Framework Presentation info */}
        <div className="space-y-6">
          {/* Quick Info Box */}
          <Card className="glassmorphism border-border/50 bg-gradient-to-br from-accent-indigo/5 to-transparent relative overflow-hidden">
            <div className="absolute top-0 right-0 p-8 opacity-5 pointer-events-none select-none text-8xl font-black">RAG</div>
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-bold text-foreground">Interactive Presentation</CardTitle>
              <CardDescription className="text-xs">Learn about SecurGraph architecture, consensus dataflows, and evaluations.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-xs text-foreground/60 leading-relaxed">
                We built a rich, animated slideshow presentation explaining how the hybrid vector-graph model secures metadata and verifies grounding.
              </p>
              <a
                href="http://localhost:8765/securegraph-presentation.html"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex w-full"
              >
                <Button className="w-full bg-accent-cyan text-black hover:opacity-90 font-bold transition gap-1.5 text-xs h-9 shadow-lg">
                  <Play className="h-3.5 w-3.5 fill-black" />
                  Launch Presentation
                  <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </a>
            </CardContent>
          </Card>

          {/* Quick Config Summary */}
          <Card className="glassmorphism border-border/50">
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-bold text-foreground">Active Configuration</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3.5 text-xs font-mono">
              <div className="flex items-center justify-between border-b border-border/10 pb-1.5">
                <span className="text-foreground/50">Core LLM Model</span>
                <span className="text-foreground font-semibold">granite4.1:3b</span>
              </div>
              <div className="flex items-center justify-between border-b border-border/10 pb-1.5">
                <span className="text-foreground/50">Embedding Model</span>
                <span className="text-foreground font-semibold">bge-m3</span>
              </div>
              <div className="flex items-center justify-between border-b border-border/10 pb-1.5">
                <span className="text-foreground/50">NLI Judge Model</span>
                <span className="text-foreground font-semibold">qwen3.5:2b</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-foreground/50">Security Store</span>
                <span className="text-foreground font-semibold">OpenFGA ReBAC</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
