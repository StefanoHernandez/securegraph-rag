"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  Anchor,
  BarChart3,
  Clock3,
  Network,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/forensic";
import { operationsApi } from "@/lib/api";
import { useErrorMessage } from "@/lib/errors";
import { OperationIngestionJob, OperationJob } from "@/lib/types";
import { useTenant as useTenantSelection } from "@/context/TenantProvider";

function statusClass(status: string): string {
  if (status === "completed" || status === "already_ingested") {
    return "text-signal-amber";
  }
  if (status === "failed" || status === "reconcile_required") {
    return "text-seal-red";
  }
  return "text-bone-dim";
}

export default function OperationsPage() {
  const t = useTranslations("operationsPage");
  const showError = useErrorMessage();
  const { tenantId, tenants, loadingTenants, selectTenant } = useTenantSelection();
  const [jobs, setJobs] = useState<OperationIngestionJob[]>([]);
  const [operationJobs, setOperationJobs] = useState<OperationJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [runningJobId, setRunningJobId] = useState<string | null>(null);
  const [runningOperation, setRunningOperation] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadJobs = useCallback(async () => {
    if (!tenantId) {
      setJobs([]);
      setOperationJobs([]);
      setLoading(loadingTenants);
      return;
    }
    setLoading(true);
    try {
      const [ingestionResult, operationResult] = await Promise.all([
        operationsApi.listIngestionJobs(tenantId),
        operationsApi.listOperationJobs(tenantId),
      ]);
      setJobs(ingestionResult.jobs);
      setOperationJobs(operationResult.jobs);
      setError(null);
    } catch (err: unknown) {
      setError(showError(err, t("loadError")));
    } finally {
      setLoading(false);
    }
  }, [loadingTenants, tenantId, t, showError]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadJobs(), 0);
    return () => window.clearTimeout(timer);
  }, [loadJobs]);

  const retryJob = async (jobId: string) => {
    if (!tenantId) return;
    setRunningJobId(jobId);
    setError(null);
    try {
      await operationsApi.retryIngestionJob(tenantId, jobId);
      setNotice(t("retryQueued", { jobId }));
      await loadJobs();
    } catch (err: unknown) {
      setError(showError(err, t("retryError")));
    } finally {
      setRunningJobId(null);
    }
  };

  const reconcileJobs = async () => {
    if (!tenantId) return;
    setReconciling(true);
    setError(null);
    try {
      const result = await operationsApi.reconcileIngestionJobs(tenantId);
      setNotice(
        result.reconciled_job_ids.length === 0
          ? t("noStaleJobs")
          : t("staleJobsFailed", { count: result.reconciled_job_ids.length })
      );
      await loadJobs();
    } catch (err: unknown) {
      setError(showError(err, t("reconcileError")));
    } finally {
      setReconciling(false);
    }
  };

  const runOperation = async (
    kind: "ledger" | "anchor" | "anchorDue" | "communities" | "eval"
  ) => {
    if (!tenantId) return;
    setRunningOperation(kind);
    setError(null);
    try {
      const result =
        kind === "ledger"
          ? await operationsApi.verifyLedger(tenantId)
          : kind === "anchor"
            ? await operationsApi.anchorLedger(tenantId)
            : kind === "anchorDue"
              ? await operationsApi.anchorLedgerIfDue(tenantId)
              : kind === "communities"
                ? await operationsApi.recomputeCommunities(tenantId)
                : await operationsApi.runEvalBaseline(tenantId, {
                    corpus_path: "corpus/mrd_v1",
                    timeout_seconds: 3600,
                  });
      setNotice(t("operationFinished", { kind: result.kind, status: result.status }));
      await loadJobs();
    } catch (err: unknown) {
      setError(showError(err, t("operationError")));
    } finally {
      setRunningOperation(null);
    }
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6 pb-10">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">{t("eyebrow")}</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            {t("title")}
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={tenantId ?? ""}
            onChange={(event) => selectTenant(event.target.value)}
            disabled={loadingTenants || tenants.length === 0}
            className="h-9 bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
          >
            {!tenantId && <option value="">{t("noTenant")}</option>}
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.display_name}
              </option>
            ))}
          </select>
          <Button size="sm" variant="outline" onClick={reconcileJobs} disabled={reconciling || !tenantId} className="gap-2">
            <Wrench className={`h-3.5 w-3.5 ${reconciling ? "animate-spin" : ""}`} />
            {t("reconcile")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void runOperation("ledger")} disabled={runningOperation !== null || !tenantId} className="gap-2">
            <ShieldCheck className={`h-3.5 w-3.5 ${runningOperation === "ledger" ? "animate-spin" : ""}`} />
            {t("verifyLedger")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void runOperation("anchor")} disabled={runningOperation !== null || !tenantId} className="gap-2">
            <Anchor className={`h-3.5 w-3.5 ${runningOperation === "anchor" ? "animate-spin" : ""}`} />
            {t("anchor")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void runOperation("anchorDue")} disabled={runningOperation !== null || !tenantId} className="gap-2">
            <Clock3 className={`h-3.5 w-3.5 ${runningOperation === "anchorDue" ? "animate-spin" : ""}`} />
            {t("anchorDue")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void runOperation("communities")} disabled={runningOperation !== null || !tenantId} className="gap-2">
            <Network className={`h-3.5 w-3.5 ${runningOperation === "communities" ? "animate-spin" : ""}`} />
            {t("recompute")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void runOperation("eval")} disabled={runningOperation !== null || !tenantId} className="gap-2">
            <BarChart3 className={`h-3.5 w-3.5 ${runningOperation === "eval" ? "animate-spin" : ""}`} />
            {t("eval")}
          </Button>
          <Button size="sm" onClick={loadJobs} disabled={loading || !tenantId} className="gap-2">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            {t("refresh")}
          </Button>
        </div>
      </header>

      {error && (
        <Panel classification="restricted" label={t("error")} role="alert">
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{error}</p>
        </Panel>
      )}
      {notice && (
        <Panel classification="internal" label={t("operationResult")} role="status" aria-live="polite">
          <p className="px-5 py-4 font-mono text-xs text-signal-amber">{notice}</p>
        </Panel>
      )}

      <Panel label={t("operationJobLedger")}>
        {loading ? (
          <div className="px-5 py-10 mono-meta">{t("loadingOperationJobs")}</div>
        ) : operationJobs.length === 0 ? (
          <div className="px-5 py-10 text-sm text-bone-dim">{t("emptyOperations")}</div>
        ) : (
          <div className="overflow-x-auto custom-scrollbar">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="border-b border-hairline mono-meta">
                <tr>
                  <th className="px-4 py-2 font-normal">{t("job")}</th>
                  <th className="px-4 py-2 font-normal">{t("kind")}</th>
                  <th className="px-4 py-2 font-normal">{t("status")}</th>
                  <th className="px-4 py-2 font-normal">{t("progress")}</th>
                  <th className="px-4 py-2 font-normal">{t("updated")}</th>
                  <th className="px-4 py-2 font-normal">{t("result")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {operationJobs.map((job) => (
                  <tr key={job.id}>
                    <td className="px-4 py-3 font-mono text-bone">{job.id}</td>
                    <td className="px-4 py-3 font-mono text-bone-dim">{job.kind}</td>
                    <td className={`px-4 py-3 font-mono uppercase ${statusClass(job.status)}`}>{job.status}</td>
                    <td className="px-4 py-3 font-mono text-bone-dim">{job.progress}%</td>
                    <td className="px-4 py-3 font-mono text-bone-dim">{new Date(job.updated_at).toLocaleString()}</td>
                    <td className="max-w-sm px-4 py-3 font-mono text-[0.625rem] text-bone-dim truncate" title={JSON.stringify(job.result)}>
                      {job.error ?? JSON.stringify(job.result)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel label={t("ingestionJobLedger")}>
        {loading ? (
          <div className="px-5 py-10 mono-meta">{t("loadingDurableJobs")}</div>
        ) : jobs.length === 0 ? (
          <div className="px-5 py-10 text-sm text-bone-dim">{t("emptyIngestion")}</div>
        ) : (
          <div className="overflow-x-auto custom-scrollbar">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="border-b border-hairline mono-meta">
                <tr>
                  <th className="px-4 py-2 font-normal">{t("job")}</th>
                  <th className="px-4 py-2 font-normal">{t("status")}</th>
                  <th className="px-4 py-2 font-normal">{t("attempts")}</th>
                  <th className="px-4 py-2 font-normal">{t("updated")}</th>
                  <th className="px-4 py-2 font-normal">{t("errorColumn")}</th>
                  <th className="px-4 py-2 font-normal" />
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {jobs.map((job) => (
                  <tr key={job.job_id}>
                    <td className="px-4 py-3 font-mono text-bone">
                      <div>{job.job_id}</div>
                      <div className="mt-1 text-[0.625rem] text-bone-dim">{job.document_id ?? t("pendingDocument")}</div>
                    </td>
                    <td className={`px-4 py-3 font-mono uppercase ${statusClass(job.status)}`}>
                      {job.status} · {job.current_step}
                    </td>
                    <td className="px-4 py-3 font-mono text-bone-dim">{job.attempt_count}</td>
                    <td className="px-4 py-3 font-mono text-bone-dim">{new Date(job.updated_at).toLocaleString()}</td>
                    <td className="max-w-xs px-4 py-3 text-bone-dim truncate" title={job.error ?? ""}>{job.error ?? "-"}</td>
                    <td className="px-4 py-3 text-right">
                      {job.status === "failed" && (
                        <Button size="sm" variant="outline" onClick={() => void retryJob(job.job_id)} disabled={runningJobId === job.job_id} className="gap-1.5">
                          <RotateCcw className={`h-3.5 w-3.5 ${runningJobId === job.job_id ? "animate-spin" : ""}`} />
                          {t("retry")}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
