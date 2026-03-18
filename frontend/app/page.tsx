import Link from "next/link";

export default function Home() {
  return (
    <div className="min-h-screen flex flex-col cyber-app p-6">
      <div className="max-w-7xl w-full mx-auto">
        <header className="py-8">
          <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-6">
            <div>
              <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)] mb-3">
                APPLYD//JOB AGENT
              </div>
              <h1 className="cyber-glitch text-5xl md:text-7xl" data-text="APPLYD">
                APPLYD
              </h1>
              <p className="text-sm md:text-base text-[var(--mutedForeground)] mt-4 max-w-2xl">
                Watches job repos, scores postings against your profile, tailors your resume, and (optionally) submits applications for you.
              </p>
              <div className="mt-6 flex gap-3 flex-wrap">
                <Link href="/login" className="cyber-button cyber-chamfer-sm">
                  LOGIN
                </Link>
                <Link
                  href="/dashboard"
                  className="cyber-button cyber-chamfer-sm cyber-button-ghost"
                  prefetch={false}
                >
                  OPEN DASHBOARD
                </Link>
              </div>
            </div>

            <div className="cyber-panel cyber-chamfer-sm p-5 w-full md:w-[420px]">
              <div className="text-xs uppercase tracking-[0.2em] text-[var(--mutedForeground)]">
                Status
              </div>
              <div className="mt-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--mutedForeground)]">Job scanning</span>
                  <span className="cyber-badge">ON</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--mutedForeground)]">AI scoring</span>
                  <span className="cyber-badge">Optional</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--mutedForeground)]">Auto-apply</span>
                  <span className="cyber-badge">Requires approval</span>
                </div>
              </div>

              <div className="mt-5 text-xs text-[var(--mutedForeground)]">
                Tip: approve a job in the dashboard to enable Phase 2 on that posting.
              </div>
            </div>
          </div>
        </header>

        <main className="grid lg:grid-cols-3 gap-6 py-6">
          <section className="cyber-panel cyber-chamfer-sm p-5">
            <div className="cyber-glitch text-2xl" data-text="SCAN">
              SCAN
            </div>
            <p className="text-sm text-[var(--mutedForeground)] mt-3">
              Polls multiple GitHub job repos, parses README tables (including embedded HTML), and deduplicates into SQLite.
            </p>
          </section>

          <section className="cyber-panel cyber-chamfer-sm p-5">
            <div className="cyber-glitch text-2xl" data-text="MATCH">
              MATCH
            </div>
            <p className="text-sm text-[var(--mutedForeground)] mt-3">
              Uses a fast heuristic pre-filter and (optionally) Claude scoring to rank fit. Without an Anthropic key, it stays fully offline.
            </p>
          </section>

          <section className="cyber-panel cyber-chamfer-sm p-5">
            <div className="cyber-glitch text-2xl" data-text="APPLY">
              APPLY
            </div>
            <p className="text-sm text-[var(--mutedForeground)] mt-3">
              Generates tailored resume PDFs (LaTeX) at approval time and runs browser automation for Phase 2. Applied jobs show description + resume used.
            </p>
          </section>
        </main>

        <footer className="py-10 text-sm text-[var(--mutedForeground)]">
          <div className="cyber-panel cyber-chamfer-sm p-5">
            <div className="font-mono">
              <span className="text-[var(--accent)]">usage</span>: open the dashboard → approve → auto-apply (optional)
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}
