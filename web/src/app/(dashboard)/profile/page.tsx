"use client";

import React, { useCallback, useEffect, useState } from "react";
import { KeyRound, LogOut, ShieldCheck, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/forensic";
import { authApi } from "@/lib/api";
import { ActiveSession, SessionUser } from "@/lib/types";
import { getErrorMessage } from "@/lib/errors";

export default function ProfilePage() {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [sessions, setSessions] = useState<ActiveSession[]>([]);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadProfile = useCallback(async () => {
    try {
      const [me, activeSessions] = await Promise.all([
        authApi.me(),
        authApi.listSessions(),
      ]);
      setUser(me);
      setSessions(activeSessions);
      setError(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Unable to load profile."));
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadProfile(), 0);
    return () => window.clearTimeout(timer);
  }, [loadProfile]);

  const changePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setMessage(null);
    setError(null);
    if (newPassword !== confirmPassword) {
      setError("New password confirmation does not match.");
      return;
    }
    setSavingPassword(true);
    try {
      await authApi.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setMessage("Password updated.");
      await loadProfile();
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Password was not updated."));
    } finally {
      setSavingPassword(false);
    }
  };

  const revokeSession = async (sessionId: string) => {
    setError(null);
    try {
      await authApi.revokeSession(sessionId);
      setSessions((items) => items.filter((item) => item.id !== sessionId));
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Session was not revoked."));
    }
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6 pb-10">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">operator account</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            Profile
          </h1>
        </div>
        {user && (
          <div className="font-mono text-xs text-bone-dim border border-hairline px-3 py-2">
            {user.role} <span className="text-bone">{user.tenant_id ?? "global"}</span>
          </div>
        )}
      </header>

      {error && (
        <Panel classification="restricted" label="profile error">
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{error}</p>
        </Panel>
      )}
      {message && (
        <Panel classification="internal" label="profile notice">
          <p className="px-5 py-4 font-mono text-xs text-signal-amber">{message}</p>
        </Panel>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[0.9fr_1fr] gap-6">
        <Panel classification="internal" label="identity">
          {user ? (
            <div className="px-5 py-5 space-y-5">
              <dl className="grid grid-cols-1 md:grid-cols-2 gap-4 font-mono text-xs">
                <div>
                  <dt className="mono-meta normal-case">display name</dt>
                  <dd className="mt-1 text-bone">{user.display_name}</dd>
                </div>
                <div>
                  <dt className="mono-meta normal-case">username</dt>
                  <dd className="mt-1 text-bone">{user.username}</dd>
                </div>
                <div>
                  <dt className="mono-meta normal-case">email</dt>
                  <dd className="mt-1 text-bone">{user.email ?? "—"}</dd>
                </div>
                <div>
                  <dt className="mono-meta normal-case">user id</dt>
                  <dd className="mt-1 text-bone truncate">{user.id}</dd>
                </div>
              </dl>
              <div className="border-t border-hairline pt-4">
                <p className="mono-meta mb-3">tenant memberships</p>
                <div className="space-y-2">
                  {user.memberships.map((membership) => (
                    <div
                      key={`${membership.tenant_id}:${membership.role}`}
                      className="flex items-center justify-between border border-hairline px-3 py-2 font-mono text-xs"
                    >
                      <span className="text-bone">{membership.tenant_id}</span>
                      <span className="text-bone-dim">{membership.role}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <p className="px-5 py-8 text-sm text-bone-dim">Loading profile.</p>
          )}
        </Panel>

        <Panel classification="confidential" label="password">
          <form onSubmit={changePassword} className="px-5 py-5 space-y-4">
            <label className="space-y-1.5 block">
              <span className="mono-meta block">current password</span>
              <Input
                type="password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
              />
            </label>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="space-y-1.5 block">
                <span className="mono-meta block">new password</span>
                <Input
                  type="password"
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
                />
              </label>
              <label className="space-y-1.5 block">
                <span className="mono-meta block">confirm password</span>
                <Input
                  type="password"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
                />
              </label>
            </div>
            <Button
              type="submit"
              disabled={savingPassword || !currentPassword || !newPassword}
              className="gap-2"
            >
              <KeyRound className="h-4 w-4" />
              Change password
            </Button>
          </form>
        </Panel>
      </div>

      <Panel label="active sessions">
        <div className="divide-y divide-hairline">
          {sessions.length === 0 ? (
            <p className="px-5 py-8 text-sm text-bone-dim">No active sessions.</p>
          ) : (
            sessions.map((session) => (
              <div
                key={session.id}
                className="px-5 py-4 flex flex-col lg:flex-row lg:items-center justify-between gap-4"
              >
                <div className="min-w-0 space-y-1">
                  <div className="flex items-center gap-2">
                    {session.current ? (
                      <ShieldCheck className="h-4 w-4 text-signal-amber" />
                    ) : (
                      <LogOut className="h-4 w-4 text-bone-dim" />
                    )}
                    <p className="font-mono text-xs text-bone truncate">{session.id}</p>
                  </div>
                  <p className="font-mono text-xs text-bone-dim truncate">
                    {session.user_agent ?? "unknown client"}
                  </p>
                  <p className="font-mono text-[0.6875rem] text-bone-dim">
                    last seen {new Date(session.last_seen_at).toLocaleString()}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={session.current}
                  onClick={() => revokeSession(session.id)}
                  className="gap-2"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Revoke
                </Button>
              </div>
            ))
          )}
        </div>
      </Panel>
    </div>
  );
}
