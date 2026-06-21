"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck, Lock, User, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { UserSession } from "@/lib/types";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // If already logged in, redirect to dashboard
    const token = localStorage.getItem("securgraph_token");
    if (token) {
      router.push("/");
    }
  }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      setError("Please fill in all fields.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await api.post<UserSession>("/api/auth/login", {
        username,
        password,
      });

      localStorage.setItem("securgraph_token", response.token);
      localStorage.setItem("securgraph_user", JSON.stringify(response.user));
      localStorage.setItem("securgraph_tenant", response.user.tenant_id);

      router.push("/");
    } catch (err: any) {
      setError(err.message || "Invalid username or password. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 flex items-center justify-center min-h-screen px-4 bg-[#0a0a0f]/40 relative z-10">
      {/* Glow effect behind the card */}
      <div className="absolute w-[450px] h-[450px] bg-gradient-to-r from-accent-indigo/10 via-accent-cyan/10 to-accent-emerald/10 rounded-full filter blur-[80px] pointer-events-none -z-10 animate-pulse"></div>

      <Card className="w-full max-w-md glassmorphism-card border-border/50 text-foreground relative overflow-hidden">
        {/* Subtle top light bar */}
        <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-accent-indigo via-accent-cyan to-accent-emerald opacity-75"></div>

        <CardHeader className="space-y-3 pt-8 pb-6 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-accent-indigo/20 text-accent-indigo border border-accent-indigo/30">
            <ShieldCheck className="h-6 w-6" />
          </div>
          <div className="space-y-1">
            <CardTitle className="text-2xl font-black tracking-tight gradient-text">
              SecurGraph RAG
            </CardTitle>
            <CardDescription className="text-foreground/50 text-xs uppercase tracking-wider font-mono">
              Verifiable RAG Access Control
            </CardDescription>
          </div>
        </CardHeader>

        <form onSubmit={handleSubmit}>
          <CardContent className="space-y-4 pb-6">
            {error && (
              <div className="flex items-start gap-2.5 p-3 rounded-lg bg-accent-rose/10 border border-accent-rose/20 text-accent-rose text-xs">
                <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <span className="leading-relaxed">{error}</span>
              </div>
            )}

            <div className="space-y-2">
              <label className="text-xs font-semibold text-foreground/70" htmlFor="username">
                Username
              </label>
              <div className="relative">
                <User className="absolute left-3 top-3 h-4 w-4 text-foreground/40" />
                <Input
                  id="username"
                  type="text"
                  placeholder="admin, analyst, or viewer"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="pl-10 bg-black/40 border-border/50 text-foreground focus-visible:ring-accent-indigo/50 h-10 text-sm"
                  autoComplete="off"
                  disabled={loading}
                />
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-semibold text-foreground/70" htmlFor="password">
                Password
              </label>
              <div className="relative">
                <Lock className="absolute left-3 top-3 h-4 w-4 text-foreground/40" />
                <Input
                  id="password"
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="pl-10 bg-black/40 border-border/50 text-foreground focus-visible:ring-accent-indigo/50 h-10 text-sm"
                  disabled={loading}
                />
              </div>
            </div>

            <div className="text-xs text-foreground/40 text-center mt-2 font-mono">
              Demo logins: admin/admin123, analyst/analyst123, viewer/viewer123
            </div>
          </CardContent>

          <CardFooter className="pt-2 pb-8 flex flex-col gap-4">
            <Button
              type="submit"
              className="w-full bg-gradient-to-r from-accent-indigo via-accent-cyan to-accent-emerald text-black font-bold hover:opacity-90 transition h-10 shadow-lg"
              disabled={loading}
            >
              {loading ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-black border-t-transparent"></div>
              ) : (
                "Authenticate Session"
              )}
            </Button>
          </CardFooter>
        </form>
      </Card>
    </div>
  );
}
