"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { KeyRound, Link2, Plus, RefreshCw, ShieldCheck, UserX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SuggestInput } from "@/components/ui/suggest-input";
import { Panel } from "@/components/forensic";
import { controlPlaneApi } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { searchUserIds } from "@/lib/suggestions";
import { TenantRole, TenantUser } from "@/lib/types";
import { useTenant as useTenantSelection } from "@/context/TenantProvider";

const ROLES: TenantRole[] = ["viewer", "analyst", "tenant_admin", "super_admin"];

export default function UsersPage() {
  const t = useTranslations("usersPage");
  const { tenantId, loadingTenants } = useTenantSelection();
  const [users, setUsers] = useState<TenantUser[]>([]);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<TenantRole>("viewer");
  const [selectedExistingUserId, setSelectedExistingUserId] = useState("");
  const [attachRole, setAttachRole] = useState<TenantRole>("viewer");
  const [temporaryPassword, setTemporaryPassword] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadUsers = useCallback(async () => {
    if (!tenantId) {
      setLoading(loadingTenants);
      return;
    }
    setLoading(true);
    try {
      setUsers(await controlPlaneApi.listUsers(tenantId));
      setError(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("loadError")));
    } finally {
      setLoading(false);
    }
  }, [loadingTenants, tenantId, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadUsers(), 0);
    return () => window.clearTimeout(timer);
  }, [loadUsers]);

  const createUser = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!tenantId) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const result = await controlPlaneApi.createUser(tenantId, {
        username,
        display_name: displayName,
        email: email || null,
        role,
      });
      setTemporaryPassword(result.temporary_password);
      setUsername("");
      setDisplayName("");
      setEmail("");
      setRole("viewer");
      await loadUsers();
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("createError")));
    } finally {
      setSaving(false);
    }
  };

  const updateMembership = async (
    user: TenantUser,
    body: { role?: TenantRole; is_active?: boolean }
  ) => {
    if (!tenantId) {
      return;
    }
    setError(null);
    try {
      const updated = await controlPlaneApi.updateUserMembership(tenantId, user.id, body);
      setUsers((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("updateError")));
    }
  };

  const resetPassword = async (user: TenantUser) => {
    if (!tenantId) {
      return;
    }
    setError(null);
    try {
      const result = await controlPlaneApi.resetPassword(tenantId, user.id);
      setTemporaryPassword(result.temporary_password);
      await loadUsers();
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("resetError")));
    }
  };

  const attachExistingUser = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!tenantId || !selectedExistingUserId) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const attached = await controlPlaneApi.attachExistingUser(
        tenantId,
        selectedExistingUserId,
        attachRole
      );
      setUsers((items) => {
        const others = items.filter((item) => item.id !== attached.id);
        return [...others, attached].sort((a, b) => a.username.localeCompare(b.username));
      });
      setSelectedExistingUserId("");
      setAttachRole("viewer");
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("attachError")));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6 pb-10">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">{t("eyebrow")}</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            {t("title")}
          </h1>
        </div>
        <Button size="sm" onClick={loadUsers} disabled={loading || !tenantId} className="gap-2">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          {t("refresh")}
        </Button>
      </header>

      {error && (
        <Panel classification="restricted" label={t("error")}>
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{error}</p>
        </Panel>
      )}

      {temporaryPassword && (
        <Panel classification="restricted" label={t("temporaryPassword")}>
          <div className="px-5 py-4 flex flex-col md:flex-row md:items-center gap-3">
            <code className="flex-1 break-all border border-hairline bg-vault-sunk px-3 py-2 font-mono text-xs text-bone">
              {temporaryPassword}
            </code>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setTemporaryPassword(null)}
            >
              {t("dismiss")}
            </Button>
          </div>
        </Panel>
      )}

      <Panel classification="confidential" label={t("createUser")}>
        <form onSubmit={createUser} className="px-5 py-5 grid grid-cols-1 md:grid-cols-5 gap-4">
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("username")}</span>
            <Input value={username} onChange={(event) => setUsername(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
          </label>
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("displayName")}</span>
            <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
          </label>
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("email")}</span>
            <Input value={email} onChange={(event) => setEmail(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
          </label>
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("role")}</span>
            <select value={role} onChange={(event) => setRole(event.target.value as TenantRole)} className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
              {ROLES.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </label>
          <div className="flex items-end">
            <Button type="submit" disabled={saving || !tenantId || !username || !displayName} className="gap-2 w-full">
              <Plus className="h-4 w-4" />
              {t("create")}
            </Button>
          </div>
        </form>
      </Panel>

      <Panel classification="internal" label={t("attachExistingUser")}>
        <div className="px-5 py-5 space-y-4">
          <form onSubmit={attachExistingUser} className="grid grid-cols-1 md:grid-cols-[1fr_12rem_auto] gap-3 items-end">
            <label className="space-y-1.5">
              <span className="mono-meta block">{t("user")}</span>
              <SuggestInput
                value={selectedExistingUserId}
                onChange={setSelectedExistingUserId}
                onQuery={searchUserIds}
                placeholder={t("search")}
                inputClassName="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
                emptyLabel={t("noUserMatches")}
              />
            </label>
            <label className="space-y-1.5">
              <span className="mono-meta block">{t("role")}</span>
              <select value={attachRole} onChange={(event) => setAttachRole(event.target.value as TenantRole)} className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
                {ROLES.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>
            <Button type="submit" disabled={saving || !tenantId || !selectedExistingUserId} className="gap-2">
              <Link2 className="h-4 w-4" />
              {t("attach")}
            </Button>
          </form>
        </div>
      </Panel>

      <Panel label={t("tenantUsers")}>
        <div className="overflow-x-auto custom-scrollbar">
          <table className="w-full text-left">
            <thead className="border-b border-hairline">
              <tr className="mono-meta">
                <th className="px-4 py-2 font-normal">{t("user")}</th>
                <th className="px-4 py-2 font-normal">{t("email")}</th>
                <th className="px-4 py-2 font-normal">{t("role")}</th>
                <th className="px-4 py-2 font-normal">{t("state")}</th>
                <th className="px-4 py-2 font-normal">{t("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {users.map((user) => (
                <tr key={user.id}>
                  <td className="px-4 py-3">
                    <p className="text-sm text-bone">{user.display_name}</p>
                    <p className="font-mono text-xs text-bone-dim">{user.username}</p>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-bone-dim">
                    {user.email ?? "-"}
                  </td>
                  <td className="px-4 py-3">
                    <select value={user.role} onChange={(event) => void updateMembership(user, { role: event.target.value as TenantRole })} className="h-8 bg-vault-raised border border-hairline px-2 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
                      {ROLES.map((item) => (
                        <option key={item} value={item}>{item}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-bone-dim">
                    {user.membership_active ? t("active") : t("suspended")}
                    {user.must_change_password ? ` · ${t("reset")}` : ""}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      <Button size="icon-sm" variant="outline" onClick={() => void resetPassword(user)} title={t("resetPassword")}>
                        <KeyRound className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="icon-sm" variant="outline" onClick={() => void updateMembership(user, { is_active: !user.membership_active })} title={user.membership_active ? t("suspend") : t("reactivate")}>
                        {user.membership_active ? <UserX className="h-3.5 w-3.5" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!loading && users.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-sm text-bone-dim">
                    {t("empty")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
