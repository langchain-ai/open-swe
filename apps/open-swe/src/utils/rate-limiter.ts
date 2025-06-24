import { createSupabaseClient } from "./supabase.js";
import { RateLimitRecord } from "./types.js";

const RATE_LIMIT_TABLE = "rate_limits";
const MAX_REQUESTS = 15;

/**
 * Fetch the current request count for a user
 * @param userIdentity - The user's identity string
 * @returns The current request count, or 0 if no record exists
 */
export async function getUserRequestCount(userIdentity: string): Promise<number> {
  const supabase = createSupabaseClient();
  
  const { data, error } = await supabase
    .from(RATE_LIMIT_TABLE)
    .select("request_count")
    .eq("user_identity", userIdentity)
    .single();

  if (error) {
    // If no record exists, return 0
    if (error.code === "PGRST116") {
      return 0;
    }
    throw new Error(`Failed to fetch user request count: ${error.message}`);
  }

  return data?.request_count || 0;
}

/**
 * Increment the request count for a user
 * @param userIdentity - The user's identity string
 * @returns The updated request count
 */
export async function incrementUserRequestCount(userIdentity: string): Promise<number> {
  const supabase = createSupabaseClient();
  const now = new Date().toISOString();

  // First, try to get the current record
  const { data: existingRecord } = await supabase
    .from(RATE_LIMIT_TABLE)
    .select("request_count")
    .eq("user_identity", userIdentity)
    .single();

  if (existingRecord) {
    // Update existing record
    const newCount = existingRecord.request_count + 1;
    const { data, error } = await supabase
      .from(RATE_LIMIT_TABLE)
      .update({
        request_count: newCount,
        updated_at: now
      })
      .eq("user_identity", userIdentity)
      .select("request_count")
      .single();

    if (error) {
      throw new Error(`Failed to update user request count: ${error.message}`);
    }

    return data.request_count;
  } else {
    // Create new record
    const { data, error } = await supabase
      .from(RATE_LIMIT_TABLE)
      .insert({
        user_identity: userIdentity,
        request_count: 1,
        created_at: now,
        updated_at: now
      })
      .select("request_count")
      .single();

    if (error) {
      throw new Error(`Failed to create user request count record: ${error.message}`);
    }

    return data.request_count;
  }
}

/**
 * Check if a user has exceeded the rate limit
 * @param userIdentity - The user's identity string
 * @returns true if the user has exceeded the rate limit, false otherwise
 */
export async function checkRateLimit(userIdentity: string): Promise<boolean> {
  const currentCount = await getUserRequestCount(userIdentity);
  return currentCount >= MAX_REQUESTS;
}

