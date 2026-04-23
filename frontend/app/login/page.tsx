"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getRegistrationStatusApi, loginApi, type RegistrationStatus } from "../_lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [reg, setReg] = useState<RegistrationStatus | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await getRegistrationStatusApi();
        if (!cancelled) setReg(s);
      } catch {
        if (!cancelled) setReg(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await loginApi(username, password);
      router.push("/dashboard");
    } catch (err: any) {
      setError(err?.message ?? "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="cyber-panel cyber-chamfer-sm p-6 w-full max-w-lg">
        <div className="flex items-center gap-3 mb-5">
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-full bg-red-500/70" />
            <span className="h-3 w-3 rounded-full bg-yellow-500/70" />
            <span className="h-3 w-3 rounded-full bg-green-500/70" />
          </div>
          <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)]">
            APPLYD//AUTH
          </div>
        </div>

        <h1
          className="cyber-glitch text-4xl md:text-5xl mb-4"
          data-text="APPLYD"
        >
          APPLYD
        </h1>

        <div className="text-sm text-[var(--mutedForeground)] mb-6 font-mono">
          User <span className="cyber-cursor" />
          <span className="ml-2 text-[var(--mutedForeground)]">login required for auto-apply</span>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <div className="cyber-input-wrap">
            <span className="cyber-input-prefix">&gt;</span>
            <input
              className="cyber-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="username"
              autoComplete="username"
            />
          </div>

          <div className="cyber-input-wrap">
            <span className="cyber-input-prefix">&gt;</span>
            <input
              className="cyber-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="password"
              type="password"
              autoComplete="current-password"
            />
          </div>

          {error ? (
            <div className="text-sm text-red-400 border border-red-400/40 p-3 cyber-chamfer-sm">
              {error}
            </div>
          ) : null}

          <button
            type="submit"
            className="cyber-button cyber-chamfer-sm w-full"
            disabled={loading}
          >
            {loading ? "AUTH IN PROGRESS" : "ENTER"}
          </button>
        </form>

        {reg === null || reg.allowed ? (
          <p className="mt-5 text-sm text-[var(--mutedForeground)]">
            Need an account?{" "}
            <Link href="/register" className="text-[var(--accent)] underline-offset-2 hover:underline">
              {reg?.open_registration ? "Create one" : reg?.invite_required ? "Register with invite code" : "Create an account"}
            </Link>
          </p>
        ) : null}
      </div>
    </div>
  );
}

