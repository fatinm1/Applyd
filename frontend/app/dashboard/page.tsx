"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  approveJobApi,
  coverLetterApi,
  getAgentStateApi,
  getMeApi,
  listJobsApi,
  patchMeApi,
  rejectJobApi,
  runAgentNowApi,
  skipJobApi,
  updateAgentStateApi,
} from "../_lib/api";
import type { Job } from "../_lib/types";

function scoreToPct(score: number | null | undefined) {
  if (score === null || score === undefined) return "—";
  return `${Math.round(score * 100)}%`;
}

export default function DashboardPage() {
  const router = useRouter();

  const [statusView, setStatusView] = useState<"pending" | "applied">("pending");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const selectedJob = useMemo(
    () => jobs.find((j) => j.id === selectedJobId) ?? null,
    [jobs, selectedJobId],
  );

  const [agentState, setAgentState] = useState<{ agent_enabled: boolean; auto_apply_enabled: boolean } | null>(
    null,
  );

  const [loadingJobs, setLoadingJobs] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [rejectNotes, setRejectNotes] = useState("");
  const [coverLoading, setCoverLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [authed, setAuthed] = useState(false);

  const [me, setMe] = useState<{ user_id: number; username: string; notification_email: string } | null>(null);
  const [notifDraft, setNotifDraft] = useState("");
  const [notifSaving, setNotifSaving] = useState(false);

  async function refresh() {
    setLoadingJobs(true);
    setError(null);
    try {
      const limit = statusView === "applied" ? 5000 : 100;
      const jobsRes = await listJobsApi(statusView, limit);
      const nextJobs: Job[] = jobsRes.jobs ?? [];
      setJobs(nextJobs);
      setSelectedJobId((prev) => (prev && nextJobs.some((j) => j.id === prev) ? prev : nextJobs[0]?.id ?? null));
    } catch (err: any) {
      if (err?.status === 401) router.push("/login");
      setError(err?.message ?? "Failed to load jobs");
    } finally {
      setLoadingJobs(false);
    }
  }

  async function refreshAgentState() {
    try {
      const res = await getAgentStateApi();
      setAgentState(res);
    } catch (err: any) {
      if (err?.status === 401) router.push("/login");
      setError(err?.message ?? "Failed to load agent state");
    }
  }

  async function checkAuth() {
    setError(null);
    try {
      const res = await getAgentStateApi();
      setAgentState(res);
      setAuthed(true);
    } catch (err: any) {
      if (err?.status === 401) {
        router.push("/login");
        return;
      }
      setError(err?.message ?? "Failed to load agent state");
      setAuthed(false);
    } finally {
      setAuthChecked(true);
    }
  }

  useEffect(() => {
    checkAuth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!authed) return;
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusView, authed]);

  async function refreshMe() {
    try {
      const m = await getMeApi();
      setMe(m);
      setNotifDraft(m.notification_email ?? "");
    } catch (err: any) {
      if (err?.status === 401) router.push("/login");
    }
  }

  useEffect(() => {
    if (!authed) return;
    refreshMe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed]);

  async function saveNotificationEmail() {
    setNotifSaving(true);
    setError(null);
    try {
      const m = await patchMeApi({ notification_email: notifDraft });
      setMe(m);
    } catch (err: any) {
      if (err?.status === 401) router.push("/login");
      setError(err?.message ?? "Could not save notification email");
    } finally {
      setNotifSaving(false);
    }
  }

  async function act(action: "approve" | "skip" | "reject", jobId: string) {
    if (actionLoading) return;
    setActionLoading(jobId);
    setError(null);
    try {
      if (action === "approve") await approveJobApi(jobId);
      if (action === "skip") await skipJobApi(jobId);
      if (action === "reject") await rejectJobApi(jobId, rejectNotes);
      setRejectNotes("");
      await refresh();
      await refreshAgentState();
    } catch (err: any) {
      if (err?.status === 401) router.push("/login");
      setError(err?.message ?? "Action failed");
    } finally {
      setActionLoading(null);
    }
  }

  async function generateCover(jobId: string) {
    if (coverLoading) return;
    setCoverLoading(true);
    setError(null);
    try {
      await coverLetterApi(jobId);
      await refresh();
    } catch (err: any) {
      if (err?.status === 401) router.push("/login");
      setError(err?.message ?? "Cover letter generation failed");
    } finally {
      setCoverLoading(false);
    }
  }

  async function toggleAgent(payload: { agent_enabled?: boolean; auto_apply_enabled?: boolean }) {
    setError(null);
    try {
      const res = await updateAgentStateApi(payload);
      setAgentState(res);
    } catch (err: any) {
      if (err?.status === 401) router.push("/login");
      setError(err?.message ?? "Failed to update agent state");
    }
  }

  async function runNow() {
    setError(null);
    try {
      await runAgentNowApi();
      await refresh();
      await refreshAgentState();
    } catch (err: any) {
      if (err?.status === 401) router.push("/login");
      setError(err?.message ?? "Run requested failed");
    }
  }

  const applyUrl = selectedJob?.apply_url || (selectedJob?.source ? `https://github.com/${selectedJob.source}` : "");

  if (!authChecked) {
    return (
      <div className="min-h-screen p-6">
        <div className="max-w-2xl mx-auto cyber-panel cyber-chamfer-sm p-6">
          <div className="text-sm text-[var(--mutedForeground)]">Checking access...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-6">
      <div className="max-w-7xl mx-auto">
        <header className="flex flex-col md:flex-row md:items-start md:justify-between gap-4 mb-6">
          <div>
            <h1 className="cyber-glitch text-4xl md:text-5xl" data-text="APPLYD/DASH">
              APPLYD/DASH
            </h1>
            <div className="text-sm text-[var(--mutedForeground)] mt-2">
              Queue control plane · approve → auto-apply
            </div>
          </div>

          <div className="flex gap-3 flex-wrap">
            <button
              className="cyber-button cyber-chamfer-sm cyber-button-ghost"
              onClick={runNow}
              disabled={!agentState?.agent_enabled}
            >
              RUN SCAN NOW
            </button>
          </div>
        </header>

        <div className="grid gap-6 lg:grid-cols-[1.15fr_0.85fr] items-start">
          {/* Left: queue */}
          <section className="cyber-panel cyber-chamfer-sm p-4 overflow-hidden">
            <div className="flex items-center justify-between mb-4">
              <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)]">
                {statusView === "pending" ? "Pending Queue" : "Applied History"}
              </div>
              <span className="cyber-badge">{jobs.length} jobs</span>
            </div>

            {loadingJobs ? (
              <div className="text-sm text-[var(--mutedForeground)]">Scanning...</div>
            ) : jobs.length === 0 ? (
              <div className="text-sm text-[var(--mutedForeground)]">
                {statusView === "pending" ? "No pending jobs right now." : "No applied jobs yet."}
              </div>
            ) : (
              <ul className="space-y-2">
                {jobs.map((job) => {
                  const isSelected = job.id === selectedJobId;
                  return (
                    <li key={job.id}>
                      <button
                        type="button"
                        className="w-full text-left border border-[var(--border)] p-3 cyber-chamfer-sm transition-all hover:border-[var(--accent)]"
                        onClick={() => setSelectedJobId(job.id)}
                        style={{
                          boxShadow: isSelected ? "var(--box-shadow-neon)" : "none",
                          borderColor: isSelected ? "var(--accent)" : undefined,
                          backgroundColor: "color-mix(in srgb, var(--muted) 60%, transparent)",
                        }}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            <div className="text-sm font-bold uppercase truncate">{job.company}</div>
                            <div className="text-xs text-[var(--mutedForeground)] truncate mt-1">{job.title}</div>
                          </div>
                          <span className="cyber-badge">{scoreToPct(job.score)}</span>
                        </div>
                        <div className="text-xs text-[var(--mutedForeground)] mt-2">
                          {job.location ? job.location : job.is_remote ? "Remote" : "—"}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {/* Right: details */}
          <section className="cyber-panel cyber-chamfer-sm p-4">
            {!selectedJob ? (
              <div className="text-sm text-[var(--mutedForeground)]">
                Select a job from the queue.
              </div>
            ) : (
              <>
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)]">
                      {selectedJob.company}
                    </div>
                    <div className="mt-1 cyber-glitch text-2xl" data-text={selectedJob.title}>
                      {selectedJob.title}
                    </div>
                    <div className="text-sm text-[var(--mutedForeground)] mt-2">
                      {selectedJob.is_remote ? "🌐 Remote" : selectedJob.location || "—"}
                    </div>
                  </div>
                  <span className="cyber-badge">{scoreToPct(selectedJob.score)}</span>
                </div>

                <div className="mt-4 border-t border-[var(--border)] pt-4">
                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-2">
                    Match reasons
                  </div>
                  <div className="space-y-1">
                    {(selectedJob.match_reasons ?? []).slice(0, 6).map((r, idx) => (
                      <div key={`${selectedJob.id}-r-${idx}`} className="text-sm">
                        • {r}
                      </div>
                    ))}
                  </div>
                </div>

                <div className="mt-4">
                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-2">
                    Cover letter
                  </div>
                  {selectedJob.cover_letter ? (
                    <pre
                      className="whitespace-pre-wrap text-sm leading-relaxed border border-[var(--border)] p-3 cyber-chamfer-sm"
                      style={{ backgroundColor: "color-mix(in srgb, var(--muted) 55%, transparent)" }}
                    >
                      {selectedJob.cover_letter}
                    </pre>
                  ) : (
                    <div
                      className="text-sm text-[var(--mutedForeground)] border border-[var(--border)] p-3 cyber-chamfer-sm"
                      style={{ backgroundColor: "color-mix(in srgb, var(--muted) 35%, transparent)" }}
                    >
                      No cover letter yet.
                    </div>
                  )}

                  {statusView === "pending" ? (
                    <button
                      className="cyber-button cyber-chamfer-sm w-full mt-3"
                      onClick={() => generateCover(selectedJob.id)}
                      disabled={coverLoading}
                    >
                      {coverLoading ? "GENERATING..." : "GENERATE"}
                    </button>
                  ) : null}
                </div>

                <div className="mt-4">
                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-2">
                    Actions
                  </div>
                  {statusView === "applied" ? (
                    <div className="text-sm text-[var(--mutedForeground)]">
                      Applied job — details below.
                    </div>
                  ) : (
                    <>
                      <div className="grid grid-cols-3 gap-2">
                        <button
                          className="cyber-button cyber-chamfer-sm"
                          onClick={() => act("approve", selectedJob.id)}
                          disabled={actionLoading === selectedJob.id}
                        >
                          APPROVE
                        </button>
                        <button
                          className="cyber-button cyber-chamfer-sm cyber-button-ghost"
                          onClick={() => act("skip", selectedJob.id)}
                          disabled={actionLoading === selectedJob.id}
                        >
                          SKIP
                        </button>
                        <button
                          className="cyber-button cyber-chamfer-sm cyber-button-destructive"
                          onClick={() => act("reject", selectedJob.id)}
                          disabled={actionLoading === selectedJob.id}
                        >
                          REJECT
                        </button>
                      </div>

                      <div className="mt-3">
                        <input
                          className="cyber-input"
                          value={rejectNotes}
                          onChange={(e) => setRejectNotes(e.target.value)}
                          placeholder="Reject notes (optional)"
                        />
                      </div>
                    </>
                  )}

                  <div className="mt-3">
                    <a
                      className="cyber-button cyber-chamfer-sm w-full inline-flex items-center justify-center"
                      href={applyUrl || "#"}
                      target="_blank"
                      rel="noreferrer"
                    >
                      OPEN APPLY URL
                    </a>
                  </div>

                  {statusView === "applied" ? (
                    <div className="mt-4 border-t border-[var(--border)] pt-4">
                      <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-2">
                        Job description
                      </div>
                      <pre
                        className="whitespace-pre-wrap text-sm leading-relaxed border border-[var(--border)] p-3 cyber-chamfer-sm"
                        style={{
                          backgroundColor: "color-mix(in srgb, var(--muted) 55%, transparent)",
                          maxHeight: 260,
                          overflow: "auto",
                        }}
                      >
                        {selectedJob.body || "No description stored."}
                      </pre>

                      <div className="mt-4 text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-2">
                        Resume submitted
                      </div>
                      <div
                        className="text-sm text-[var(--mutedForeground)] border border-[var(--border)] p-3 cyber-chamfer-sm"
                        style={{ backgroundColor: "color-mix(in srgb, var(--muted) 35%, transparent)" }}
                      >
                        <div className="font-mono break-all">
                          {selectedJob.resume_pdf_path
                            ? selectedJob.resume_pdf_path
                            : "No resume_pdf_path recorded."}
                        </div>
                        {selectedJob.applied_at ? (
                          <div className="mt-2 text-xs text-[var(--mutedForeground)]">
                            Applied at: {new Date(selectedJob.applied_at).toLocaleString()}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>
              </>
            )}
          </section>
        </div>

        {/* Notification email (per logged-in user) */}
        <section className="mt-6 cyber-panel cyber-chamfer-sm p-4">
          <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-2">
            Your notification email
          </div>
          <p className="text-sm text-[var(--mutedForeground)] mb-3">
            Match digests and approval-request emails go here when you use <strong>Run scan</strong>. The background
            worker sends to every user address below plus <code className="text-xs">NOTIFY_EMAIL</code> on the server.
          </p>
          {me ? (
            <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
              <span className="text-xs text-[var(--mutedForeground)] shrink-0">@{me.username}</span>
              <input
                className="cyber-input flex-1 min-w-0"
                value={notifDraft}
                onChange={(e) => setNotifDraft(e.target.value)}
                placeholder="you@example.com"
                type="email"
                autoComplete="email"
              />
              <button
                type="button"
                className="cyber-button cyber-chamfer-sm cyber-button-secondary shrink-0"
                onClick={() => void saveNotificationEmail()}
                disabled={notifSaving}
              >
                {notifSaving ? "SAVING..." : "SAVE"}
              </button>
            </div>
          ) : (
            <div className="text-sm text-[var(--mutedForeground)]">Loading profile…</div>
          )}
        </section>

        {/* Controls */}
        <section className="mt-6 cyber-panel cyber-chamfer-sm p-4">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div>
              <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)]">
                Agent Controls
              </div>
              <div className="text-sm text-[var(--mutedForeground)] mt-1">
                Toggle scanning and Phase 2 auto-apply.
              </div>
            </div>

            <div className="flex gap-3 flex-wrap">
              <button
                className={`cyber-button cyber-chamfer-sm ${statusView === "pending" ? "" : "cyber-button-ghost"}`}
                onClick={() => setStatusView("pending")}
              >
                PENDING
              </button>
              <button
                className={`cyber-button cyber-chamfer-sm ${statusView === "applied" ? "" : "cyber-button-ghost"}`}
                onClick={() => setStatusView("applied")}
              >
                APPLIED
              </button>
              <button
                className={`cyber-button cyber-chamfer-sm ${agentState?.agent_enabled ? "" : "cyber-button-ghost"}`}
                onClick={() => toggleAgent({ agent_enabled: !agentState?.agent_enabled })}
                disabled={!agentState}
              >
                AGENT {agentState?.agent_enabled ? "ON" : "OFF"}
              </button>
              <button
                className={`cyber-button cyber-chamfer-sm cyber-button-secondary ${agentState?.auto_apply_enabled ? "" : "cyber-button-ghost"}`}
                onClick={() => toggleAgent({ auto_apply_enabled: !agentState?.auto_apply_enabled })}
                disabled={!agentState}
              >
                AUTO-APPLY {agentState?.auto_apply_enabled ? "ON" : "OFF"}
              </button>
              <button
                className="cyber-button cyber-chamfer-sm cyber-button-ghost"
                onClick={runNow}
                disabled={!agentState?.agent_enabled}
              >
                RUN NOW
              </button>
            </div>
          </div>
        </section>

        {error ? (
          <div className="mt-4 text-sm text-red-400 border border-red-400/40 p-3 cyber-chamfer-sm">
            {error}
          </div>
        ) : null}
      </div>
    </div>
  );
}

