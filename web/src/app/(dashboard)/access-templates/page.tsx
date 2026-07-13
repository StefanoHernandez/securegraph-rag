"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { ClipboardList, Plus, RefreshCw, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SuggestInput } from "@/components/ui/suggest-input";
import { Panel } from "@/components/forensic";
import { controlPlaneApi } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { appendCsvValue, searchUserIds } from "@/lib/suggestions";
import { AccessTemplate, Classification, TenantGroup } from "@/lib/types";
import { useTenant as useTenantSelection } from "@/context/TenantProvider";

const CLASSIFICATIONS: Classification[] = ["public", "internal", "confidential", "restricted"];
const OWNER_RULES = ["uploader", "tenant_admin"] as const;

function splitIds(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function appendCsvIds(current: string, value: string): string {
  return appendCsvValue(current, value);
}

export default function AccessTemplatesPage() {
  const t = useTranslations("accessTemplatesPage");
  const { tenantId, loadingTenants } = useTenantSelection();
  const [templates, setTemplates] = useState<AccessTemplate[]>([]);
  const [groups, setGroups] = useState<TenantGroup[]>([]);
  const [documentType, setDocumentType] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [classification, setClassification] = useState<Classification>("internal");
  const [ownerRule, setOwnerRule] = useState<(typeof OWNER_RULES)[number]>("uploader");
  const [selectedGroupIds, setSelectedGroupIds] = useState<string[]>([]);
  const [directViewerIds, setDirectViewerIds] = useState("");
  const [viewerPicker, setViewerPicker] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const groupNames = useMemo(
    () => new Map(groups.map((group) => [group.id, group.display_name])),
    [groups]
  );

  const documentTypeSuggestions = useMemo(
    () =>
      Array.from(new Set(templates.map((template) => template.document_type))).map((value) => ({
        value,
        label: value,
      })),
    [templates]
  );

  const loadTemplates = useCallback(async () => {
    if (!tenantId) {
      setLoading(loadingTenants);
      return;
    }
    setLoading(true);
    try {
      const [templateRows, groupRows] = await Promise.all([
        controlPlaneApi.listAccessTemplates(tenantId),
        controlPlaneApi.listGroups(tenantId),
      ]);
      setTemplates(templateRows);
      setGroups(groupRows);
      setError(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("loadError")));
    } finally {
      setLoading(false);
    }
  }, [loadingTenants, tenantId, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadTemplates(), 0);
    return () => window.clearTimeout(timer);
  }, [loadTemplates]);

  const toggleGroup = (groupId: string) => {
    setSelectedGroupIds((items) =>
      items.includes(groupId)
        ? items.filter((item) => item !== groupId)
        : [...items, groupId]
    );
  };

  const createTemplate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!tenantId) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await controlPlaneApi.createAccessTemplate(tenantId, {
        document_type: documentType,
        display_name: displayName,
        default_classification: classification,
        owner_rule: ownerRule,
        allowed_group_ids: selectedGroupIds,
        direct_viewer_ids: splitIds(directViewerIds),
      });
      setTemplates((items) => [...items, created].sort((a, b) => a.display_name.localeCompare(b.display_name)));
      setDocumentType("");
      setDisplayName("");
      setClassification("internal");
      setOwnerRule("uploader");
      setSelectedGroupIds([]);
      setDirectViewerIds("");
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("createError")));
    } finally {
      setSaving(false);
    }
  };

  const updateTemplate = async (
    template: AccessTemplate,
    body: {
      display_name?: string;
      default_classification?: Classification;
      owner_rule?: "uploader" | "tenant_admin";
      allowed_group_ids?: string[];
      direct_viewer_ids?: string[];
      is_active?: boolean;
    }
  ) => {
    if (!tenantId) {
      return;
    }
    setError(null);
    try {
      const updated = await controlPlaneApi.updateAccessTemplate(tenantId, template.id, body);
      setTemplates((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("updateError")));
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
        <Button size="sm" onClick={loadTemplates} disabled={loading || !tenantId} className="gap-2">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          {t("refresh")}
        </Button>
      </header>

      {error && (
        <Panel classification="restricted" label={t("error")}>
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{error}</p>
        </Panel>
      )}

      <Panel classification="confidential" label={t("createTemplate")}>
        <form onSubmit={createTemplate} className="px-5 py-5 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <label className="space-y-1.5">
              <span className="mono-meta block">{t("documentType")}</span>
              <SuggestInput
                value={documentType}
                onChange={setDocumentType}
                suggestions={documentTypeSuggestions}
                inputClassName="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
              />
            </label>
            <label className="space-y-1.5">
              <span className="mono-meta block">{t("displayName")}</span>
              <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
            </label>
            <label className="space-y-1.5">
              <span className="mono-meta block">{t("classification")}</span>
              <select value={classification} onChange={(event) => setClassification(event.target.value as Classification)} className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
                {CLASSIFICATIONS.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>
            <label className="space-y-1.5">
              <span className="mono-meta block">{t("owner")}</span>
              <select value={ownerRule} onChange={(event) => setOwnerRule(event.target.value as (typeof OWNER_RULES)[number])} className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
                {OWNER_RULES.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-[1fr_0.8fr_auto] gap-4 items-end">
            <div className="space-y-2">
              <span className="mono-meta block">{t("groups")}</span>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {groups.map((group) => (
                  <label key={group.id} className="flex items-center gap-2 border border-hairline bg-vault-raised px-3 py-2 font-mono text-xs text-bone-dim">
                    <input type="checkbox" checked={selectedGroupIds.includes(group.id)} onChange={() => toggleGroup(group.id)} />
                    <span className="truncate">{group.display_name}</span>
                  </label>
                ))}
              </div>
            </div>
            <label className="space-y-1.5">
              <span className="mono-meta block">{t("directViewers")}</span>
              <Input value={directViewerIds} onChange={(event) => setDirectViewerIds(event.target.value)} placeholder="usr_1, usr_2" className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
              <SuggestInput
                value={viewerPicker}
                onChange={setViewerPicker}
                onQuery={searchUserIds}
                onPick={(item) => {
                  setDirectViewerIds((current) => appendCsvIds(current, item.value));
                  setViewerPicker("");
                }}
                placeholder={t("addViewer")}
                inputClassName="bg-vault-raised border-hairline text-bone font-mono text-xs h-9"
                emptyLabel={t("noUserMatches")}
              />
            </label>
            <Button type="submit" disabled={saving || !tenantId || !documentType || !displayName} className="gap-2">
              <Plus className="h-4 w-4" />
              {t("create")}
            </Button>
          </div>
        </form>
      </Panel>

      <Panel label={t("templates")}>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-px bg-hairline">
          {templates.map((template) => (
            <TemplateCard
              key={template.id}
              template={template}
              groups={groups}
              groupNames={groupNames}
              onUpdate={updateTemplate}
            />
          ))}
          {!loading && templates.length === 0 && (
            <p className="bg-vault-raised px-5 py-8 text-sm text-bone-dim">
              {t("empty")}
            </p>
          )}
        </div>
      </Panel>
    </div>
  );
}

function TemplateCard({
  template,
  groups,
  groupNames,
  onUpdate,
}: {
  template: AccessTemplate;
  groups: TenantGroup[];
  groupNames: Map<string, string>;
  onUpdate: (
    template: AccessTemplate,
    body: {
      display_name?: string;
      default_classification?: Classification;
      owner_rule?: "uploader" | "tenant_admin";
      allowed_group_ids?: string[];
      direct_viewer_ids?: string[];
      is_active?: boolean;
    }
  ) => void;
}) {
  const t = useTranslations("accessTemplatesPage");
  const [name, setName] = useState(template.display_name);
  const [classification, setClassification] = useState<Classification>(template.default_classification);
  const [ownerRule, setOwnerRule] = useState<(typeof OWNER_RULES)[number]>(template.owner_rule);
  const [groupIds, setGroupIds] = useState<string[]>(template.allowed_group_ids);
  const [directViewers, setDirectViewers] = useState(template.direct_viewer_ids.join(", "));
  const [viewerPicker, setViewerPicker] = useState("");
  const [active, setActive] = useState(template.is_active);

  const toggleGroup = (groupId: string) => {
    setGroupIds((items) =>
      items.includes(groupId)
        ? items.filter((item) => item !== groupId)
        : [...items, groupId]
    );
  };

  const save = (event: React.FormEvent) => {
    event.preventDefault();
    onUpdate(template, {
      display_name: name,
      default_classification: classification,
      owner_rule: ownerRule,
      allowed_group_ids: groupIds,
      direct_viewer_ids: splitIds(directViewers),
      is_active: active,
    });
  };

  return (
    <article className="bg-vault-raised px-5 py-4 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm text-bone">{template.display_name}</p>
          <p className="font-mono text-xs text-bone-dim">{template.document_type}</p>
        </div>
        <ClipboardList className="h-4 w-4 text-bone-dim" />
      </div>
      <div className="grid grid-cols-2 gap-3 font-mono text-xs text-bone-dim">
        <p>{t("classification")} · <span className="text-bone">{template.default_classification}</span></p>
        <p>{t("owner")} · <span className="text-bone">{template.owner_rule}</span></p>
        <p>{t("version")} · <span className="text-bone">v{template.version}</span></p>
        <p>{t("state")} · <span className="text-bone">{template.is_active ? t("active") : t("inactive")}</span></p>
      </div>
      <div className="font-mono text-xs text-bone-dim">
        {t("groups")} ·{" "}
        <span className="text-bone">
          {template.allowed_group_ids.map((id) => groupNames.get(id) ?? id).join(", ") || "-"}
        </span>
      </div>
      <form onSubmit={save} className="space-y-3 border border-hairline bg-vault-sunk p-3">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("name")}</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-9" />
          </label>
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("direct")}</span>
            <Input value={directViewers} onChange={(event) => setDirectViewers(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-9" />
            <SuggestInput
              value={viewerPicker}
              onChange={setViewerPicker}
              onQuery={searchUserIds}
              onPick={(item) => {
                setDirectViewers((current) => appendCsvIds(current, item.value));
                setViewerPicker("");
              }}
              placeholder={t("addViewer")}
              inputClassName="bg-vault-raised border-hairline text-bone font-mono text-xs h-8"
              emptyLabel={t("noUserMatches")}
            />
          </label>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto_auto] gap-3 items-end">
          <select value={classification} onChange={(event) => setClassification(event.target.value as Classification)} className="h-9 bg-vault-raised border border-hairline px-2 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
            {CLASSIFICATIONS.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
          <select value={ownerRule} onChange={(event) => setOwnerRule(event.target.value as (typeof OWNER_RULES)[number])} className="h-9 bg-vault-raised border border-hairline px-2 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
            {OWNER_RULES.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
          <label className="h-9 inline-flex items-center gap-2 border border-hairline bg-vault-raised px-2 font-mono text-xs text-bone-dim">
            <input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} />
            {t("active")}
          </label>
          <Button type="submit" size="icon-sm" variant="outline" title={t("saveTemplate")}>
            <Save className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {groups.map((group) => (
            <label key={group.id} className="flex items-center gap-2 border border-hairline bg-vault-raised px-3 py-2 font-mono text-xs text-bone-dim">
              <input type="checkbox" checked={groupIds.includes(group.id)} onChange={() => toggleGroup(group.id)} />
              <span className="truncate">{group.display_name}</span>
            </label>
          ))}
        </div>
      </form>
    </article>
  );
}
