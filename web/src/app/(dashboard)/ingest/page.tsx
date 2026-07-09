"use client";

import React, { useState, useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import {
  UploadCloud,
  FileText,
  X,
  Clock,
  Database,
  Tag,
  User,
  Trash2,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel, ClassificationStamp } from "@/components/forensic";
import { ingestApi } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";

interface IngestionTrackerJob {
  job_id: string;
  document_id: string;
  trace_id: string;
  filename: string;
  fileSize: string;
  tenant_id: string;
  owner_id: string;
  classification: string;
  document_type: string;
  status: "queued" | "running" | "completed" | "failed";
  error?: string | null;
  created_at: string;
}

const DOCUMENT_TYPES = [
  { value: "policy", label: "Security Policy" },
  { value: "contract", label: "Contract Agreement" },
  { value: "report", label: "Analytics Report" },
  { value: "specification", label: "Technical Specification" },
  { value: "note", label: "Internal Note" },
  { value: "other", label: "Other Document" }
];

function loadStoredJobs(): IngestionTrackerJob[] {
  if (typeof window === "undefined") return [];
  const savedJobs = localStorage.getItem("securgraph_ingest_jobs");
  if (!savedJobs) return [];
  try {
    return JSON.parse(savedJobs) as IngestionTrackerJob[];
  } catch {
    return [];
  }
}

function loadStoredIdentity(): { tenantId: string; ownerId: string } {
  if (typeof window === "undefined") {
    return { tenantId: "default", ownerId: "user:anonymous" };
  }
  const storedTenant = localStorage.getItem("securgraph_tenant");
  const storedUser = localStorage.getItem("securgraph_user");
  if (!storedUser) {
    return { tenantId: storedTenant || "default", ownerId: "user:anonymous" };
  }
  try {
    const user = JSON.parse(storedUser) as {
      id?: string;
      username?: string;
      tenant_id?: string;
    };
    return {
      tenantId: storedTenant || user.tenant_id || "default",
      ownerId: `user:${user.id || user.username || "anonymous"}`,
    };
  } catch {
    return { tenantId: storedTenant || "default", ownerId: "user:anonymous" };
  }
}

export default function IngestPage() {
  const t = useTranslations("ingestPage");
  const [initialIdentity] = useState(loadStoredIdentity);
  const [file, setFile] = useState<File | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Form Fields
  const [tenantId, setTenantId] = useState(initialIdentity.tenantId);
  const [ownerId, setOwnerId] = useState(initialIdentity.ownerId);
  const [documentType, setDocumentType] = useState("policy");
  const [classification, setClassification] = useState("internal");
  const [policyTags, setPolicyTags] = useState("");
  const [titleOverride, setTitleOverride] = useState("");

  // Jobs Tracker
  const [jobs, setJobs] = useState<IngestionTrackerJob[]>(loadStoredJobs);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Save jobs to localStorage
  const saveJobsToStorage = (updatedJobs: IngestionTrackerJob[]) => {
    setJobs(updatedJobs);
    localStorage.setItem("securgraph_ingest_jobs", JSON.stringify(updatedJobs));
  };

  // Poll status for running/queued jobs
  useEffect(() => {
    const activeJobs = jobs.filter(j => j.status === "queued" || j.status === "running");
    if (activeJobs.length === 0) return;

    const interval = setInterval(async () => {
      let changed = false;
      const updatedJobs = [...jobs];

      for (let i = 0; i < updatedJobs.length; i++) {
        const job = updatedJobs[i];
        if (job.status === "queued" || job.status === "running") {
          try {
            const statusResponse = await ingestApi.getStatus(job.job_id);
            if (statusResponse.status !== job.status || statusResponse.error !== job.error) {
              updatedJobs[i] = {
                ...job,
                status: statusResponse.status,
                error: statusResponse.error || null,
              };
              changed = true;
            }
          } catch (err) {
            console.error(`Error polling job status for ${job.job_id}:`, err);
          }
        }
      }

      if (changed) {
        saveJobsToStorage(updatedJobs);
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [jobs]);

  // File size formatter
  const formatBytes = (bytes: number, decimals = 2) => {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
  };

  // Drag and drop handlers
  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setIsDragActive(true);
    } else if (e.type === "dragleave") {
      setIsDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const droppedFile = e.dataTransfer.files[0];
      validateAndSetFile(droppedFile);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      validateAndSetFile(e.target.files[0]);
    }
  };

  const validateAndSetFile = (selectedFile: File) => {
    setErrorMessage(null);
    setSuccessMessage(null);

    // Limit to 50MB
    const limit = 50 * 1024 * 1024;
    if (selectedFile.size > limit) {
      setErrorMessage(t("sizeError"));
      setFile(null);
      return;
    }

    setFile(selectedFile);
  };

  const triggerFileSelect = () => {
    fileInputRef.current?.click();
  };

  const removeFile = () => {
    setFile(null);
    setErrorMessage(null);
  };

  const handleIngest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) {
      setErrorMessage(t("fileRequired"));
      return;
    }

    setLoading(true);
    setErrorMessage(null);
    setSuccessMessage(null);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("tenant_id", tenantId);
    formData.append("owner_id", ownerId);
    formData.append("document_type", documentType);
    formData.append("classification", classification);
    formData.append("policy_tags", policyTags);
    if (titleOverride.trim()) {
      formData.append("title_override", titleOverride.trim());
    }

    try {
      const result = await ingestApi.uploadFile(formData);

      const newJob: IngestionTrackerJob = {
        job_id: result.job_id,
        document_id: result.document_id,
        trace_id: result.trace_id,
        filename: file.name,
        fileSize: formatBytes(file.size),
        tenant_id: tenantId,
        owner_id: ownerId,
        classification: classification,
        document_type: documentType,
        status: "queued",
        created_at: new Date().toLocaleTimeString(),
      };

      saveJobsToStorage([newJob, ...jobs]);
      setSuccessMessage(t("uploadSuccess", { jobId: result.job_id }));
      setFile(null);
      setTitleOverride("");
      setPolicyTags("");
    } catch (err: unknown) {
      setErrorMessage(
        getErrorMessage(err, t("uploadError"))
      );
    } finally {
      setLoading(false);
    }
  };

  const clearJobsList = () => {
    saveJobsToStorage([]);
  };

  // Ledger status stamp — queued/running = bone-dim, completed = amber, failed = seal-red
  const statusStamp = (status: string) => {
    switch (status) {
      case "completed":
        return <span className="stamp text-signal-amber">{t("completed")}</span>;
      case "failed":
        return <span className="stamp text-seal-red">{t("failed")}</span>;
      case "running":
        return (
          <span className="stamp text-bone-dim">
            <RefreshCw className="h-3 w-3 animate-spin" /> {t("running")}
          </span>
        );
      default:
        return <span className="stamp text-bone-dim">{t("queued")}</span>;
    }
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6 pb-10">
      <header className="pb-4 border-b border-hairline">
        <p className="mono-meta">{t("eyebrow")}</p>
        <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
          {t("title")}
        </h1>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 items-start">
        <div className="lg:col-span-3">
          <Panel label={t("panel")}>
            <form onSubmit={handleIngest} className="px-5 py-5 space-y-6">
              <div
                onDragEnter={handleDrag}
                onDragOver={handleDrag}
                onDragLeave={handleDrag}
                onDrop={handleDrop}
                onClick={triggerFileSelect}
                className={`border border-dashed p-8 text-center cursor-pointer transition-colors flex flex-col items-center justify-center min-h-48 ${
                  isDragActive
                    ? "border-signal-amber bg-[rgba(232,178,58,0.06)]"
                    : "border-hairline bg-vault-raised hover:bg-[rgba(232,230,223,0.03)]"
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  onChange={handleFileChange}
                  accept=".txt,.md,.markdown,.pdf,.docx,.xlsx"
                  className="hidden"
                />

                {file ? (
                  <div className="w-full max-w-md" onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-between px-3.5 py-3 bg-vault-sunk border border-hairline">
                      <div className="flex items-center gap-3 min-w-0">
                        <FileText className="h-5 w-5 flex-shrink-0 text-signal-amber" />
                        <div className="text-left min-w-0">
                          <p className="text-xs text-bone truncate">{file.name}</p>
                          <p className="mono-meta normal-case mt-0.5">{formatBytes(file.size)}</p>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={removeFile}
                        className="p-1 text-bone-dim hover:text-seal-red transition-colors"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-3 pointer-events-none">
                    <UploadCloud
                      className={`mx-auto h-8 w-8 ${
                        isDragActive ? "text-signal-amber" : "text-bone-dim"
                      }`}
                    />
                    <div className="space-y-1">
                      <p className="mono-meta normal-case text-bone">
                        {t("drop")}
                      </p>
                      <p className="mono-meta normal-case">
                        {t("formats")}
                      </p>
                    </div>
                  </div>
                )}
              </div>

              {errorMessage && (
                <div className="border border-seal-red/40 bg-vault-sunk px-3 py-2.5">
                  <p className="font-mono text-xs text-seal-red">⚠ {errorMessage}</p>
                </div>
              )}
              {successMessage && (
                <div className="border border-signal-amber/40 bg-vault-sunk px-3 py-2.5">
                  <p className="font-mono text-xs text-signal-amber">✓ {successMessage}</p>
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <label className="mono-meta block">{t("documentType")}</label>
                  <select
                    value={documentType}
                    onChange={(e) => setDocumentType(e.target.value)}
                    className="w-full bg-vault-raised border border-hairline px-2.5 py-2 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                  >
                    {DOCUMENT_TYPES.map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <label className="mono-meta block">{t("classification")}</label>
                    <ClassificationStamp level={classification} />
                  </div>
                  <select
                    value={classification}
                    onChange={(e) => setClassification(e.target.value)}
                    className="w-full bg-vault-raised border border-hairline px-2.5 py-2 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                  >
                    <option value="public">Public (Unrestricted)</option>
                    <option value="internal">Internal (Default)</option>
                    <option value="restricted">Restricted (High-Confidentiality)</option>
                  </select>
                </div>

                <div className="space-y-1.5">
                  <label className="mono-meta flex items-center gap-1">
                    <User className="h-3 w-3" /> {t("ownerId")}
                  </label>
                  <Input
                    value={ownerId}
                    onChange={(e) => setOwnerId(e.target.value)}
                    placeholder="e.g. user:admin"
                    className="bg-vault-raised border-hairline font-mono text-xs"
                    required
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="mono-meta flex items-center gap-1">
                    <Database className="h-3 w-3" /> {t("tenantId")}
                  </label>
                  <Input
                    value={tenantId}
                    onChange={(e) => setTenantId(e.target.value)}
                    placeholder="e.g. tenant_a"
                    className="bg-vault-raised border-hairline font-mono text-xs"
                    required
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="mono-meta block">{t("titleOverride")}</label>
                  <Input
                    value={titleOverride}
                    onChange={(e) => setTitleOverride(e.target.value)}
                    placeholder="Defaults to filename"
                    className="bg-vault-raised border-hairline font-mono text-xs"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="mono-meta flex items-center gap-1">
                    <Tag className="h-3 w-3" /> {t("policyTags")}
                  </label>
                  <Input
                    value={policyTags}
                    onChange={(e) => setPolicyTags(e.target.value)}
                    placeholder="e.g. finance, hr (comma separated)"
                    className="bg-vault-raised border-hairline font-mono text-xs"
                  />
                </div>
              </div>

              <Button type="submit" disabled={loading || !file} className="w-full gap-2">
                {loading ? (
                  <>
                    <RefreshCw className="h-4 w-4 animate-spin" />
                    <span>{t("submitting")}</span>
                  </>
                ) : (
                  <span>{t("submit")}</span>
                )}
              </Button>
            </form>
          </Panel>
        </div>

        <div className="lg:col-span-2">
          <Panel
            label={t("ledger")}
            headRight={
              jobs.length > 0 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={clearJobsList}
                  className="gap-1.5 text-bone-dim hover:text-seal-red"
                  title={t("clearTitle")}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {t("clear")}
                </Button>
              ) : undefined
            }
          >
            {jobs.length === 0 ? (
              <div className="px-5 py-12 text-center flex flex-col items-center gap-3">
                <Clock className="h-6 w-6 text-bone-dim" />
                <span className="mono-meta">{t("empty")}</span>
              </div>
            ) : (
              <ul className="divide-y divide-hairline max-h-[580px] overflow-y-auto custom-scrollbar">
                {jobs.map((job) => (
                  <li key={job.job_id} className="px-5 py-4 space-y-3">
                    <div className="flex justify-between items-start gap-4">
                      <div className="min-w-0">
                        <p className="text-sm text-bone truncate" title={job.filename}>
                          {job.filename}
                        </p>
                        <p className="mono-meta mt-1 normal-case">
                          {job.fileSize} · {job.created_at}
                        </p>
                      </div>
                      {statusStamp(job.status)}
                    </div>

                    <div className="flex flex-wrap items-center gap-3 font-mono text-[0.625rem]">
                      <span className="text-bone-dim uppercase tracking-wider">
                        {job.document_type}
                      </span>
                      <ClassificationStamp level={job.classification} />
                      <span className="text-bone-dim uppercase tracking-wider">
                        {job.tenant_id}
                      </span>
                    </div>

                    <div className="bg-vault-sunk border border-hairline px-2.5 py-2 space-y-1 font-mono text-[0.625rem] text-bone-dim">
                      <div className="flex justify-between gap-3">
                        <span>{t("jobId")}</span>
                        <span className="text-bone truncate max-w-40">{job.job_id}</span>
                      </div>
                      <div className="flex justify-between gap-3">
                        <span>{t("docId")}</span>
                        <span className="text-bone truncate max-w-40" title={job.document_id}>
                          {job.document_id}
                        </span>
                      </div>
                      {job.error && (
                        <div className="pt-1.5 border-t border-hairline mt-1 leading-normal text-seal-red">
                          error: {job.error}
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
