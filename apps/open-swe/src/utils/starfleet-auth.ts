import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "StarfleetAuth");

interface StarfleetTokenResponse {
  access_token: string;
  expires_in: number;
  token_type: string;
}

/**
 * Manages NVIDIA Starfleet authentication for LLM Gateway access
 * Handles token caching, refresh, and error recovery
 */
class StarfleetAuthManager {
  private token: string | null = null;
  private tokenExpiry: number = 0;
  private tokenRefreshPromise: Promise<string> | null = null;

  /**
   * Get a valid access token (cached or fresh)
   * Automatically refreshes if token is expired or about to expire
   */
  async getAccessToken(): Promise<string> {
    // Check if we have a valid cached token (with 60s buffer before expiry)
    if (this.token && Date.now() < this.tokenExpiry - 60000) {
      logger.debug("Using cached Starfleet token", {
        expiresIn: Math.floor((this.tokenExpiry - Date.now()) / 1000) + "s",
      });
      return this.token;
    }

    // If a refresh is already in progress, wait for it
    if (this.tokenRefreshPromise) {
      logger.debug("Waiting for ongoing token refresh");
      return this.tokenRefreshPromise;
    }

    // Start a new token refresh
    this.tokenRefreshPromise = this.refreshToken();

    try {
      const token = await this.tokenRefreshPromise;
      return token;
    } finally {
      this.tokenRefreshPromise = null;
    }
  }

  /**
   * Refresh the Starfleet token
   */
  private async refreshToken(): Promise<string> {
    const tokenUrl = process.env.STARFLEET_TOKEN_URL;
    const clientId = process.env.STARFLEET_ID;
    const clientSecret = process.env.STARFLEET_SECRET;

    if (!tokenUrl || !clientId || !clientSecret) {
      const missingVars = [];
      if (!tokenUrl) missingVars.push("STARFLEET_TOKEN_URL");
      if (!clientId) missingVars.push("STARFLEET_ID");
      if (!clientSecret) missingVars.push("STARFLEET_SECRET");

      throw new Error(
        `Starfleet credentials not configured. Missing: ${missingVars.join(", ")}. ` +
          `Please add these to your .env file.`
      );
    }

    logger.info("Requesting new Starfleet access token", {
      tokenUrl,
      clientId: clientId.substring(0, 15) + "...",
    });

    try {
      // Create Basic Auth header
      const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString(
        "base64"
      );

      const response = await fetch(tokenUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          Authorization: `Basic ${credentials}`,
        },
        body: "grant_type=client_credentials&scope=azureopenai-readwrite",
      });

      if (!response.ok) {
        const errorText = await response.text();
        logger.error("Starfleet auth failed", {
          status: response.status,
          statusText: response.statusText,
          error: errorText,
        });
        throw new Error(
          `Starfleet authentication failed: ${response.status} ${response.statusText}. ` +
            `Check your STARFLEET_ID and STARFLEET_SECRET credentials.`
        );
      }

      const data = (await response.json()) as StarfleetTokenResponse;

      if (!data.access_token) {
        throw new Error("Starfleet response missing access_token");
      }

      // Cache the token
      this.token = data.access_token;
      this.tokenExpiry = Date.now() + data.expires_in * 1000;

      logger.info("Starfleet token acquired successfully", {
        expiresIn: data.expires_in + "s",
        tokenType: data.token_type,
        expiryTime: new Date(this.tokenExpiry).toISOString(),
      });

      return this.token;
    } catch (error) {
      logger.error("Failed to refresh Starfleet token", {
        error: error instanceof Error ? error.message : String(error),
      });
      throw error;
    }
  }

  /**
   * Clear cached token (useful for testing or forced refresh)
   */
  clearToken(): void {
    logger.info("Clearing cached Starfleet token");
    this.token = null;
    this.tokenExpiry = 0;
    this.tokenRefreshPromise = null;
  }

  /**
   * Check if token is currently valid
   */
  hasValidToken(): boolean {
    return this.token !== null && Date.now() < this.tokenExpiry - 60000;
  }

  /**
   * Get token info (for debugging)
   */
  getTokenInfo(): {
    hasToken: boolean;
    expiresIn: number | null;
    expiryTime: string | null;
  } {
    if (!this.token) {
      return {
        hasToken: false,
        expiresIn: null,
        expiryTime: null,
      };
    }

    const expiresIn = Math.max(0, this.tokenExpiry - Date.now());
    return {
      hasToken: true,
      expiresIn: Math.floor(expiresIn / 1000),
      expiryTime: new Date(this.tokenExpiry).toISOString(),
    };
  }
}

// Export singleton instance
export const starfleetAuth = new StarfleetAuthManager();




