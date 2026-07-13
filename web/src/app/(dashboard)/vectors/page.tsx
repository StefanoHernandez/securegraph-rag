"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  ArrowUpRight,
  Box,
  Copy,
  Layers,
  RefreshCw,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/forensic";
import VectorAtlasPlot from "@/components/vectors/VectorAtlasPlot";
import type { AtlasPoint, AtlasViewMode } from "@/components/vectors/atlasPoint";
import { adminApi } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { useTenant as useTenantSelection } from "@/context/TenantProvider";

const VectorScene3D = dynamic(() => import("@/components/vectors/VectorScene3D"), {
  ssr: false,
  loading: () => (
    <div className="h-full w-full flex items-center justify-center mono-meta text-bone-dim">
      …
    </div>
  ),
});

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return reduced;
}

export default function VectorsPage() {
  const t = useTranslations("vectorsPage");
  const searchParams = useSearchParams();
  const reducedMotion = useReducedMotion();
  const { tenants, tenantId, loadingTenants, tenantError, selectTenant } =
    useTenantSelection();

  const [points, setPoints] = useState<AtlasPoint[]>([]);
  const [projectionMethod, setProjectionMethod] = useState<string>("pca");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [documentFilter, setDocumentFilter] = useState("");
  const [classificationFilter, setClassificationFilter] = useState("");
  const [viewMode, setViewMode] = useState<AtlasViewMode>("2d");

  const effectiveViewMode: AtlasViewMode = reducedMotion ? "2d" : viewMode;
  const projectionDims = effectiveViewMode === "3d" ? 3 : 2;

  const [queryText, setQueryText] = useState(() => searchParams.get("q") ?? "");
  const [highlightIds, setHighlightIds] = useState<Set<string>>(() => {
    const raw = searchParams.get("highlight");
    if (!raw) return new Set();
    return new Set(
      raw
        .split(",")
        .map((id) => id.trim())
        .filter(Boolean)
    );
  });
  const [neighborHits, setNeighborHits] = useState<
    Array<{ chunk_id: string; score: number; point_id: string }>
  >([]);
  const [selected, setSelected] = useState<AtlasPoint | null>(null);

  const highlightedPointIds = useMemo(() => {
    const ids = new Set(highlightIds);
    for (const hit of neighborHits) {
      ids.add(hit.point_id);
    }
    for (const point of points) {
      if (highlightIds.has(point.chunk_id)) {
        ids.add(point.id);
      }
    }
    return ids;
  }, [highlightIds, neighborHits, points]);

  const loadAtlas = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await adminApi.getVectorAtlas(tenantId, {
        documentId: documentFilter || undefined,
        classification: classificationFilter || undefined,
        dims: projectionDims,
      });
      setPoints(res.points);
      setProjectionMethod(res.projection_method);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("loadError")));
    } finally {
      setLoading(false);
    }
  }, [tenantId, documentFilter, classificationFilter, projectionDims, t]);

  const runNeighborSearch = useCallback(
    async (text: string) => {
      if (!tenantId || !text.trim()) return;
      setLoading(true);
      setError(null);
      try {
        const res = await adminApi.searchVectorAtlasNeighbors(tenantId, text.trim(), 10);
        setNeighborHits(
          res.chunk_ids.map((chunkId, idx) => ({
            chunk_id: chunkId,
            score: res.scores[idx] ?? 0,
            point_id: res.point_ids[idx] ?? "",
          }))
        );
      } catch (err: unknown) {
        setError(getErrorMessage(err, t("searchError")));
      } finally {
        setLoading(false);
      }
    },
    [tenantId, t]
  );

  useEffect(() => {
    void loadAtlas();
  }, [loadAtlas]);

  useEffect(() => {
    const tenant = searchParams.get("tenant");
    if (tenant) selectTenant(tenant);
    const q = searchParams.get("q");
    const highlight = searchParams.get("highlight");
    if (q) setQueryText(q);
    if (highlight) {
      setHighlightIds(
        new Set(
          highlight
            .split(",")
            .map((id) => id.trim())
            .filter(Boolean)
        )
      );
    }
    if (q && tenantId) {
      void runNeighborSearch(q);
    }
  }, [searchParams, tenantId, runNeighborSearch, selectTenant]);

  const documentOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const p of points) {
      if (!p.document_id) continue;
      const label = p.source_uri
        ? p.source_uri.replace(/^file:\/\//, "")
        : p.document_id;
      seen.set(p.document_id, label);
    }
    return Array.from(seen.entries());
  }, [points]);

  const copyChunkId = async (chunkId: string) => {
    try {
      await navigator.clipboard.writeText(chunkId);
    } catch {
      // clipboard may be unavailable
    }
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">{t("eyebrow")}</p>
          <h1 className="font-display text-2xl font-bold text-bone">{t("title")}</h1>
          <p className="text-xs text-bone-dim mt-1 max-w-2xl">{t("subtitle")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {loadingTenants ? (
            <span className="mono-meta">{t("loadingTenants")}</span>
          ) : tenantError ? (
            <span className="text-xs text-seal-red">{tenantError}</span>
          ) : (
            <select
              value={tenantId ?? ""}
              onChange={(e) => selectTenant(e.target.value)}
              className="bg-vault-raised border border-hairline px-3 py-2 text-xs text-bone font-mono"
            >
              {tenants.map((tenant) => (
                <option key={tenant.id} value={tenant.id}>
                  {tenant.display_name}
                </option>
              ))}
            </select>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => void loadAtlas()}
            disabled={loading || !tenantId}
            className="font-mono text-xs"
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            {t("refresh")}
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 min-h-[calc(100vh-220px)]">
        <div className="lg:col-span-3 flex flex-col gap-4">
          <Panel className="p-4 space-y-3">
            <div className="flex flex-col md:flex-row gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-bone-dim" />
                <Input
                  value={queryText}
                  onChange={(e) => setQueryText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void runNeighborSearch(queryText);
                  }}
                  placeholder={t("queryPlaceholder")}
                  className="pl-9 font-mono text-xs"
                />
              </div>
              <Button
                onClick={() => void runNeighborSearch(queryText)}
                disabled={loading || !queryText.trim()}
                className="font-mono text-xs"
              >
                {t("search")}
              </Button>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-2">
                <select
                  value={documentFilter}
                  onChange={(e) => setDocumentFilter(e.target.value)}
                  className="bg-vault-raised border border-hairline px-3 py-2 text-xs text-bone font-mono min-w-[12rem]"
                >
                  <option value="">{t("allDocuments")}</option>
                  {documentOptions.map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
                </select>
                <select
                  value={classificationFilter}
                  onChange={(e) => setClassificationFilter(e.target.value)}
                  className="bg-vault-raised border border-hairline px-3 py-2 text-xs text-bone font-mono"
                >
                  <option value="">{t("allClassifications")}</option>
                  {["public", "internal", "confidential", "restricted"].map((level) => (
                    <option key={level} value={level}>
                      {level}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex border border-hairline">
                <button
                  type="button"
                  onClick={() => setViewMode("2d")}
                  disabled={reducedMotion}
                  className={cn(
                    "inline-flex items-center gap-1.5 px-3 py-2 font-mono text-[0.6875rem] uppercase tracking-wider transition-colors",
                    effectiveViewMode === "2d"
                      ? "bg-vault-raised text-bone"
                      : "text-bone-dim hover:text-bone"
                  )}
                >
                  <Layers className="h-3.5 w-3.5" />
                  {t("view2d")}
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("3d")}
                  disabled={reducedMotion}
                  className={cn(
                    "inline-flex items-center gap-1.5 px-3 py-2 font-mono text-[0.6875rem] uppercase tracking-wider border-l border-hairline transition-colors",
                    effectiveViewMode === "3d"
                      ? "bg-vault-raised text-bone"
                      : "text-bone-dim hover:text-bone"
                  )}
                >
                  <Box className="h-3.5 w-3.5" />
                  {t("view3d")}
                </button>
              </div>
            </div>
            <p className="mono-meta">
              {t("pointCount", { count: points.length })} · {t("projection", { method: projectionMethod })} ·{" "}
              {effectiveViewMode === "3d" ? t("dims3d") : t("dims2d")}
              {reducedMotion ? ` · ${t("reducedMotion2d")}` : ""}
            </p>
          </Panel>

          <Panel className="flex-1 min-h-[420px] p-0 overflow-hidden">
            {error ? (
              <div className="h-full flex items-center justify-center text-sm text-seal-red px-6 text-center">
                {error}
              </div>
            ) : points.length === 0 && !loading ? (
              <div className="h-full flex items-center justify-center text-sm text-bone-dim px-6 text-center">
                {t("empty")}
              </div>
            ) : effectiveViewMode === "3d" ? (
              <VectorScene3D
                points={points}
                highlightedIds={highlightedPointIds}
                selectedId={selected?.id ?? null}
                onSelect={setSelected}
              />
            ) : (
              <VectorAtlasPlot
                points={points}
                highlightedIds={highlightedPointIds}
                selectedId={selected?.id ?? null}
                onSelect={setSelected}
                reducedMotion={reducedMotion}
              />
            )}
          </Panel>
        </div>

        <div className="space-y-4">
          {neighborHits.length > 0 && (
            <Panel className="p-4 space-y-3">
              <p className="mono-meta">{t("neighbors")}</p>
              <ul className="space-y-2 max-h-48 overflow-y-auto custom-scrollbar">
                {neighborHits.map((hit) => (
                  <li
                    key={hit.point_id}
                    className="border border-hairline px-3 py-2 text-xs cursor-pointer hover:border-signal-amber"
                    onClick={() => {
                      const point = points.find((p) => p.id === hit.point_id);
                      if (point) setSelected(point);
                    }}
                  >
                    <p className="font-mono text-bone truncate">{hit.chunk_id}</p>
                    <p className="mono-meta mt-1 tabular-nums">
                      {t("score")} {(hit.score * 100).toFixed(1)}%
                    </p>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel className="p-4 space-y-3 min-h-[16rem]">
            <p className="mono-meta">{t("chunkPreview")}</p>
            {!selected ? (
              <p className="text-xs text-bone-dim">{t("selectPoint")}</p>
            ) : (
              <div className="space-y-3 text-xs">
                <div>
                  <p className="mono-meta">{t("chunkId")}</p>
                  <p className="font-mono text-bone break-all">{selected.chunk_id}</p>
                </div>
                {selected.source_uri && (
                  <div>
                    <p className="mono-meta">{t("sourceUri")}</p>
                    <p className="font-mono text-bone break-all">{selected.source_uri}</p>
                  </div>
                )}
                <div>
                  <p className="mono-meta">{t("classification")}</p>
                  <p className="text-bone">{selected.classification}</p>
                </div>
                <p className="text-bone/85 leading-relaxed">{selected.text_preview}</p>
                <div className="flex flex-wrap gap-2 pt-1">
                  {selected.last_trace_id ? (
                    <Link
                      href={`/explain?trace_id=${selected.last_trace_id}`}
                      className="inline-flex items-center gap-1 font-mono text-[0.6875rem] uppercase tracking-wider text-signal-amber hover:text-bone"
                    >
                      {t("openExplain")}
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    </Link>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => void copyChunkId(selected.chunk_id)}
                    className="inline-flex items-center gap-1 font-mono text-[0.6875rem] uppercase tracking-wider text-bone-dim hover:text-bone"
                  >
                    <Copy className="h-3.5 w-3.5" />
                    {t("copyChunkId")}
                  </button>
                </div>
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
