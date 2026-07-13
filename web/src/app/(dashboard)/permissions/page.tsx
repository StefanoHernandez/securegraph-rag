"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
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
import { SuggestInput } from "@/components/ui/suggest-input";
import { Panel, ClassificationStamp } from "@/components/forensic";
import { adminApi, controlPlaneApi, tenantApi } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import {
  documentToFgaObject,
  loadGroupSubjectsForTenant,
  searchUserSubjects,
  uniqueStrings,
} from "@/lib/suggestions";
import type { Suggestion } from "@/components/ui/suggest-input";

interface FGATuple {
  user: string;
  relation: string;
  object: string;
}

export default function PermissionsPage() {
  const t = useTranslations("permissionsPage");
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
  const [matrixUsers, setMatrixUsers] = useState<string[]>([]);
  const [matrixDocs, setMatrixDocs] = useState<string[]>([]);
  const [subjectSuggestions, setSubjectSuggestions] = useState<Suggestion[]>([]);
  const [objectSuggestions, setObjectSuggestions] = useState<Suggestion[]>([]);

  const fetchTuples = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminApi.listPermissions();
      setTuples(res.tuples);

      // Build the matrix axes from the real tuples only.
      const docs = Array.from(
        new Set(
          res.tuples
            .filter(t => t.object.startsWith("document:"))
            .map(t => t.object)
        )
      );
      setMatrixDocs(docs);

      const users = Array.from(
        new Set(
          res.tuples
            .filter(t => t.user.startsWith("user:"))
            .map(t => t.user)
        )
      );
      setMatrixUsers(users);
      setSubjectSuggestions(
        users.map((user) => ({
          value: user,
          label: user.replace(/^user:/, ""),
        }))
      );
      setObjectSuggestions(
        docs.map((doc) => documentToFgaObject(doc))
      );
    } catch (err: unknown) {
      console.error(err);
      setError(
        getErrorMessage(err, t("loadError"))
      );
      // No fake fallback: on error show an empty list + the error banner
      // instead of fabricating permission tuples.
      setTuples([]);
      setMatrixDocs([]);
      setMatrixUsers([]);
      setSubjectSuggestions([]);
      setObjectSuggestions([]);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchTuples(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchTuples]);

  useEffect(() => {
    let cancelled = false;
    const loadCatalog = async () => {
      try {
        const [users, tenants] = await Promise.all([
          controlPlaneApi.searchUsers(""),
          tenantApi.listTenants(),
        ]);
        if (cancelled) return;

        const tenantIds = uniqueStrings(
          tenants.slice(0, 4).map((tenant) => tenant.id)
        );
        const groupBatches = await Promise.all(
          tenantIds.map((tenantId) => loadGroupSubjectsForTenant(tenantId))
        );
        const catalog = [
          ...users.map((user) => ({
            value: `user:${user.id}`,
            label: user.display_name,
            hint: user.username,
          })),
          ...groupBatches.flat(),
        ];
        setSubjectSuggestions((current) => {
          const seen = new Set(current.map((item) => item.value));
          const merged = [...current];
          for (const item of catalog) {
            if (seen.has(item.value)) continue;
            seen.add(item.value);
            merged.push(item);
          }
          return merged;
        });
      } catch {
        // Tuple-derived suggestions remain available.
      }
    };
    void loadCatalog();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleGrant = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!subject || !relation || !object) {
      setError(t("fillFields"));
      return;
    }

    setSubmitting(true);
    setError(null);
    setFormSuccess(null);

    try {
      await adminApi.grantPermission(subject.trim(), relation, object.trim());
      setFormSuccess(t("grantSuccess", { subject, relation, object }));
      await fetchTuples();
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("grantError")));
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async (tuple: FGATuple) => {
    if (
      !confirm(
        t("revokeConfirm", {
          object: tuple.object,
          relation: tuple.relation,
          user: tuple.user,
        })
      )
    ) {
      return;
    }

    setLoading(true);
    try {
      await adminApi.revokePermission(tuple.user, tuple.relation, tuple.object);
      await fetchTuples();
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("revokeError")));
      setLoading(false);
    }
  };

  // Relation shown in the matrix: strictly what the live tuples say.
  // No client-side special cases — OpenFGA is the only source of truth,
  // and inherited/indirect access is deliberately not simulated here.
  const checkAccess = (user: string, doc: string) => {
    const matched = tuples.find(t => t.user === user && t.object === doc);
    return matched ? matched.relation : null;
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-10">

      <header className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">{t("eyebrow")}</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            {t("title")}
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
          {t("refresh")}
        </Button>
      </header>

      {error && (
        <div className="px-4 py-3 bg-vault-sunk border border-seal-red/40 text-seal-red text-xs flex items-start gap-2 max-w-xl">
          <ShieldAlert className="h-4 w-4 flex-shrink-0 mt-0.5" />
          <div className="space-y-1 font-mono text-[10px]">
            <span className="mono-meta text-seal-red normal-case">{t("error")}</span>
            <p>{error}</p>
            <p className="text-bone-dim">{t("errorHint")}</p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">

        <Panel
          label={t("grantRelationship")}
          headRight={<UserPlus className="h-4 w-4 text-bone-dim" />}
        >
          <div className="px-5 py-3 border-b border-hairline">
            <p className="text-xs text-bone-dim leading-relaxed">
              {t("grantHelp")}
            </p>
          </div>
          <div className="p-5">
            <form onSubmit={handleGrant} className="space-y-4 text-xs">

              <div className="space-y-1.5">
                <label className="mono-meta block">
                  {t("subject")}
                </label>
                <SuggestInput
                  value={subject}
                  onChange={setSubject}
                  suggestions={subjectSuggestions}
                  onQuery={searchUserSubjects}
                  placeholder="e.g. user:bob or group:marketing#member"
                  inputClassName="pl-9 bg-vault-raised border-hairline text-bone text-xs font-mono h-9 focus-visible:border-signal-amber"
                  required
                  leftIcon={<Users className="h-4 w-4" />}
                  emptyLabel={t("noSubjectMatches")}
                />
              </div>

              <div className="space-y-1.5">
                <label className="mono-meta block">
                  {t("relationship")}
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

              <div className="space-y-1.5">
                <label className="mono-meta block">
                  {t("object")}
                </label>
                <SuggestInput
                  value={object}
                  onChange={setObject}
                  suggestions={objectSuggestions}
                  placeholder="e.g. document:sec_doc_01"
                  inputClassName="pl-9 bg-vault-raised border-hairline text-bone text-xs font-mono h-9 focus-visible:border-signal-amber"
                  required
                  leftIcon={<FileText className="h-4 w-4" />}
                  emptyLabel={t("noObjectMatches")}
                />
              </div>

              {formSuccess && (
                <div className="px-3 py-2.5 bg-vault-sunk border border-signal-amber/40 text-signal-amber text-[10px] leading-normal font-mono">
                  {formSuccess}
                </div>
              )}

              <Button
                type="submit"
                disabled={submitting}
                className="w-full text-xs h-9"
              >
                {submitting ? t("writingTuple") : t("registerTuple")}
              </Button>

            </form>
          </div>
        </Panel>

        <Panel
          className="lg:col-span-2 min-h-[400px]"
          label={t("activeTuples", { count: tuples.length })}
          headRight={<Key className="h-4 w-4 text-bone-dim" />}
        >
          <div className="overflow-x-auto custom-scrollbar">
            {loading ? (
              <div className="py-20 flex flex-col items-center gap-2 text-bone-dim">
                <RefreshCw className="h-5 w-5 animate-spin text-signal-amber" />
                <span className="mono-meta">{t("loadingTuples")}</span>
              </div>
            ) : (
              <table className="w-full text-left text-xs border-collapse min-w-[450px]">
                <thead>
                  <tr className="border-b border-hairline text-bone-dim">
                    {[
                      t("subjectColumn"),
                      t("relationColumn"),
                      t("objectColumn"),
                      "",
                    ].map((h, i) => (
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
                  {tuples.map((tuple, idx) => (
                    <tr key={idx} className="hover:bg-[rgba(232,230,223,0.03)] transition-colors">
                      <td className="px-4 py-3 font-mono text-bone truncate max-w-40" title={tuple.user}>
                        {tuple.user}
                      </td>
                      <td className="px-4 py-3">
                        <span className={
                          tuple.relation === "owner"
                            ? "stamp text-seal-red"
                            : tuple.relation.includes("parent")
                              ? "stamp text-bone-dim"
                              : "stamp text-signal-amber"
                        }>
                          {tuple.relation}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-bone-dim truncate max-w-44" title={tuple.object}>
                        {tuple.object}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleRevoke(tuple)}
                          className="h-7 w-7 text-bone-dim hover:text-seal-red"
                          title={t("revokeTuple")}
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

      <Panel
        label={t("grid")}
        headRight={<Grid className="h-4 w-4 text-bone-dim" />}
      >
        <div className="px-5 py-3 border-b border-hairline">
          <p className="text-xs text-bone-dim leading-relaxed">
            {t("gridHelp")}
          </p>
        </div>
        <div className="overflow-x-auto custom-scrollbar">
          <table className="w-full text-center text-xs border-collapse min-w-[600px]">
            <thead>
              <tr className="border-b border-hairline text-bone-dim">
                <th className="px-4 py-2.5 text-left font-mono text-[0.625rem] uppercase tracking-wider font-medium">
                  {t("fgaSubject")}
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
