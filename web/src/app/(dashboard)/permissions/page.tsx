"use client";

import React, { useState, useEffect } from "react";
import { 
  ShieldAlert, 
  ShieldCheck, 
  Plus, 
  Trash2, 
  UserPlus, 
  RefreshCw, 
  Lock, 
  Unlock,
  Key,
  Users,
  FileText,
  Sliders,
  Settings,
  Grid
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { adminApi } from "@/lib/api";

interface FGATuple {
  user: string;
  relation: string;
  object: string;
}

const DEMO_USERS = ["user:admin", "user:analyst", "user:viewer"];
const DEMO_RELATIONS = ["owner", "direct_viewer", "viewer", "group_viewer"];

export default function PermissionsPage() {
  const [tuples, setTuples] = useState<FGATuple[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Grant Form State
  const [subject, setSubject] = useState("user:viewer");
  const [relation, setRelation] = useState("direct_viewer");
  const [object, setObject] = useState("document:");
  const [submitting, setSubmitting] = useState(false);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);

  // Matrix State
  const [matrixUsers, setMatrixUsers] = useState<string[]>(DEMO_USERS);
  const [matrixDocs, setMatrixDocs] = useState<string[]>([]);

  useEffect(() => {
    fetchTuples();
  }, []);

  const fetchTuples = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminApi.listPermissions();
      setTuples(res.tuples);

      // Extract unique documents from tuples to build the matrix
      const docs = Array.from(
        new Set(
          res.tuples
            .filter(t => t.object.startsWith("document:"))
            .map(t => t.object)
        )
      );
      setMatrixDocs(docs.length > 0 ? docs : ["document:sec_doc_01", "document:sec_doc_02"]);
    } catch (err: any) {
      console.error(err);
      setError(err?.message || "Failed to retrieve FGA tuples from the store.");
      // Se non si connette a FGA, mockiamo un po' di tuple per fare una UI premium utilizzabile
      const mockTuples = [
        { user: "user:admin", relation: "owner", object: "document:sec_doc_01" },
        { user: "user:analyst", relation: "direct_viewer", object: "document:sec_doc_01" },
        { user: "user:viewer", relation: "direct_viewer", object: "document:sec_doc_02" },
        { user: "document:sec_doc_01", relation: "parent_document", object: "chunk:chk_1" },
        { user: "document:sec_doc_01", relation: "parent_document", object: "chunk:chk_2" }
      ];
      setTuples(mockTuples);
      setMatrixDocs(["document:sec_doc_01", "document:sec_doc_02"]);
    } finally {
      setLoading(false);
    }
  };

  const handleGrant = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!subject || !relation || !object) {
      setError("Compila tutti i campi.");
      return;
    }

    setSubmitting(true);
    setError(null);
    setFormSuccess(null);

    try {
      await adminApi.grantPermission(subject.trim(), relation, object.trim());
      setFormSuccess(`Permesso registrato! ${subject} è ora ${relation} di ${object}`);
      fetchTuples();
    } catch (err: any) {
      setError(err?.message || "Impossibile scrivere la relazione FGA.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async (tuple: FGATuple) => {
    if (!confirm(`Sei sicuro di voler revocare il permesso: ${tuple.user} -> ${tuple.relation} -> ${tuple.object}?`)) {
      return;
    }

    setLoading(true);
    try {
      await adminApi.revokePermission(tuple.user, tuple.relation, tuple.object);
      fetchTuples();
    } catch (err: any) {
      setError(err?.message || "Impossibile revocare la relazione FGA.");
      setLoading(false);
    }
  };

  // Check if a user has access to a document (based on tuples)
  const checkAccess = (user: string, doc: string) => {
    // Admin always has owner/viewer access in the mock/actual tuples
    if (user === "user:admin") return "owner";
    
    const matched = tuples.find(t => t.user === user && t.object === doc);
    return matched ? matched.relation : null;
  };

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-10">
      
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border/20 pb-4">
        <div>
          <h1 className="text-xl md:text-2xl font-black text-foreground">
            Permissions Control Center
          </h1>
          <p className="text-[10px] text-foreground/45 font-mono">
            Relationship-Based Access Control (ReBAC) powered by OpenFGA
          </p>
        </div>
        <Button
          size="sm"
          onClick={fetchTuples}
          variant="outline"
          className="border-border/40 hover:bg-white/5 cursor-pointer h-8 text-xs font-bold"
          disabled={loading}
        >
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
          Refresh store
        </Button>
      </div>

      {error && (
        <div className="p-3 bg-accent-rose/10 border border-accent-rose/25 text-accent-rose rounded-lg text-xs flex items-start gap-2 max-w-xl">
          <ShieldAlert className="h-4.5 w-4.5 flex-shrink-0 mt-0.5" />
          <div className="space-y-1 font-mono text-[10px]">
            <span className="font-bold font-sans text-xs">Errore di comunicazione FGA</span>
            <p>{error}</p>
            <p className="text-foreground/50 font-sans">Visualizzazione dei dati mock locali per scopi dimostrativi.</p>
          </div>
        </div>
      )}

      {/* Grid: Grant Form & Active Tuples Table */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        
        {/* Grant Permission Form (1 col) */}
        <Card className="glassmorphism border-border/50 text-foreground">
          <CardHeader className="pb-3 border-b border-border/10">
            <CardTitle className="text-sm font-bold flex items-center gap-1.5">
              <UserPlus className="h-4.5 w-4.5 text-accent-indigo" />
              Grant Relationship
            </CardTitle>
            <CardDescription className="text-[10px]">
              Write a direct access control tuple into the authorization model.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-4 pt-5">
            <form onSubmit={handleGrant} className="space-y-4 text-xs">
              
              {/* Subject */}
              <div className="space-y-1.5">
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Subject (User/Group)
                </label>
                <div className="relative">
                  <Users className="absolute left-2.5 top-2.5 h-4 w-4 text-foreground/40" />
                  <Input
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    placeholder="e.g. user:bob or group:marketing#member"
                    className="pl-9 bg-black/45 border-border/40 text-xs focus-visible:ring-accent-indigo/50 h-9"
                    required
                  />
                </div>
                <div className="flex gap-2 pt-1">
                  {DEMO_USERS.map((usr) => (
                    <button
                      key={usr}
                      type="button"
                      onClick={() => setSubject(usr)}
                      className="text-[9px] font-mono bg-white/5 border border-white/10 hover:border-accent-indigo/50 hover:bg-accent-indigo/5 px-2 py-0.5 rounded text-foreground/75"
                    >
                      {usr.replace("user:", "")}
                    </button>
                  ))}
                </div>
              </div>

              {/* Relation */}
              <div className="space-y-1.5">
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Relationship
                </label>
                <select
                  value={relation}
                  onChange={(e) => setRelation(e.target.value)}
                  className="w-full bg-black/45 border border-border/40 rounded-lg p-2 text-xs text-foreground font-sans focus:outline-none focus:border-accent-indigo"
                >
                  <option value="owner">owner (Full Admin)</option>
                  <option value="direct_viewer">direct_viewer (Explicit User access)</option>
                  <option value="group_viewer">group_viewer (Transitive Group access)</option>
                  <option value="viewer">viewer (Union resolver)</option>
                </select>
              </div>

              {/* Object */}
              <div className="space-y-1.5">
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Object (Resource)
                </label>
                <div className="relative">
                  <FileText className="absolute left-2.5 top-2.5 h-4 w-4 text-foreground/40" />
                  <Input
                    value={object}
                    onChange={(e) => setObject(e.target.value)}
                    placeholder="e.g. document:sec_doc_01"
                    className="pl-9 bg-black/45 border-border/40 text-xs focus-visible:ring-accent-indigo/50 h-9"
                    required
                  />
                </div>
                {matrixDocs.length > 0 && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {matrixDocs.map((doc) => (
                      <button
                        key={doc}
                        type="button"
                        onClick={() => setObject(doc)}
                        className="text-[9px] font-mono bg-white/5 border border-white/10 hover:border-accent-indigo/50 hover:bg-accent-indigo/5 px-2 py-0.5 rounded text-foreground/75 truncate max-w-28"
                      >
                        {doc.replace("document:", "")}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Form Status Messages */}
              {formSuccess && (
                <div className="p-3 bg-accent-emerald/10 border border-accent-emerald/25 text-accent-emerald rounded-lg text-[10px] leading-normal font-sans">
                  {formSuccess}
                </div>
              )}

              {/* Submit */}
              <Button
                type="submit"
                disabled={submitting}
                className="w-full bg-accent-indigo hover:opacity-90 text-black font-bold text-xs h-9 cursor-pointer"
              >
                {submitting ? "Scrittura tupla..." : "Register Tuple"}
              </Button>

            </form>
          </CardContent>
        </Card>

        {/* Active Tuples Table (2 cols) */}
        <Card className="lg:col-span-2 glassmorphism border-border/50 text-foreground min-h-[400px]">
          <CardHeader className="pb-3 border-b border-border/10">
            <CardTitle className="text-sm font-bold flex items-center gap-1.5">
              <Key className="h-4.5 w-4.5 text-accent-cyan" />
              Active Authorization Tuples ({tuples.length})
            </CardTitle>
            <CardDescription className="text-xs">
              Raw relationship logs stored inside the FGA container.
            </CardDescription>
          </CardHeader>
          
          <CardContent className="p-0 overflow-x-auto custom-scrollbar">
            {loading ? (
              <div className="py-20 text-center text-xs text-foreground/45 flex flex-col items-center justify-center gap-2">
                <RefreshCw className="h-6 w-6 animate-spin text-accent-cyan" />
                <span>Caricamento delle tuple OpenFGA...</span>
              </div>
            ) : (
              <table className="w-full text-left text-xs font-mono border-collapse min-w-[450px]">
                <thead>
                  <tr className="bg-black/30 border-b border-border/10 text-foreground/50 text-[10px] uppercase font-bold tracking-wider">
                    <th className="p-4">Subject</th>
                    <th className="p-4">Relation</th>
                    <th className="p-4">Object</th>
                    <th className="p-4 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/10">
                  {tuples.map((t, idx) => (
                    <tr key={idx} className="hover:bg-white/5 transition">
                      <td className="p-4 text-foreground/90 font-bold truncate max-w-40" title={t.user}>
                        {t.user}
                      </td>
                      <td className="p-4">
                        <Badge className={`text-[9px] font-bold ${
                          t.relation === "owner" 
                            ? "bg-accent-rose/10 text-accent-rose border-accent-rose/25" 
                            : t.relation.includes("parent")
                              ? "bg-foreground/10 text-foreground/70 border-border/40"
                              : "bg-accent-indigo/10 text-accent-indigo border-accent-indigo/25"
                        }`}>
                          {t.relation}
                        </Badge>
                      </td>
                      <td className="p-4 text-foreground/80 truncate max-w-44" title={t.object}>
                        {t.object}
                      </td>
                      <td className="p-4 text-right">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleRevoke(t)}
                          className="h-7 w-7 text-foreground/45 hover:text-accent-rose hover:bg-accent-rose/10 rounded-md transition cursor-pointer"
                          title="Revoke relationship tuple"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>

      {/* User-Document Authorization Matrix */}
      <Card className="glassmorphism border-border/50 text-foreground">
        <CardHeader className="pb-3 border-b border-border/10">
          <CardTitle className="text-sm font-bold flex items-center gap-1.5">
            <Grid className="h-4.5 w-4.5 text-accent-emerald" />
            User-Document Authorization Matrix
          </CardTitle>
          <CardDescription className="text-xs">
            A computed visual matrix representing accessibility status derived from tuples.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0 overflow-x-auto custom-scrollbar">
          <table className="w-full text-center text-xs font-mono border-collapse min-w-[600px]">
            <thead>
              <tr className="bg-black/30 border-b border-border/10 text-foreground/50 text-[10px] uppercase font-bold tracking-wider">
                <th className="p-4 text-left">FGA User / Subject</th>
                {matrixDocs.map((doc) => (
                  <th key={doc} className="p-4 font-bold text-foreground">
                    {doc.replace("document:", "")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/10">
              {matrixUsers.map((user) => (
                <tr key={user} className="hover:bg-white/5 transition">
                  <td className="p-4 text-left font-bold text-foreground/90 flex items-center gap-1.5">
                    <div className="h-2 w-2 rounded-full bg-accent-indigo animate-pulse"></div>
                    {user}
                  </td>
                  {matrixDocs.map((doc) => {
                    const relation = checkAccess(user, doc);
                    return (
                      <td key={doc} className="p-4">
                        {relation ? (
                          <div className="inline-flex items-center gap-1 text-[10px] px-2.5 py-1 rounded-full bg-accent-emerald/10 text-accent-emerald border border-accent-emerald/20 font-bold uppercase">
                            <Unlock className="h-3.5 w-3.5" />
                            Granted ({relation})
                          </div>
                        ) : (
                          <div className="inline-flex items-center gap-1 text-[10px] px-2.5 py-1 rounded-full bg-accent-rose/10 text-accent-rose border border-accent-rose/20 font-bold uppercase">
                            <Lock className="h-3.5 w-3.5" />
                            Denied
                          </div>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

    </div>
  );
}
