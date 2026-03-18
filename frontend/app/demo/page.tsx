"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { ReactNode } from "react";
import { runDemoApi } from "../_lib/api";

function scoreToPct(score: number | null | undefined) {
  if (score === null || score === undefined) return "—";
  return `${Math.round(score * 100)}%`;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="cyber-panel cyber-chamfer-sm p-5">
      <div className="cyber-glitch text-2xl" data-text={title}>
        {title}
      </div>
      <div className="mt-3 text-sm text-[var(--mutedForeground)]">{children}</div>
    </section>
  );
}

export default function DemoPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<any>(null);

  async function runDemo() {
    setLoading(true);
    setError(null);
    try {
      const res = await runDemoApi({ samplePerSource: 5, bodyExcerptChars: 320 });
      setData(res);
    } catch (err: any) {
      setError(err?.message ?? "Demo failed to run");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    runDemo();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const agentSettings = data?.agent_settings ?? null;
  const demo = data?.demo ?? null;
  const samples = data?.samples ?? [];

  const statsLine = useMemo(() => {
    if (!agentSettings) return "";
    const agentEnabled = agentSettings.agent_enabled ? "ON" : "OFF";
    const autoApply = agentSettings.auto_apply_enabled ? "ON" : "OFF";
    return `Agent: ${agentEnabled} · Auto-apply: ${autoApply}`;
  }, [agentSettings]);

  return (
    <div className="min-h-screen flex flex-col cyber-app p-6">
      <div className="max-w-7xl w-full mx-auto">
        <header className="py-8">
          <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-6">
            <div>
              <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-3">
                APPLYD//DEMO (NO LOGIN)
              </div>
              <h1 className="cyber-glitch text-5xl md:text-7xl" data-text="APPLYD">
                APPLYD
              </h1>
              <p className="text-sm md:text-base text-[var(--mutedForeground)] mt-4 max-w-2xl">
                This page simulates what Applyd does end-to-end: scan repos, parse job rows, run heuristic scoring,
                and estimate which auto-apply handler would be used. No applications are submitted.
              </p>
              <div className="mt-6 flex gap-3 flex-wrap">
                <Link href="/login" className="cyber-button cyber-chamfer-sm">
                  LOGIN
                </Link>
                <button
                  className="cyber-button cyber-chamfer-sm cyber-button-ghost"
                  onClick={runDemo}
                  disabled={loading}
                >
                  {loading ? "RUNNING..." : "RUN DEMO"}
                </button>
              </div>
            </div>

            <div className="cyber-panel cyber-chamfer-sm p-5 w-full md:w-[420px]">
              <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)]">Demo Status</div>
              <div className="mt-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--mutedForeground)]">Scoring mode</span>
                  <span className="cyber-badge">{demo?.scoring_mode ?? "—"}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--mutedForeground)]">Tailored resume</span>
                  <span className="cyber-badge">
                    {demo?.tailored_resume_enabled ? "ENABLED" : "DISABLED"}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--mutedForeground)]">Claude key</span>
                  <span className="cyber-badge">{demo?.anthropic_key_configured ? "CONFIGURED" : "OFFLINE"}</span>
                </div>
              </div>
              <div className="mt-5 text-xs text-[var(--mutedForeground)]">
                {statsLine || "Loading agent state..."} · {data?.note ?? ""}
              </div>
            </div>
          </div>
        </header>

        {error ? (
          <div className="cyber-panel cyber-chamfer-sm p-5 border border-red-500/30 text-red-300 mb-6">
            {error}
          </div>
        ) : null}

        {loading ? (
          <div className="text-sm text-[var(--mutedForeground)]">Running demo simulation...</div>
        ) : (
          <main className="grid lg:grid-cols-3 gap-6 py-6">
            <div className="lg:col-span-1 space-y-6">
              <Section title="SCAN">
                Polls multiple GitHub repos, fetches README tables (including embedded HTML tables), and converts
                rows into structured jobs.
              </Section>
              <Section title="MATCH">
                Runs a fast heuristic pre-filter (offline). If an Anthropic key is configured, some jobs may be
                scored further; the demo endpoint uses heuristic-only to avoid any costs.
              </Section>
              <Section title="APPLY (SIMULATED)">
                When you approve jobs in the dashboard, Phase 2 would generate a tailored PDF resume per job and
                then submit via a platform-specific browser handler (for example: `simplify.jobs` targeted
                Submit-click).
              </Section>
            </div>

            <div className="lg:col-span-2 space-y-6">
              <div className="cyber-panel cyber-chamfer-sm p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)]">
                    Sample parsed postings
                  </div>
                  <span className="cyber-badge">{samples.length} sources</span>
                </div>

                <div className="space-y-4">
                  {samples.map((s: any) => (
                    <div key={s.source} className="border border-[var(--border)] p-4 cyber-chamfer-sm">
                      <div className="flex items-center justify-between">
                        <div className="text-sm font-semibold">{s.source}</div>
                        <div className="text-xs text-[var(--mutedForeground)]">{s.jobs?.length ?? 0} jobs</div>
                      </div>

                      <div className="mt-3 space-y-3">
                        {(s.jobs ?? []).map((j: any) => (
                          <div
                            key={j.job_id}
                            className="border border-[var(--border)] rounded-md p-3 cyber-chamfer-sm"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-sm font-semibold truncate">
                                  {j.company} — {j.title}
                                </div>
                                <div className="text-xs text-[var(--mutedForeground)] mt-1">
                                  {j.location || "Location not provided"} · {j.date_posted || "Date unknown"}
                                </div>
                              </div>
                              <div className="flex flex-col items-end">
                                <span className="cyber-badge">{scoreToPct(j.match_score)}</span>
                                <div className="text-[11px] text-[var(--mutedForeground)] mt-1">match score</div>
                              </div>
                            </div>

                            <div className="mt-2 text-xs uppercase tracking-[0.18em] text-[var(--mutedForeground)]">
                              Estimated auto-apply
                            </div>
                            <div className="text-sm text-[var(--mutedForeground)] mt-1">
                              {j.estimated_auto_apply_handler}
                            </div>

                            {j.apply_url ? (
                              <div className="mt-2">
                                <a
                                  href={j.apply_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="cyber-button cyber-chamfer-sm cyber-button-ghost inline-flex items-center"
                                >
                                  OPEN APPLY URL
                                </a>
                              </div>
                            ) : null}

                            {j.match_reasons?.length ? (
                              <div className="mt-3">
                                <div className="text-xs uppercase tracking-[0.18em] text-[var(--mutedForeground)]">
                                  Why this scored
                                </div>
                                <div className="mt-2 text-sm text-[var(--mutedForeground)] space-y-1">
                                  {j.match_reasons.slice(0, 6).map((r: string, idx: number) => (
                                    <div key={`${j.job_id}-${idx}`}>{r}</div>
                                  ))}
                                </div>
                              </div>
                            ) : null}

                            {j.body_excerpt ? (
                              <div className="mt-3">
                                <div className="text-xs uppercase tracking-[0.18em] text-[var(--mutedForeground)]">
                                  Job excerpt
                                </div>
                                <pre className="whitespace-pre-wrap text-sm leading-relaxed border border-[var(--border)] p-3 cyber-chamfer-sm mt-2">
                                  {j.body_excerpt}
                                </pre>
                              </div>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="cyber-panel cyber-chamfer-sm p-5">
                <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-3">
                  What happens after you approve
                </div>
                <div className="text-sm text-[var(--mutedForeground)] space-y-2">
                  <div>
                    1. The backend generates a tailored PDF resume for the approved job (LaTeX compile via `pdflatex`
                    if tailoring is enabled).
                  </div>
                  <div>
                    2. Phase 2 picks only approved jobs and submits using Playwright automation.
                  </div>
                  <div>
                    3. On success, the job status flips to `applied` and the dashboard shows the stored
                    `resume_pdf_path`.
                  </div>
                </div>
              </div>
            </div>
          </main>
        )}
      </div>
    </div>
  );
}

