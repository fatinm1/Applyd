export type Job = {
  id: string;
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
};

export type JobListResponse = {
  jobs: Job[];
};

export type JobResponse = {
  job: Job;
};

