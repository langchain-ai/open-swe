export interface PRData {
  url: string;
  html_url: string;
  diff_url: string;
  patch_url: string;
  repo_owner: string;
  repo_name: string;
  pr_number: number;
  merge_commit_sha: string;
  pre_merge_commit_sha: string;
  title: string;
  body: string;
  created_at: string;
  merged_at: string;
}

export interface PRProcessResult {
  pr_number: number;
  repo_name: string;
  workspace_id?: string;
  success: boolean;
  evals_found: boolean;
  evals_files: string[];
  error?: string;
  pre_merge_sha?: string;
}
