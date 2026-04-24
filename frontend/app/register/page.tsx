"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getRegistrationStatusApi, registerApi, type RegistrationStatus } from "../_lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [status, setStatus] = useState<RegistrationStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [notificationEmail, setNotificationEmail] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await getRegistrationStatusApi();
        if (!cancelled) setStatus(s);
      } catch (e: any) {
        if (!cancelled) setStatusError(e?.message ?? "Could not load registration options");
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

        {statusError ? (
          <div className="text-sm text-red-400 border border-red-400/40 p-3 cyber-chamfer-sm mb-4">{statusError}</div>
        ) : null}

        {!status ? (
          <div className="text-sm text-[var(--mutedForeground)] mb-6">Loading…</div>
        ) : !status.allowed ? (
          <div className="text-sm text-[var(--mutedForeground)] mb-6">
            Registration is turned off on this server. Ask the operator to set{" "}
            <code className="text-xs">ALLOW_OPEN_REGISTRATION=true</code> or a{" "}
            <code className="text-xs">REGISTRATION_INVITE_CODE</code> in the environment.
          </div>
        ) : (
          <>
            <p className="text-sm text-[var(--mutedForeground)] mb-6">
              {status.open_registration
                ? "Create your own account. Your notification email is where we send match digests and approval requests when you run scans from the dashboard."
                : "Create an account using the invite code from your server operator. Your notification email receives match digests and approval requests when you run scans from the dashboard."}
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
                  placeholder="your email (required for sign-up confirmation & job alerts)"
                  type="email"
                  autoComplete="email"
                />
              </div>
              {status.invite_required ? (
                <div className="cyber-input-wrap">
                  <span className="cyber-input-prefix">&gt;</span>
                  <input
                    className="cyber-input"
                    value={inviteCode}
                    onChange={(e) => setInviteCode(e.target.value)}
                    placeholder="invite code (required)"
                    autoComplete="off"
                  />
                </div>
              ) : null}

              {error ? (
                <div className="text-sm text-red-400 border border-red-400/40 p-3 cyber-chamfer-sm">{error}</div>
              ) : null}

              <button type="submit" className="cyber-button cyber-chamfer-sm w-full" disabled={loading}>
                {loading ? "CREATING..." : "CREATE ACCOUNT"}
              </button>
            </form>
          </>
        )}

        <p className="mt-5 text-sm text-[var(--mutedForeground)]">
          <Link href="/login" className="text-[var(--accent)] underline-offset-2 hover:underline">
            Back to login
          </Link>
        </p>
      </div>
    </div>
  );
}
