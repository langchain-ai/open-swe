import { createClient } from "@supabase/supabase-js";

/**
 * Initialize and export the Supabase client using environment variables
 */
export function createSupabaseClient() {
  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseAnonKey = process.env.SUPABASE_ANON_KEY;

  if (!supabaseUrl || !supabaseAnonKey) {
    throw new Error("SUPABASE_URL and SUPABASE_ANON_KEY environment variables are required");
  }

  return createClient(supabaseUrl, supabaseAnonKey);
}

