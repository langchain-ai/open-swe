/**
 * Interface for the Supabase rate limiting table
 */
export interface RateLimitRecord {
  user_identity: string;
  request_count: number;
  created_at: string;
  updated_at: string;
}

