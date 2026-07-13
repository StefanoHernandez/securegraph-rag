"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Plus, RefreshCw, Save, UserMinus, UserPlus, UsersRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SuggestInput } from "@/components/ui/suggest-input";
import { Panel } from "@/components/forensic";
import { controlPlaneApi } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { searchUserIds } from "@/lib/suggestions";
import { GroupKind, TenantGroup, TenantUser } from "@/lib/types";
import { useTenant as useTenantSelection } from "@/context/TenantProvider";

const KINDS: GroupKind[] = ["functional", "project"];

export default function GroupsPage() {
  const t = useTranslations("groupsPage");
  const { tenantId, loadingTenants } = useTenantSelection();
  const [groups, setGroups] = useState<TenantGroup[]>([]);
  const [users, setUsers] = useState<TenantUser[]>([]);
  const [members, setMembers] = useState<TenantUser[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<GroupKind>("project");
  const [selectedUserId, setSelectedUserId] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedGroup = useMemo(
    () => groups.find((group) => group.id === selectedGroupId) ?? null,
    [groups, selectedGroupId]
  );

  const loadGroups = useCallback(async () => {
    if (!tenantId) {
      setLoading(loadingTenants);
      return;
    }
    setLoading(true);
    try {
      const [groupRows, userRows] = await Promise.all([
        controlPlaneApi.listGroups(tenantId),
        controlPlaneApi.listUsers(tenantId),
      ]);
      setGroups(groupRows);
      setUsers(userRows);
      const nextGroupId = selectedGroupId || groupRows[0]?.id || "";
      setSelectedGroupId(nextGroupId);
      setError(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("loadError")));
    } finally {
      setLoading(false);
    }
  }, [loadingTenants, selectedGroupId, tenantId, t]);

  const loadMembers = useCallback(async () => {
    if (!tenantId || !selectedGroupId) {
      setMembers([]);
      return;
    }
    try {
      setMembers(await controlPlaneApi.listGroupMembers(tenantId, selectedGroupId));
      setError(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("loadMembersError")));
    }
  }, [selectedGroupId, tenantId, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadGroups(), 0);
    return () => window.clearTimeout(timer);
  }, [loadGroups]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadMembers(), 0);
    return () => window.clearTimeout(timer);
  }, [loadMembers]);

  const createGroup = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!tenantId) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await controlPlaneApi.createGroup(tenantId, {
        slug,
        display_name: displayName,
        kind,
        description: description || null,
      });
      setGroups((items) => [...items, created].sort((a, b) => a.display_name.localeCompare(b.display_name)));
      setSelectedGroupId(created.id);
      setSlug("");
      setDisplayName("");
      setDescription("");
      setKind("project");
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("createError")));
    } finally {
      setSaving(false);
    }
  };

  const addMember = async () => {
    if (!tenantId || !selectedGroupId || !selectedUserId) {
      return;
    }
    setError(null);
    try {
      await controlPlaneApi.addGroupMember(tenantId, selectedGroupId, selectedUserId);
      setSelectedUserId("");
      await loadMembers();
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("addError")));
    }
  };

  const removeMember = async (user: TenantUser) => {
    if (!tenantId || !selectedGroupId) {
      return;
    }
    setError(null);
    try {
      await controlPlaneApi.removeGroupMember(tenantId, selectedGroupId, user.id);
      await loadMembers();
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("removeError")));
    }
  };

  const updateGroup = async (
    group: TenantGroup,
    body: {
      display_name: string;
      description: string | null;
      kind: GroupKind;
      is_active: boolean;
    }
  ) => {
    if (!tenantId) {
      return;
    }
    setError(null);
    try {
      const updated = await controlPlaneApi.updateGroup(tenantId, group.id, body);
      setGroups((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("updateError")));
    }
  };

  const memberIds = new Set(members.map((member) => member.id));
  const addableUsers = users.filter((user) => user.membership_active && !memberIds.has(user.id));

  return (
    <div className="max-w-6xl mx-auto space-y-6 pb-10">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">{t("eyebrow")}</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            {t("title")}
          </h1>
        </div>
        <Button size="sm" onClick={loadGroups} disabled={loading || !tenantId} className="gap-2">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          {t("refresh")}
        </Button>
      </header>

      {error && (
        <Panel classification="restricted" label={t("error")}>
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{error}</p>
        </Panel>
      )}

      <Panel classification="confidential" label={t("createGroup")}>
        <form onSubmit={createGroup} className="px-5 py-5 grid grid-cols-1 md:grid-cols-5 gap-4">
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("slug")}</span>
            <Input value={slug} onChange={(event) => setSlug(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
          </label>
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("name")}</span>
            <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
          </label>
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("kind")}</span>
            <select value={kind} onChange={(event) => setKind(event.target.value as GroupKind)} className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
              {KINDS.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </label>
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("description")}</span>
            <Input value={description} onChange={(event) => setDescription(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
          </label>
          <div className="flex items-end">
            <Button type="submit" disabled={saving || !tenantId || !slug || !displayName} className="gap-2 w-full">
              <Plus className="h-4 w-4" />
              {t("create")}
            </Button>
          </div>
        </form>
      </Panel>

      <div className="grid grid-cols-1 xl:grid-cols-[0.9fr_1.1fr] gap-6">
        <Panel label={t("groups")}>
          <div className="divide-y divide-hairline">
            {groups.map((group) => (
              <button
                key={group.id}
                type="button"
                onClick={() => setSelectedGroupId(group.id)}
                className={`w-full px-5 py-3 text-left transition-colors ${selectedGroupId === group.id ? "bg-vault-raised" : "hover:bg-[rgba(232,230,223,0.03)]"}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm text-bone">{group.display_name}</p>
                    <p className="font-mono text-xs text-bone-dim">{group.slug}</p>
                  </div>
                  <span className="font-mono text-[0.6875rem] uppercase text-bone-dim">
                    {group.kind}
                    {group.is_active ? "" : ` · ${t("inactive")}`}
                  </span>
                </div>
              </button>
            ))}
            {!loading && groups.length === 0 && (
              <p className="px-5 py-8 text-sm text-bone-dim">{t("empty")}</p>
            )}
          </div>
        </Panel>

        <Panel
          classification={selectedGroup?.kind === "project" ? "confidential" : "internal"}
          label={selectedGroup ? selectedGroup.display_name : t("members")}
          headRight={<UsersRound className="h-4 w-4 text-bone-dim" />}
        >
          <div className="px-5 py-5 space-y-4">
            {selectedGroup && (
              <GroupEditor
                key={selectedGroup.id}
                group={selectedGroup}
                onSave={updateGroup}
              />
            )}
            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3">
              <SuggestInput
                value={selectedUserId}
                onChange={setSelectedUserId}
                suggestions={addableUsers.map((user) => ({
                  value: user.id,
                  label: user.display_name,
                  hint: user.username,
                }))}
                onQuery={searchUserIds}
                placeholder={t("selectUser")}
                inputClassName="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
                emptyLabel={t("noUserMatches")}
              />
              <Button type="button" onClick={() => void addMember()} disabled={!selectedGroupId || !selectedUserId} className="gap-2">
                <UserPlus className="h-4 w-4" />
                {t("add")}
              </Button>
            </div>

            <div className="border border-hairline">
              {members.map((member) => (
                <div key={member.id} className="flex items-center justify-between gap-3 px-4 py-3 border-b border-hairline last:border-b-0">
                  <div>
                    <p className="text-sm text-bone">{member.display_name}</p>
                    <p className="font-mono text-xs text-bone-dim">{member.username} · {member.role}</p>
                  </div>
                  <Button size="icon-sm" variant="outline" onClick={() => void removeMember(member)} title={t("removeMember")}>
                    <UserMinus className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
              {members.length === 0 && (
                <p className="px-4 py-8 text-sm text-bone-dim">{t("noMembers")}</p>
              )}
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function GroupEditor({
  group,
  onSave,
}: {
  group: TenantGroup;
  onSave: (
    group: TenantGroup,
    body: {
      display_name: string;
      description: string | null;
      kind: GroupKind;
      is_active: boolean;
    }
  ) => void;
}) {
  const t = useTranslations("groupsPage");
  const [name, setName] = useState(group.display_name);
  const [description, setDescription] = useState(group.description ?? "");
  const [kind, setKind] = useState<GroupKind>(group.kind);
  const [active, setActive] = useState(group.is_active);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    onSave(group, {
      display_name: name,
      description: description || null,
      kind,
      is_active: active,
    });
  };

  return (
    <form onSubmit={submit} className="grid grid-cols-1 md:grid-cols-[1fr_0.8fr_auto] gap-3 border border-hairline bg-vault-sunk p-3">
      <label className="space-y-1.5">
        <span className="mono-meta block">{t("name")}</span>
        <Input value={name} onChange={(event) => setName(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-9" />
      </label>
      <label className="space-y-1.5">
        <span className="mono-meta block">{t("description")}</span>
        <Input value={description} onChange={(event) => setDescription(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-9" />
      </label>
      <div className="flex items-end gap-2">
        <select value={kind} onChange={(event) => setKind(event.target.value as GroupKind)} className="h-9 bg-vault-raised border border-hairline px-2 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
          {KINDS.map((item) => (
            <option key={item} value={item}>{item}</option>
          ))}
        </select>
        <label className="h-9 inline-flex items-center gap-2 border border-hairline bg-vault-raised px-2 font-mono text-xs text-bone-dim">
          <input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} />
          {t("active")}
        </label>
        <Button type="submit" size="icon-sm" variant="outline" title={t("saveGroup")}>
          <Save className="h-3.5 w-3.5" />
        </Button>
      </div>
    </form>
  );
}
