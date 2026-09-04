"use client";

import React, { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { HelpCircle, ShieldAlert, ArrowLeft, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/forensic";
import { CustodyTimeline } from "@/components/explain/CustodyTimeline";
import { explainApi } from "@/lib/api";
import { ExplainReport } from "@/lib/types";
import { useErrorMessage } from "@/lib/errors";
import { useTenant } from "@/context/TenantProvider";
import { useSessionUser } from "@/lib/session";

function ExplainContent() {
  const t = useTranslations("explainPage");
  const showError = useErrorMessage();
  const searchParams = useSearchParams();
  const router = useRouter();
  const traceIdParam = searchParams.get("trace_id");
  const { tenantId } = useTenant();
  const sessionUser = useSessionUser();

  const [traceId, setTraceId] = useState(traceIdParam || "");
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<ExplainReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchReport = useCallback(async (tid: string) => {
    setLoading(true);
    setError(null);
    try {
      const uId = sessionUser?.id || sessionUser?.username || "anonymous";
      const tId = tenantId || "default";
      const res = await explainApi.getReport(tid, tId, uId);
      setReport(res);
    } catch (err: unknown) {
      console.error(err);
      setError(
        showError(
          err,
          t("loadError")
        )
      );
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, [sessionUser, t, tenantId, showError]);

  useEffect(() => {
    if (!traceIdParam) return;
    const timer = window.setTimeout(() => void fetchReport(traceIdParam), 0);
    return () => window.clearTimeout(timer);
  }, [fetchReport, traceIdParam]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (traceId.trim()) {
      router.push(`/explain?trace_id=${traceId.trim()}`);
    }
  };

  return (
    <div className="space-y-6 max-w-4xl mx-auto pb-10">
      {/* Header */}
      <header className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div className="flex items-start gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.back()}
            className="h-8 w-8 text-bone-dim hover:text-bone"
            aria-label={t("back")}
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <p className="mono-meta">{t("eyebrow")}</p>
            <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
              {t("title")}
            </h1>
          </div>
        </div>

        <form
          onSubmit={handleSearchSubmit}
          className="flex items-center gap-2 max-w-md w-full sm:w-auto"
        >
          <div className="relative flex-1 sm:w-80">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-bone-dim" />
            <Input
              value={traceId}
              onChange={(e) => setTraceId(e.target.value)}
              placeholder={t("placeholder")}
              className="pl-9 bg-vault-raised border-hairline text-bone text-xs font-mono h-9 focus-visible:border-signal-amber"
              autoComplete="off"
            />
          </div>
          <Button type="submit" size="sm" className="h-9">
            {t("audit")}
          </Button>
        </form>
      </header>

      {!traceIdParam ? (
        <Panel label={t("awaiting")} className="max-w-xl mx-auto">
          <div className="px-6 py-16 text-center space-y-4">
            <div className="mx-auto h-12 w-12 border border-hairline bg-vault-sunk flex items-center justify-center text-bone-dim">
              <HelpCircle className="h-6 w-6" />
            </div>
            <h2 className="font-display text-lg font-bold text-bone">
              {t("noneTitle")}
            </h2>
            <p className="text-xs text-bone-dim max-w-md mx-auto leading-relaxed">
              {t("noneCopy")}
            </p>
          </div>
        </Panel>
      ) : loading ? (
        <div className="h-[50vh] flex flex-col items-center justify-center text-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-signal-amber border-t-transparent" />
          <p className="mono-meta">{t("loading")}</p>
        </div>
      ) : error ? (
        <Panel
          classification="restricted"
          label={t("error")}
          className="max-w-xl mx-auto"
        >
          <div className="px-8 py-8 text-center space-y-4">
            <div className="mx-auto h-12 w-12 border border-seal-red/40 bg-vault-sunk flex items-center justify-center text-seal-red">
              <ShieldAlert className="h-6 w-6" />
            </div>
            <h2 className="font-display text-lg font-bold text-bone">
              {t("error")}
            </h2>
            <p className="text-xs text-bone-dim leading-relaxed font-mono">{error}</p>
            <Button variant="outline" size="sm" onClick={() => router.push("/chat")}>
              {t("backToChat")}
            </Button>
          </div>
        </Panel>
      ) : report ? (
        <CustodyTimeline report={report} />
      ) : null}
    </div>
  );
}

export default function ExplainPage() {
  const t = useTranslations("explainPage");
  return (
    <Suspense
      fallback={
        <div className="h-[50vh] flex flex-col items-center justify-center text-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-signal-amber border-t-transparent" />
          <p className="mono-meta">{t("fallback")}</p>
        </div>
      }
    >
      <ExplainContent />
    </Suspense>
  );
}
