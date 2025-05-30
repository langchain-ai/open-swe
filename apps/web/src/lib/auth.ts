import { cookies } from 'next/headers';

const GITHUB_TOKEN_COOKIE = 'github_access_token';
const GITHUB_TOKEN_TYPE_COOKIE = 'github_token_type';
const GITHUB_TOKEN_SCOPE_COOKIE = 'github_token_scope';

// Token expiration: 30 days (GitHub tokens don't expire by default, but we set a reasonable limit)
const TOKEN_EXPIRY_DAYS = 30;

export interface GitHubTokenData {
  access_token: string;
  token_type: string;
  scope: string;
}

/**
 * Stores GitHub OAuth token data in secure HTTP-only cookies
 */
export async function storeGitHubToken(tokenData: GitHubTokenData): Promise<void> {
  const cookieStore = await cookies();
  const expiryDate = new Date();
  expiryDate.setDate(expiryDate.getDate() + TOKEN_EXPIRY_DAYS);

  const cookieOptions = {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax' as const,
    expires: expiryDate,
    path: '/',
  };

  // Store token components in separate cookies for better security
  cookieStore.set(GITHUB_TOKEN_COOKIE, tokenData.access_token, cookieOptions);
  cookieStore.set(GITHUB_TOKEN_TYPE_COOKIE, tokenData.token_type || 'bearer', cookieOptions);
  cookieStore.set(GITHUB_TOKEN_SCOPE_COOKIE, tokenData.scope || '', cookieOptions);
}

/**
 * Retrieves GitHub OAuth token data from cookies
 */
export async function getGitHubToken(): Promise<GitHubTokenData | null> {
  try {
    const cookieStore = await cookies();
    
    const accessToken = cookieStore.get(GITHUB_TOKEN_COOKIE)?.value;
    const tokenType = cookieStore.get(GITHUB_TOKEN_TYPE_COOKIE)?.value;
    const scope = cookieStore.get(GITHUB_TOKEN_SCOPE_COOKIE)?.value;

    if (!accessToken) {
      return null;
    }

    return {
      access_token: accessToken,
      token_type: tokenType || 'bearer',
      scope: scope || '',
    };
  } catch (error) {
    console.error('Error retrieving GitHub token:', error);
    return null;
  }
}

/**
 * Removes GitHub OAuth token data from cookies (logout)
 */
export async function clearGitHubToken(): Promise<void> {
  const cookieStore = await cookies();
  
  const cookieOptions = {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax' as const,
    expires: new Date(0), // Expire immediately
    path: '/',
  };

  cookieStore.set(GITHUB_TOKEN_COOKIE, '', cookieOptions);
  cookieStore.set(GITHUB_TOKEN_TYPE_COOKIE, '', cookieOptions);
  cookieStore.set(GITHUB_TOKEN_SCOPE_COOKIE, '', cookieOptions);
}

/**
 * Checks if user has a valid GitHub token
 */
export async function isAuthenticated(): Promise<boolean> {
  const token = await getGitHubToken();
  return token !== null && token.access_token.length > 0;
}
