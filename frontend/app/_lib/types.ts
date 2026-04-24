export type Job = {
  id: string;
  /** Dashboard user who owns this queue row (matches server `jobs.owner_user_id`). */
  owner_user_id: number;
  company: string;
  title: string;
  location: string;
  apply_url: string;
  source: string;
  date_posted: string;
  is_remote: boolean;
  score: number | null;
  match_reasons: string[];
  status: string;
  cover_letter: string | null;
  notes: string | null;
  applied_at: string | null;
  created_at: string | null;
  body?: string | null;
  resume_pdf_path?: string | null;
  resume_generated_at?: string | null;
};

export type JobListResponse = {
  jobs: Job[];
};

export type JobResponse = {
  job: Job;
};

