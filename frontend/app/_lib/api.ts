const API_BASE_URL =
  // IMPORTANT:
  // Next.js inlines `NEXT_PUBLIC_*` variables at build time.
  // On Railway (Docker), calling `http://localhost:8000` from the browser
  // will fail, so we default to same-origin relative `/api/*` calls.
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ?? "";

async function apiFetchJson(path: string, init?: RequestInit) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (res.status === 401) {
    const err: any = new Error("Unauthorized");
    err.status = 401;
    throw err;
  }

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    const err: any = new Error(text || `Request failed: ${res.status}`);
    err.status = res.status;
    throw err;
  }

  return res.json();
}

export async function loginApi(username: string, password: string) {
  return apiFetchJson("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export type RegistrationStatus = {
  allowed: boolean;
  open_registration: boolean;
  invite_required: boolean;
};

export async function getRegistrationStatusApi(): Promise<RegistrationStatus> {
  return apiFetchJson("/api/auth/registration", { method: "GET" });
}

export type RegisterResponse = {
  ok: boolean;
  user_id: number;
  welcome_email_sent: boolean;
  welcome_email_status: string;
};

export async function registerApi(payload: {
  username: string;
  password: string;
  notification_email?: string;
  invite_code?: string;
}): Promise<RegisterResponse> {
  return apiFetchJson("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({
      username: payload.username,
      password: payload.password,
      notification_email: payload.notification_email ?? "",
      invite_code: payload.invite_code ?? "",
    }),
  });
}

export async function getMeApi() {
  return apiFetchJson("/api/me", { method: "GET" });
}

export async function patchMeApi(payload: { notification_email: string }) {
  return apiFetchJson("/api/me", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export type DeleteAccountResponse = {
  ok: boolean;
  deletion_email_sent: boolean;
  deletion_email_status: string;
};

export async function deleteAccountApi(password: string): Promise<DeleteAccountResponse> {
  return apiFetchJson("/api/me/delete", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export async function logoutApi() {
  return apiFetchJson("/api/auth/logout", { method: "POST" });
}

export async function listJobsApi(status: string, limit: number) {
  return apiFetchJson(`/api/jobs?status=${encodeURIComponent(status)}&limit=${limit}`, {
    method: "GET",
  });
}

export async function getJobApi(jobId: string) {
  return apiFetchJson(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "GET" });
}

export async function approveJobApi(jobId: string) {
  return apiFetchJson(`/api/jobs/${encodeURIComponent(jobId)}/approve`, { method: "POST" });
}

export async function skipJobApi(jobId: string) {
  return apiFetchJson(`/api/jobs/${encodeURIComponent(jobId)}/skip`, { method: "POST" });
}

export async function rejectJobApi(jobId: string, notes: string) {
  return apiFetchJson(`/api/jobs/${encodeURIComponent(jobId)}/reject`, {
    method: "POST",
    body: JSON.stringify({ notes }),
  });
}

export async function coverLetterApi(jobId: string) {
  return apiFetchJson(`/api/jobs/${encodeURIComponent(jobId)}/cover-letter`, { method: "POST" });
}

export async function getStatsApi() {
  return apiFetchJson(`/api/stats`, { method: "GET" });
}

export async function getAgentStateApi() {
  return apiFetchJson(`/api/agent/state`, { method: "GET" });
}

export async function updateAgentStateApi(payload: { agent_enabled?: boolean; auto_apply_enabled?: boolean }) {
  return apiFetchJson(`/api/agent/state`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function runAgentNowApi() {
  return apiFetchJson(`/api/agent/run`, { method: "POST" });
}

export async function runDemoApi(payload?: { samplePerSource?: number; bodyExcerptChars?: number }) {
  return apiFetchJson(`/api/demo/run`, {
    method: "POST",
    body: JSON.stringify({
      sample_per_source: payload?.samplePerSource ?? 5,
      body_excerpt_chars: payload?.bodyExcerptChars ?? 300,
    }),
  });
}

