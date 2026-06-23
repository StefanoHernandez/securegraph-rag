"use client";

import React, { useState, useEffect, useRef } from "react";
import { 
  UploadCloud, 
  FileText, 
  X, 
  CheckCircle2, 
  XCircle, 
  Clock, 
  HelpCircle,
  Database,
  Tag,
  Shield,
  User,
  ExternalLink,
  Trash2,
  RefreshCw,
  FolderOpen
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ingestApi } from "@/lib/api";

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

const CLASSIFICATION_LEVELS = [
  { value: "public", label: "Public", color: "text-accent-emerald bg-accent-emerald/10 border-accent-emerald/20" },
  { value: "internal", label: "Internal", color: "text-accent-indigo bg-accent-indigo/10 border-accent-indigo/20" },
  { value: "restricted", label: "Restricted", color: "text-accent-rose bg-accent-rose/10 border-accent-rose/20" }
];

export default function IngestPage() {
  const [file, setFile] = useState<File | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Form Fields
  const [tenantId, setTenantId] = useState("default");
  const [ownerId, setOwnerId] = useState("user:admin");
  const [documentType, setDocumentType] = useState("policy");
  const [classification, setClassification] = useState("internal");
  const [policyTags, setPolicyTags] = useState("");
  const [titleOverride, setTitleOverride] = useState("");

  // Jobs Tracker
  const [jobs, setJobs] = useState<IngestionTrackerJob[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load jobs from localStorage
  useEffect(() => {
    const savedJobs = localStorage.getItem("securgraph_ingest_jobs");
    if (savedJobs) {
      try {
        setJobs(JSON.parse(savedJobs));
      } catch (e) {
        console.error("Failed to parse ingestion jobs from localStorage", e);
      }
    }

    // Load user context to default tenant/owner
    const userStr = localStorage.getItem("securgraph_user");
    const storedTenant = localStorage.getItem("securgraph_tenant");
    if (userStr) {
      try {
        const parsed = JSON.parse(userStr);
        setTenantId(storedTenant || parsed.tenant_id || "default");
        setOwnerId(`user:${parsed.id || parsed.username || "admin"}`);
      } catch (e) {
        console.error(e);
      }
    }
  }, []);

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
      setErrorMessage("Errore: Il file supera la dimensione massima consentita di 50MB.");
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
      setErrorMessage("Per favore seleziona un file da caricare.");
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
      setSuccessMessage(`File caricato con successo! Job ID: ${result.job_id}. Il file è in fase di indicizzazione.`);
      setFile(null);
      setTitleOverride("");
      setPolicyTags("");
    } catch (err: any) {
      setErrorMessage(err?.message || "Upload fallito. Verifica la connessione al server.");
    } finally {
      setLoading(false);
    }
  };

  const clearJobsList = () => {
    saveJobsToStorage([]);
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "completed":
        return (
          <Badge className="bg-accent-emerald/10 text-accent-emerald border-accent-emerald/20 hover:bg-accent-emerald/20 flex items-center gap-1 text-[10px] w-fit">
            <CheckCircle2 className="h-3 w-3" /> Completed
          </Badge>
        );
      case "failed":
        return (
          <Badge className="bg-accent-rose/10 text-accent-rose border-accent-rose/20 hover:bg-accent-rose/20 flex items-center gap-1 text-[10px] w-fit">
            <XCircle className="h-3 w-3" /> Failed
          </Badge>
        );
      case "running":
        return (
          <Badge className="bg-accent-cyan/10 text-accent-cyan border-accent-cyan/20 hover:bg-accent-cyan/20 flex items-center gap-1 text-[10px] w-fit animate-pulse">
            <RefreshCw className="h-3 w-3 animate-spin" /> Processing
          </Badge>
        );
      default:
        return (
          <Badge className="bg-accent-amber/10 text-accent-amber border-accent-amber/20 hover:bg-accent-amber/20 flex items-center gap-1 text-[10px] w-fit">
            <Clock className="h-3 w-3" /> Queued
          </Badge>
        );
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 max-w-7xl mx-auto items-start">
      
      {/* Left: Upload and Form Controls (3 columns) */}
      <div className="lg:col-span-3 space-y-6">
        <Card className="glassmorphism border-border/50 text-foreground">
          <CardHeader>
            <CardTitle className="text-lg font-black flex items-center gap-2">
              <UploadCloud className="h-5 w-5 text-accent-indigo" />
              Document Ingestion Portal
            </CardTitle>
            <CardDescription className="text-xs">
              Upload files to index them into the SecurGraph Vector store & Neo4j. ReBAC policies will be generated automatically.
            </CardDescription>
          </CardHeader>
          
          <CardContent>
            <form onSubmit={handleIngest} className="space-y-6">
              
              {/* Drag and Drop Zone */}
              <div
                onDragEnter={handleDrag}
                onDragOver={handleDrag}
                onDragLeave={handleDrag}
                onDrop={handleDrop}
                onClick={triggerFileSelect}
                className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition flex flex-col items-center justify-center min-h-48 ${
                  isDragActive
                    ? "border-accent-indigo bg-accent-indigo/5"
                    : file
                      ? "border-accent-emerald/40 bg-accent-emerald/5"
                      : "border-border/40 bg-black/20 hover:bg-white/5 hover:border-border/70"
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  onChange={handleFileChange}
                  accept=".txt,.pdf,.md,.csv,.json"
                  className="hidden"
                />

                {file ? (
                  <div className="space-y-4 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-between p-3.5 bg-black/40 border border-border/50 rounded-xl">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="p-2 bg-accent-emerald/10 text-accent-emerald rounded-lg flex-shrink-0">
                          <FileText className="h-6 w-6" />
                        </div>
                        <div className="text-left min-w-0">
                          <p className="text-xs font-bold truncate text-foreground">{file.name}</p>
                          <p className="text-[10px] text-foreground/45 font-mono">{formatBytes(file.size)}</p>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={removeFile}
                        className="p-1 rounded-lg hover:bg-white/5 text-foreground/40 hover:text-accent-rose transition"
                      >
                        <X className="h-4.5 w-4.5" />
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2 pointer-events-none">
                    <div className="mx-auto h-11 w-11 rounded-full bg-accent-indigo/10 border border-accent-indigo/25 flex items-center justify-center text-accent-indigo">
                      <UploadCloud className="h-5 w-5" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs font-bold text-foreground">
                        Sfoglia i file o trascinali qui
                      </p>
                      <p className="text-[10px] text-foreground/45">
                        PDF, TXT, MD, CSV, JSON fino a 50MB
                      </p>
                    </div>
                  </div>
                )}
              </div>

              {/* Feedback messages */}
              {errorMessage && (
                <div className="p-3 bg-accent-rose/10 border border-accent-rose/25 text-accent-rose rounded-lg text-xs flex items-start gap-2">
                  <XCircle className="h-4.5 w-4.5 flex-shrink-0 mt-0.5" />
                  <span>{errorMessage}</span>
                </div>
              )}
              {successMessage && (
                <div className="p-3 bg-accent-emerald/10 border border-accent-emerald/25 text-accent-emerald rounded-lg text-xs flex items-start gap-2">
                  <CheckCircle2 className="h-4.5 w-4.5 flex-shrink-0 mt-0.5" />
                  <span>{successMessage}</span>
                </div>
              )}

              {/* Metadata Form Fields */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                
                {/* Document Type */}
                <div className="space-y-1.5">
                  <label className="text-[10px] font-mono text-foreground/50 uppercase tracking-wider block">
                    Document Type
                  </label>
                  <select
                    value={documentType}
                    onChange={(e) => setDocumentType(e.target.value)}
                    className="w-full bg-black/45 border border-border/40 rounded-lg p-2.5 text-xs text-foreground font-sans focus:outline-none focus:border-accent-indigo"
                  >
                    {DOCUMENT_TYPES.map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>

                {/* Classification Level */}
                <div className="space-y-1.5">
                  <label className="text-[10px] font-mono text-foreground/50 uppercase tracking-wider block">
                    Classification Level
                  </label>
                  <select
                    value={classification}
                    onChange={(e) => setClassification(e.target.value)}
                    className="w-full bg-black/45 border border-border/40 rounded-lg p-2.5 text-xs text-foreground font-sans focus:outline-none focus:border-accent-indigo"
                  >
                    <option value="public">Public (Unrestricted)</option>
                    <option value="internal">Internal (Default)</option>
                    <option value="restricted">Restricted (High-Confidentiality)</option>
                  </select>
                </div>

                {/* Owner ID */}
                <div className="space-y-1.5">
                  <div className="flex items-center gap-1 text-[10px] font-mono text-foreground/50 uppercase tracking-wider">
                    <User className="h-3.5 w-3.5" /> Owner ID (FGA)
                  </div>
                  <Input
                    value={ownerId}
                    onChange={(e) => setOwnerId(e.target.value)}
                    placeholder="e.g. user:admin"
                    className="bg-black/45 border-border/40 text-xs focus-visible:ring-accent-indigo/50 h-9"
                    required
                  />
                </div>

                {/* Tenant ID */}
                <div className="space-y-1.5">
                  <div className="flex items-center gap-1 text-[10px] font-mono text-foreground/50 uppercase tracking-wider">
                    <Database className="h-3.5 w-3.5" /> Tenant ID
                  </div>
                  <Input
                    value={tenantId}
                    onChange={(e) => setTenantId(e.target.value)}
                    placeholder="e.g. tenant_a"
                    className="bg-black/45 border-border/40 text-xs focus-visible:ring-accent-indigo/50 h-9"
                    required
                  />
                </div>

                {/* Title Override (Optional) */}
                <div className="space-y-1.5">
                  <label className="text-[10px] font-mono text-foreground/50 uppercase tracking-wider block">
                    Title Override (Optional)
                  </label>
                  <Input
                    value={titleOverride}
                    onChange={(e) => setTitleOverride(e.target.value)}
                    placeholder="Defaults to filename"
                    className="bg-black/45 border-border/40 text-xs focus-visible:ring-accent-indigo/50 h-9"
                  />
                </div>

                {/* Policy Tags */}
                <div className="space-y-1.5">
                  <div className="flex items-center gap-1 text-[10px] font-mono text-foreground/50 uppercase tracking-wider">
                    <Tag className="h-3.5 w-3.5" /> Policy Tags (ReBAC)
                  </div>
                  <Input
                    value={policyTags}
                    onChange={(e) => setPolicyTags(e.target.value)}
                    placeholder="e.g. finance, hr (comma separated)"
                    className="bg-black/45 border-border/40 text-xs focus-visible:ring-accent-indigo/50 h-9"
                  />
                </div>

              </div>

              {/* Submit Button */}
              <Button
                type="submit"
                disabled={loading || !file}
                className="w-full bg-gradient-to-r from-accent-indigo via-accent-cyan to-accent-emerald text-black font-bold hover:opacity-90 transition h-10 shadow-lg cursor-pointer"
              >
                {loading ? (
                  <div className="flex items-center gap-2">
                    <RefreshCw className="h-4.5 w-4.5 animate-spin" />
                    <span>Inizializzazione Ingest Pipeline...</span>
                  </div>
                ) : (
                  <span>Carica ed Indicizza File</span>
                )}
              </Button>

            </form>
          </CardContent>
        </Card>
      </div>

      {/* Right: Ingestion Job History & Polling Status (2 columns) */}
      <div className="lg:col-span-2 space-y-6">
        <Card className="glassmorphism border-border/50 text-foreground">
          <CardHeader className="pb-3 border-b border-border/10 flex flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-sm font-bold flex items-center gap-1.5">
                <FolderOpen className="h-4.5 w-4.5 text-accent-cyan" />
                Ingestion Monitor
              </CardTitle>
              <CardDescription className="text-[10px]">
                Real-time ingestion task logs.
              </CardDescription>
            </div>
            {jobs.length > 0 && (
              <Button
                variant="ghost"
                size="icon"
                onClick={clearJobsList}
                className="h-8 w-8 text-foreground/50 hover:text-accent-rose hover:bg-accent-rose/10 rounded-lg transition"
                title="Clear job logs history"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
          </CardHeader>
          
          <CardContent className="p-0">
            {jobs.length === 0 ? (
              <div className="p-8 text-center text-xs text-foreground/45 flex flex-col items-center justify-center gap-3">
                <Clock className="h-8 w-8 text-foreground/20 animate-pulse" />
                <span>Nessun caricamento recente.</span>
              </div>
            ) : (
              <div className="divide-y divide-border/10 max-h-[580px] overflow-y-auto custom-scrollbar">
                {jobs.map((job) => {
                  const classLevelObj = CLASSIFICATION_LEVELS.find(l => l.value === job.classification);
                  return (
                    <div key={job.job_id} className="p-4 space-y-3.5 text-xs">
                      {/* Top row: Filename + Status */}
                      <div className="flex justify-between items-start gap-4">
                        <div className="min-w-0">
                          <p className="font-bold text-foreground truncate" title={job.filename}>
                            {job.filename}
                          </p>
                          <p className="text-[9px] text-foreground/40 font-mono">
                            Size: {job.fileSize} • {job.created_at}
                          </p>
                        </div>
                        {getStatusBadge(job.status)}
                      </div>

                      {/* Middle row: Metadata tags */}
                      <div className="flex flex-wrap gap-2 text-[9px] font-mono">
                        <Badge variant="outline" className="border-border/30 text-foreground/60 h-5">
                          {job.document_type}
                        </Badge>
                        <Badge variant="outline" className={`${classLevelObj?.color || "text-foreground border-border/30"} h-5 font-bold uppercase tracking-wider`}>
                          {job.classification}
                        </Badge>
                        <Badge variant="outline" className="border-border/30 text-accent-cyan/85 bg-accent-cyan/5 h-5">
                          {job.tenant_id}
                        </Badge>
                      </div>

                      {/* Bottom details / link */}
                      <div className="bg-black/20 rounded-lg p-2.5 space-y-1 font-mono text-[9px] text-foreground/50 border border-border/10">
                        <div className="flex justify-between">
                          <span>Job ID:</span>
                          <span className="text-foreground/80 truncate max-w-40">{job.job_id}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Doc ID:</span>
                          <span className="text-foreground/80 truncate max-w-40" title={job.document_id}>{job.document_id}</span>
                        </div>
                        {job.error && (
                          <div className="pt-1.5 text-accent-rose text-left leading-normal border-t border-border/10 mt-1">
                            <b>Error:</b> {job.error}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

    </div>
  );
}
