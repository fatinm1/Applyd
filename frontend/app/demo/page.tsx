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
  const [samplePerSource, setSamplePerSource] = useState(5);
  const [bodyExcerptChars, setBodyExcerptChars] = useState(320);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  async function runDemo(params?: { samplePerSource?: number; bodyExcerptChars?: number }) {
    setLoading(true);
    setError(null);
    try {
      const res = await runDemoApi({
        samplePerSource: params?.samplePerSource ?? samplePerSource,
        bodyExcerptChars: params?.bodyExcerptChars ?? bodyExcerptChars,
      });
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

  const allJobs = useMemo(() => {
    const flat: any[] = [];
    for (const src of samples ?? []) {
      for (const j of src?.jobs ?? []) {
        flat.push(j);
      }
    }
    return flat;
  }, [samples]);

  const selectedJob = useMemo(() => {
    if (!allJobs.length) return null;
    if (!selectedJobId) return allJobs[0];
    return allJobs.find((j) => j.job_id === selectedJobId) ?? allJobs[0];
  }, [allJobs, selectedJobId]);

  useEffect(() => {
    if (!selectedJobId && allJobs.length > 0) {
      setSelectedJobId(allJobs[0].job_id);
    }
  }, [allJobs, selectedJobId]);

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
                  onClick={() => runDemo({ samplePerSource, bodyExcerptChars })}
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

              <div className="mt-5 border-t border-[var(--border)] pt-4">
                <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)]">Demo Parameters</div>
                <div className="mt-3 space-y-3">
                  <label className="block">
                    <div className="text-sm text-[var(--mutedForeground)]">Samples per source</div>
                    <input
                      className="cyber-input mt-2"
                      type="number"
                      min={1}
                      max={20}
                      value={samplePerSource}
                      onChange={(e) => setSamplePerSource(Math.max(1, Math.min(20, Number(e.target.value))))}
                    />
                  </label>
                  <label className="block">
                    <div className="text-sm text-[var(--mutedForeground)]">Excerpt chars</div>
                    <input
                      className="cyber-input mt-2"
                      type="number"
                      min={100}
                      max={2000}
                      value={bodyExcerptChars}
                      onChange={(e) =>
                        setBodyExcerptChars(Math.max(100, Math.min(2000, Number(e.target.value))))
                      }
                    />
                  </label>
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
                Polls multiple GitHub repos and extracts postings from README tables (markdown pipe tables and
                embedded HTML tables). Each table row becomes a normalized `job` object (company, title, location,
                apply URL, and a stored excerpt of the row/description).
              </Section>
              <Section title="MATCH">
                Runs a cost-safe, offline heuristic pre-filter: role keyword match, location match, and skills
                overlap. In the real agent, Claude can optionally refine scores for promising jobs, but this demo
                endpoint intentionally stays heuristic-only (so it never makes Anthropic/API calls).
              </Section>
              <Section title="RESUME TAILORING (SIMULATED)">
                On a real `approve`, the backend can compile a job-specific resume by replacing marked blocks in
                a LaTeX template, then running `pdflatex` and caching the PDF under `tailored_resumes/&lt;job_id&gt;/resume.pdf`.
                The demo shows the expected attachment path and whether `pdflatex` is available, but it does not compile
                or run any LaTeX.
              </Section>
              <Section title="APPLY (SIMULATED)">
                Phase 2 auto-apply targets only jobs with `status=approved`. It generates/fetches the resume that
                matches that job, fills the cover letter + identity fields, uploads the resume file (when a file input exists),
                and then submits using a domain-specific Playwright handler.

                For `simplify.jobs`, the automation includes a targeted best-effort click on Apply/Submit-like buttons.
              </Section>
              <Section title="COVER LETTER (OFFLINE PREVIEW)">
                The demo previews the cover letter excerpt using a deterministic offline fallback (so it never uses Claude).
                In the real flow, the cover letter is generated at approval time, then stored and used during auto-apply.
              </Section>
              <Section title="AUDIT TRAIL + EMAILS">
                On real successful applications: the agent records the attempt in `application_log`, updates the job to
                `status=applied`, stores the `resume_pdf_path`, and sends an email notification.

                The demo never sends emails; it only explains when those actions happen.
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

                {selectedJob ? (
                  <div className="mb-5 border border-[var(--border)] p-4 cyber-chamfer-sm">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="text-sm font-semibold truncate">
                          {selectedJob.company} — {selectedJob.title}
                        </div>
                        <div className="text-xs text-[var(--mutedForeground)] mt-1">
                          {selectedJob.location || "Location not provided"} · {selectedJob.date_posted || "Date unknown"}
                        </div>
                      </div>
                      <div className="flex flex-col items-end">
                        <span className="cyber-badge">{scoreToPct(selectedJob.match_score)}</span>
                        <div className="text-[11px] text-[var(--mutedForeground)] mt-1">match score</div>
                      </div>
                    </div>

                    <div className="mt-4 grid md:grid-cols-2 gap-4">
                      <div>
                        <div className="text-xs uppercase tracking-[0.18em] text-[var(--mutedForeground)] mb-2">
                          Why this scored
                        </div>
                        {selectedJob.match_reasons?.length ? (
                          <div className="text-sm text-[var(--mutedForeground)] space-y-1">
                            {selectedJob.match_reasons.map((r: string, idx: number) => (
                              <div key={`${selectedJob.job_id}-r-${idx}`}>{r}</div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-sm text-[var(--mutedForeground)]">No reasons stored.</div>
                        )}
                      </div>

                      <div>
                        <div className="text-xs uppercase tracking-[0.18em] text-[var(--mutedForeground)] mb-2">
                          Phase 2 handler (estimate)
                        </div>
                        <div className="text-sm text-[var(--mutedForeground)]">
                          {selectedJob.estimated_auto_apply_handler}
                        </div>

                        <div className="mt-3 text-xs uppercase tracking-[0.18em] text-[var(--mutedForeground)] mb-2">
                          Resume attachment (simulation)
                        </div>
                        <div className="text-sm text-[var(--mutedForeground)]">
                          {selectedJob.resume_tailer_simulation?.expected_attachment_path}
                        </div>
                      </div>
                    </div>

                    <div className="mt-4">
                      <div className="text-xs uppercase tracking-[0.18em] text-[var(--mutedForeground)] mb-2">
                        Job excerpt
                      </div>
                      <pre className="whitespace-pre-wrap text-sm leading-relaxed border border-[var(--border)] p-3 cyber-chamfer-sm mt-2">
                        {selectedJob.body_excerpt || "No excerpt stored."}
                      </pre>
                    </div>

                    <div className="mt-4">
                      <div className="text-xs uppercase tracking-[0.18em] text-[var(--mutedForeground)] mb-2">
                        Cover letter preview (offline)
                      </div>
                      <pre className="whitespace-pre-wrap text-sm leading-relaxed border border-[var(--border)] p-3 cyber-chamfer-sm mt-2 max-h-72 overflow-auto">
                        {selectedJob.simulated_cover_letter_excerpt || "No cover letter preview available."}
                      </pre>
                    </div>

                    <div className="mt-4">
                      <div className="text-xs uppercase tracking-[0.18em] text-[var(--mutedForeground)] mb-2">
                        Resume tailoring simulation
                      </div>
                      <div className="text-sm text-[var(--mutedForeground)] space-y-1">
                        <div>
                          Tailoring enabled: {selectedJob.resume_tailer_simulation?.tailoring_enabled ? "YES" : "NO"}
                        </div>
                        <div>
                          pdflatex available: {selectedJob.resume_tailer_simulation?.pdflatex_available ? "YES" : "NO"}
                        </div>
                        <div>{selectedJob.resume_tailer_simulation?.note}</div>
                      </div>
                    </div>
                  </div>
                ) : null}

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
                            role="button"
                            tabIndex={0}
                            onClick={() => setSelectedJobId(j.job_id)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") setSelectedJobId(j.job_id);
                            }}
                            className={`border border-[var(--border)] rounded-md p-3 cyber-chamfer-sm cursor-pointer transition-all ${
                              selectedJob?.job_id === j.job_id
                                ? "border-[var(--accent)]"
                                : "hover:border-[var(--accent)]/60"
                            }`}
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
                  <div>
                    4. On success, the system records an entry in `application_log` and sends an email notification
                    (both for traceability and audit).
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

