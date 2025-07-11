import * as crypto from "node:crypto";

/**
 * Encryption utility for GitHub tokens using AES-256-GCM
 *
 * This module provides secure encryption and decryption of GitHub access tokens
 * using AES-256-GCM encryption with authenticated encryption.
 */

const ALGORITHM = "aes-256-gcm";
const IV_LENGTH = 12; // 96 bits (Standard for GCM)
const TAG_LENGTH = 16; // 128 bits

/**
 * Derives a 256-bit key from the provided encryption key string
 * Uses SHA-256 to ensure consistent key length
 */
function deriveKey(encryptionKey: string): Buffer {
  return crypto.createHash("sha256").update(encryptionKey).digest();
}

/**
 * Encrypts a secret using AES-256-GCM
 *
 * @param secret - The secret to encrypt
 * @param encryptionKey - The encryption key (will be hashed to 256 bits)
 * @returns Base64 encoded encrypted data containing IV, encrypted token, and auth tag
 * @throws Error if encryption fails or inputs are invalid
 */
export function encryptSecret(secret: string, encryptionKey: string): string {
  if (!secret || typeof secret !== "string") {
    throw new Error("Secret must be a non-empty string");
  }

  if (!encryptionKey || typeof encryptionKey !== "string") {
    throw new Error("Encryption key must be a non-empty string");
  }

  try {
    // Generate a random IV for each encryption (12 bytes for GCM)
    const iv = crypto.randomBytes(IV_LENGTH);

    // Derive the encryption key
    const key = deriveKey(encryptionKey);

    // Create cipher
    const cipher = crypto.createCipheriv(ALGORITHM, key, iv);

    // Encrypt the secret
    const encryptedBuffer = Buffer.concat([
      cipher.update(secret, "utf8"),
      cipher.final(),
    ]);

    // Get the authentication tag
    const tag = cipher.getAuthTag();

    // Combine IV, encrypted data, and tag into a single base64 string
    // Format: IV (12 bytes) + EncryptedData + AuthTag (16 bytes)
    const combined = Buffer.concat([iv, encryptedBuffer, tag]);
    return combined.toString("base64");
  } catch (error) {
    throw new Error(
      `Failed to encrypt secret: ${error instanceof Error ? error.message : "Unknown error"}`,
    );
  }
}

/**
 * Decrypts a secret using AES-256-GCM
 *
 * @param encryptedSecret - Base64 encoded encrypted data from encryptSecret
 * @param encryptionKey - The encryption key used for encryption
 * @returns The decrypted secret
 * @throws Error if decryption fails or inputs are invalid
 */
export function decryptSecret(
  encryptedSecret: string,
  encryptionKey: string,
): string {
  if (!encryptedSecret || typeof encryptedSecret !== "string") {
    throw new Error("Encrypted secret must be a non-empty string");
  }

  if (!encryptionKey || typeof encryptionKey !== "string") {
    throw new Error("Encryption key must be a non-empty string");
  }

  try {
    // Decode the combined data
    const combined = Buffer.from(encryptedSecret, "base64");

    // Minimum length: IV_LENGTH + TAG_LENGTH + 1 byte for data
    if (combined.length < IV_LENGTH + TAG_LENGTH + 1) {
      throw new Error(
        "Invalid encrypted secret format: too short or malformed",
      );
    }

    // Extract IV, encrypted data, and tag
    // IV is first IV_LENGTH bytes
    // AuthTag is last TAG_LENGTH bytes
    // Encrypted data is in between
    const iv = combined.subarray(0, IV_LENGTH);
    const tag = combined.subarray(combined.length - TAG_LENGTH);
    const encrypted = combined.subarray(
      IV_LENGTH,
      combined.length - TAG_LENGTH,
    );

    // Derive the encryption key
    const key = deriveKey(encryptionKey);

    // Create decipher
    const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);
    decipher.setAuthTag(tag);

    // Decrypt the token
    // 'encrypted' is a Buffer, so no input encoding is specified for update()
    const decryptedBuffer = Buffer.concat([
      decipher.update(encrypted),
      decipher.final(),
    ]);

    return decryptedBuffer.toString("utf8");
  } catch (error) {
    throw new Error(
      `Failed to decrypt secret: ${error instanceof Error ? error.message : "Unknown error"}`,
    );
  }
}
