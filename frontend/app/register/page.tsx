"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { registerApi } from "../_lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [notificationEmail, setNotificationEmail] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await registerApi({
        username,
        password,
        notification_email: notificationEmail,
        invite_code: inviteCode,
      });
      router.push("/login");
    } catch (err: any) {
      setError(err?.message ?? "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="cyber-panel cyber-chamfer-sm p-6 w-full max-w-lg">
        <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-4">
          APPLYD//REGISTER
        </div>
        <h1 className="cyber-glitch text-3xl md:text-4xl mb-2" data-text="NEW USER">
          NEW USER
        </h1>
        <p className="text-sm text-[var(--mutedForeground)] mb-6">
          Requires a server-configured invite code. Your notification email receives match digests and approval
          requests when you run scans from the dashboard.
        </p>

        <form onSubmit={onSubmit} className="space-y-4">
          <div className="cyber-input-wrap">
            <span className="cyber-input-prefix">&gt;</span>
            <input
              className="cyber-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="username (min 2 chars)"
              autoComplete="username"
            />
          </div>
          <div className="cyber-input-wrap">
            <span className="cyber-input-prefix">&gt;</span>
            <input
              className="cyber-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="password (min 6 chars)"
              type="password"
              autoComplete="new-password"
            />
          </div>
          <div className="cyber-input-wrap">
            <span className="cyber-input-prefix">&gt;</span>
            <input
              className="cyber-input"
              value={notificationEmail}
              onChange={(e) => setNotificationEmail(e.target.value)}
              placeholder="notification email (where we send matches)"
              type="email"
              autoComplete="email"
            />
          </div>
          <div className="cyber-input-wrap">
            <span className="cyber-input-prefix">&gt;</span>
            <input
              className="cyber-input"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
              placeholder="invite code"
              autoComplete="off"
            />
          </div>

          {error ? (
            <div className="text-sm text-red-400 border border-red-400/40 p-3 cyber-chamfer-sm">{error}</div>
          ) : null}

          <button type="submit" className="cyber-button cyber-chamfer-sm w-full" disabled={loading}>
            {loading ? "CREATING..." : "CREATE ACCOUNT"}
          </button>
        </form>

        <p className="mt-5 text-sm text-[var(--mutedForeground)]">
          <Link href="/login" className="text-[var(--accent)] underline-offset-2 hover:underline">
            Back to login
          </Link>
        </p>
      </div>
    </div>
  );
}
