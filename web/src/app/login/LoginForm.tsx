"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel, ClassificationStamp } from "@/components/forensic";
import { authApi } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";

function postLoginTarget(): string {
  // Return-URL from ?next=, restricted to internal paths ("/..." but not
  // "//host") so the parameter cannot become an open redirect.
  const next = new URLSearchParams(window.location.search).get("next");
  if (next && next.startsWith("/") && !next.startsWith("//")) {
    return next;
  }
  return "/";
}

export default function LoginForm() {
  const t = useTranslations("loginPage");
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    authApi
      .me()
      .then(() => {
        if (active) {
          router.push(postLoginTarget());
        }
      })
      .catch(() => {
        // Anonymous users stay on the login screen.
      });
    return () => {
      active = false;
    };
  }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      setError(t("fillFields"));
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await authApi.login(username, password);

      localStorage.setItem("securgraph_user", JSON.stringify(response.user));
      if (response.user.tenant_id) {
        localStorage.setItem("securgraph_tenant", response.user.tenant_id);
      }

      router.push(postLoginTarget());
    } catch (err: unknown) {
      setError(
        getErrorMessage(err, t("invalid"))
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 flex items-center justify-center min-h-screen px-4 relative z-10">
      <Panel
        classification="restricted"
        label={t("panel")}
        headRight={<ClassificationStamp level="restricted" />}
        className="w-full max-w-md declassify"
      >
        <div className="px-6 pt-8 pb-5 text-center border-b border-hairline">
          <p className="font-display text-2xl font-bold tracking-[0.18em] text-bone select-none">
            SECURE<span className="text-signal-amber">GRAPH</span>
          </p>
          <p className="mono-meta mt-2">{t("eyebrow")}</p>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="px-6 py-6 space-y-4">
            {error && (
              <div
                className="classification-gutter px-3 py-2.5 font-mono text-xs text-seal-red"
                style={{ ["--gutter-color" as string]: "var(--seal-red)" }}
              >
                ⚠ {error}
              </div>
            )}

            <div className="space-y-1.5">
              <label className="mono-meta block" htmlFor="username">
                {t("username")}
              </label>
              <Input
                id="username"
                type="text"
                placeholder={t("usernamePlaceholder")}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="bg-vault-raised border-hairline text-bone text-sm focus-visible:border-signal-amber h-10"
                autoComplete="off"
                disabled={loading}
              />
            </div>

            <div className="space-y-1.5">
              <label className="mono-meta block" htmlFor="password">
                {t("password")}
              </label>
              <Input
                id="password"
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="bg-vault-raised border-hairline text-bone text-sm focus-visible:border-signal-amber h-10"
                disabled={loading}
              />
            </div>

            <Button
              type="submit"
              className="w-full h-10"
              disabled={loading}
            >
              {loading ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              ) : (
                t("submit")
              )}
            </Button>
          </div>

          <div className="px-6 pb-6 pt-1 border-t border-hairline">
            <p className="mono-meta normal-case leading-relaxed">
              {t("demo")}
            </p>
          </div>
        </form>
      </Panel>
    </div>
  );
}
