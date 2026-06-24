"use client";

import React, { useState, useEffect } from "react";
import {
  ShieldAlert,
  Trash2,
  UserPlus,
  RefreshCw,
  Key,
  Users,
  FileText,
  Grid
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel, ClassificationStamp } from "@/components/forensic";
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
    <div className="space-y-6 max-w-7xl mx-auto pb-10">

      {/* Header */}
      <header className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">section 04 · clearance control</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            Clearance Control
          </h1>
        </div>
        <Button
          size="sm"
          onClick={fetchTuples}
          variant="outline"
          className="gap-1.5"
          disabled={loading}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh store
        </Button>
      </header>

      {error && (
        <div className="px-4 py-3 bg-vault-sunk border border-seal-red/40 text-seal-red text-xs flex items-start gap-2 max-w-xl">
          <ShieldAlert className="h-4 w-4 flex-shrink-0 mt-0.5" />
          <div className="space-y-1 font-mono text-[10px]">
            <span className="mono-meta text-seal-red normal-case">FGA communication error</span>
            <p>{error}</p>
            <p className="text-bone-dim">Visualizzazione dei dati mock locali per scopi dimostrativi.</p>
          </div>
        </div>
      )}

      {/* Grid: Grant Form & Active Tuples Table */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">

        {/* Grant Permission Form (1 col) */}
        <Panel
          label="grant relationship"
          headRight={<UserPlus className="h-4 w-4 text-bone-dim" />}
        >
          <div className="px-5 py-3 border-b border-hairline">
            <p className="text-xs text-bone-dim leading-relaxed">
              Write a direct access control tuple into the authorization model.
            </p>
          </div>
          <div className="p-5">
            <form onSubmit={handleGrant} className="space-y-4 text-xs">

              {/* Subject */}
              <div className="space-y-1.5">
                <label className="mono-meta block">
                  Subject (User/Group)
                </label>
                <div className="relative">
                  <Users className="absolute left-2.5 top-2.5 h-4 w-4 text-bone-dim" />
                  <Input
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    placeholder="e.g. user:bob or group:marketing#member"
                    className="pl-9 bg-vault-raised border-hairline text-bone text-xs font-mono h-9 focus-visible:border-signal-amber"
                    required
                  />
                </div>
                <div className="flex gap-2 pt-1">
                  {DEMO_USERS.map((usr) => (
                    <Button
                      key={usr}
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setSubject(usr)}
                      className="h-6 px-2 font-mono text-[10px]"
                    >
                      {usr.replace("user:", "")}
                    </Button>
                  ))}
                </div>
              </div>

              {/* Relation */}
              <div className="space-y-1.5">
                <label className="mono-meta block">
                  Relationship
                </label>
                <select
                  value={relation}
                  onChange={(e) => setRelation(e.target.value)}
                  className="w-full bg-vault-raised border border-hairline p-2 text-xs text-bone font-mono focus:outline-none focus:border-signal-amber"
                >
                  <option value="owner">owner (Full Admin)</option>
                  <option value="direct_viewer">direct_viewer (Explicit User access)</option>
                  <option value="group_viewer">group_viewer (Transitive Group access)</option>
                  <option value="viewer">viewer (Union resolver)</option>
                </select>
              </div>

              {/* Object */}
              <div className="space-y-1.5">
                <label className="mono-meta block">
                  Object (Resource)
                </label>
                <div className="relative">
                  <FileText className="absolute left-2.5 top-2.5 h-4 w-4 text-bone-dim" />
                  <Input
                    value={object}
                    onChange={(e) => setObject(e.target.value)}
                    placeholder="e.g. document:sec_doc_01"
                    className="pl-9 bg-vault-raised border-hairline text-bone text-xs font-mono h-9 focus-visible:border-signal-amber"
                    required
                  />
                </div>
                {matrixDocs.length > 0 && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {matrixDocs.map((doc) => (
                      <Button
                        key={doc}
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setObject(doc)}
                        className="h-6 px-2 font-mono text-[10px] max-w-28 truncate"
                      >
                        {doc.replace("document:", "")}
                      </Button>
                    ))}
                  </div>
                )}
              </div>

              {/* Form Status Messages */}
              {formSuccess && (
                <div className="px-3 py-2.5 bg-vault-sunk border border-signal-amber/40 text-signal-amber text-[10px] leading-normal font-mono">
                  {formSuccess}
                </div>
              )}

              {/* Submit */}
              <Button
                type="submit"
                disabled={submitting}
                className="w-full text-xs h-9"
              >
                {submitting ? "Scrittura tupla…" : "Register Tuple"}
              </Button>

            </form>
          </div>
        </Panel>

        {/* Active Tuples Table (2 cols) */}
        <Panel
          className="lg:col-span-2 min-h-[400px]"
          label={`active authorization tuples · ${tuples.length}`}
          headRight={<Key className="h-4 w-4 text-bone-dim" />}
        >
          <div className="overflow-x-auto custom-scrollbar">
            {loading ? (
              <div className="py-20 flex flex-col items-center gap-2 text-bone-dim">
                <RefreshCw className="h-5 w-5 animate-spin text-signal-amber" />
                <span className="mono-meta">caricamento tuple openfga…</span>
              </div>
            ) : (
              <table className="w-full text-left text-xs border-collapse min-w-[450px]">
                <thead>
                  <tr className="border-b border-hairline text-bone-dim">
                    {["subject", "relation", "object", ""].map((h, i) => (
                      <th
                        key={i}
                        className={`px-4 py-2.5 font-mono text-[0.625rem] uppercase tracking-wider font-medium ${i === 3 ? "text-right" : ""}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {tuples.map((t, idx) => (
                    <tr key={idx} className="hover:bg-[rgba(232,230,223,0.03)] transition-colors">
                      <td className="px-4 py-3 font-mono text-bone truncate max-w-40" title={t.user}>
                        {t.user}
                      </td>
                      <td className="px-4 py-3">
                        <span className={
                          t.relation === "owner"
                            ? "stamp text-seal-red"
                            : t.relation.includes("parent")
                              ? "stamp text-bone-dim"
                              : "stamp text-signal-amber"
                        }>
                          {t.relation}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-bone-dim truncate max-w-44" title={t.object}>
                        {t.object}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleRevoke(t)}
                          className="h-7 w-7 text-bone-dim hover:text-seal-red"
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
          </div>
        </Panel>
      </div>

      {/* User-Document Authorization Matrix */}
      <Panel
        label="user-document clearance grid"
        headRight={<Grid className="h-4 w-4 text-bone-dim" />}
      >
        <div className="px-5 py-3 border-b border-hairline">
          <p className="text-xs text-bone-dim leading-relaxed">
            Computed accessibility derived from tuples. A granted cell carries a clearance stamp; a withheld cell is left blank.
          </p>
        </div>
        <div className="overflow-x-auto custom-scrollbar">
          <table className="w-full text-center text-xs border-collapse min-w-[600px]">
            <thead>
              <tr className="border-b border-hairline text-bone-dim">
                <th className="px-4 py-2.5 text-left font-mono text-[0.625rem] uppercase tracking-wider font-medium">
                  fga subject
                </th>
                {matrixDocs.map((doc) => (
                  <th key={doc} className="px-4 py-2.5 font-medium">
                    <ClassificationStamp level="restricted" className="mx-auto" />
                    <span className="block mt-1.5 font-mono text-[0.625rem] uppercase tracking-wider text-bone-dim">
                      {doc.replace("document:", "")}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {matrixUsers.map((user) => (
                <tr key={user} className="hover:bg-[rgba(232,230,223,0.03)] transition-colors">
                  <td className="px-4 py-3 text-left font-mono text-bone">
                    {user}
                  </td>
                  {matrixDocs.map((doc) => {
                    const relation = checkAccess(user, doc);
                    const restricted = relation === "owner";
                    return (
                      <td key={doc} className="px-4 py-3">
                        {relation ? (
                          <span className={restricted ? "stamp text-seal-red" : "stamp text-signal-amber"}>
                            ✓ {relation.toUpperCase()}
                          </span>
                        ) : (
                          <span className="font-mono text-bone-dim/50">—</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

    </div>
  );
}
