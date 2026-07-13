import { controlPlaneApi, operationsApi, tenantApi } from "@/lib/api";
import type { Suggestion } from "@/components/ui/suggest-input";
import type { TenantGroup, TenantUser, UserSearchResult } from "@/lib/types";

const FGA_USER_PREFIX = "user:";
const FGA_GROUP_SUFFIX = "#member";

export function userToFgaSubject(user: Pick<UserSearchResult, "id" | "username" | "display_name">): Suggestion {
  return {
    value: `${FGA_USER_PREFIX}${user.id}`,
    label: user.display_name,
    hint: user.username,
  };
}

export function tenantUserToFgaSubject(user: Pick<TenantUser, "id" | "username" | "display_name">): Suggestion {
  return userToFgaSubject(user);
}

export function groupToFgaSubject(group: Pick<TenantGroup, "slug" | "display_name">): Suggestion {
  return {
    value: `group:${group.slug}${FGA_GROUP_SUFFIX}`,
    label: group.display_name,
    hint: group.slug,
  };
}

export function documentToFgaObject(documentId: string, label?: string): Suggestion {
  const value = documentId.startsWith("document:") ? documentId : `document:${documentId}`;
  return {
    value,
    label: label ?? value.replace(/^document:/, ""),
  };
}

export async function searchUserIds(query: string): Promise<Suggestion[]> {
  const users = await controlPlaneApi.searchUsers(query);
  return users.map((user) => ({
    value: user.id,
    label: user.display_name,
    hint: user.username,
  }));
}

export async function searchUserSubjects(query: string): Promise<Suggestion[]> {
  const users = await controlPlaneApi.searchUsers(query);
  return users.map(userToFgaSubject);
}

export async function searchTenantSuggestions(query: string): Promise<Suggestion[]> {
  const tenants = await tenantApi.listTenants();
  const needle = query.trim().toLowerCase();
  return tenants
    .filter((tenant) => {
      if (!needle) return true;
      return (
        tenant.id.toLowerCase().includes(needle) ||
        tenant.display_name.toLowerCase().includes(needle)
      );
    })
    .slice(0, 12)
    .map((tenant) => ({
      value: tenant.id,
      label: tenant.display_name,
      hint: tenant.id,
    }));
}

export async function loadGroupSubjectsForTenant(tenantId: string): Promise<Suggestion[]> {
  const groups = await controlPlaneApi.listGroups(tenantId);
  return groups.filter((group) => group.is_active).map(groupToFgaSubject);
}

export async function loadTenantUserSubjects(tenantId: string): Promise<Suggestion[]> {
  const users = await controlPlaneApi.listUsers(tenantId);
  return users.filter((user) => user.is_active).map(tenantUserToFgaSubject);
}

export function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values.filter(Boolean)));
}

export function appendCsvValue(current: string, value: string): string {
  const parts = current
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  if (parts.includes(value)) return current;
  return [...parts, value].join(", ");
}

export async function searchTenantDocumentIds(
  tenantId: string,
  query: string
): Promise<Suggestion[]> {
  const { jobs } = await operationsApi.listIngestionJobs(tenantId);
  const needle = query.trim().toLowerCase();
  return uniqueStrings(
    jobs.map((job) => job.document_id).filter((id): id is string => Boolean(id))
  )
    .filter((id) => !needle || id.toLowerCase().includes(needle))
    .slice(0, 12)
    .map((id) => ({ value: id, label: id }));
}

export async function loadTenantDocumentTypeSuggestions(
  tenantId: string
): Promise<Suggestion[]> {
  const templates = await controlPlaneApi.listAccessTemplates(tenantId);
  return uniqueStrings(templates.map((template) => template.document_type)).map(
    (value) => ({ value, label: value })
  );
}
