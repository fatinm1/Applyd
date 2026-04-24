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
  const [registrationDone, setRegistrationDone] = useState<{
    welcome_email_sent: boolean;
    welcome_email_status: string;
  } | null>(null);

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
      const res = await registerApi({
        username,
        password,
        notification_email: notificationEmail,
        invite_code: inviteCode,
      });
      setRegistrationDone({
        welcome_email_sent: Boolean(res.welcome_email_sent),
        welcome_email_status: res.welcome_email_status ?? "",
      });
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

        {registrationDone ? (
          <div className="mb-6 space-y-4">
            <div className="text-sm border border-[var(--border)] p-4 cyber-chamfer-sm leading-relaxed">
              <p className="font-semibold text-[var(--foreground)] mb-2">Account created</p>
              {registrationDone.welcome_email_sent ? (
                <p className="text-[var(--mutedForeground)]">
                  A confirmation was sent to your notification address. Check <strong>Spam</strong> or your
                  university quarantine folder — mail from Gmail SMTP is sometimes filtered.
                </p>
              ) : registrationDone.welcome_email_status === "smtp_not_configured" ? (
                <p className="text-[var(--mutedForeground)]">
                  This server is not set up to send mail (<code className="text-xs">NOTIFY_EMAIL</code> /{" "}
                  <code className="text-xs">GMAIL_APP_PASSWORD</code> missing on Railway). Ask the operator to add
                  them, then use the dashboard to confirm your notification email.
                </p>
              ) : registrationDone.welcome_email_status === "smtp_failed" ? (
                <p className="text-[var(--mutedForeground)]">
                  The confirmation email could not be sent (SMTP error — often an invalid Gmail App Password, or paste
                  the 16-character password <strong>without spaces</strong>). You can still sign in; check server logs
                  for details.
                </p>
              ) : registrationDone.welcome_email_status === "no_destination" ? (
                <p className="text-[var(--mutedForeground)]">
                  No email address was saved for notifications. After login, set your notification email on the
                  dashboard.
                </p>
              ) : (
                <p className="text-[var(--mutedForeground)]">You can sign in below.</p>
              )}
            </div>
            <button type="button" className="cyber-button cyber-chamfer-sm w-full" onClick={() => router.push("/login")}>
              Continue to login
            </button>
          </div>
        ) : null}

        {!registrationDone && !status ? (
          <div className="text-sm text-[var(--mutedForeground)] mb-6">Loading…</div>
        ) : !registrationDone && status && !status.allowed ? (
          <div className="text-sm text-[var(--mutedForeground)] mb-6">
            Registration is turned off on this server. Ask the operator to set{" "}
            <code className="text-xs">ALLOW_OPEN_REGISTRATION=true</code> or a{" "}
            <code className="text-xs">REGISTRATION_INVITE_CODE</code> in the environment.
          </div>
        ) : !registrationDone && status && status.allowed ? (
          <>
            <p className="text-sm text-[var(--mutedForeground)] mb-6">
              {status.open_registration
                ? "Create your own account. Sign-up confirmation and job alerts go to your notification email below (or to your username if it is a full email address and you leave the field blank)."
                : "Create an account using the invite code from your server operator. Confirmation and job alerts use your notification email, or your username when it is an email address."}
            </p>

            <form onSubmit={onSubmit} className="space-y-4">
              <div className="cyber-input-wrap">
                <span className="cyber-input-prefix">&gt;</span>
                <input
                  className="cyber-input"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="username or your email"
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
        ) : null}

        <p className="mt-5 text-sm text-[var(--mutedForeground)]">
          <Link href="/login" className="text-[var(--accent)] underline-offset-2 hover:underline">
            Back to login
          </Link>
        </p>
      </div>
    </div>
  );
}
