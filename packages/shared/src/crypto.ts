import { createCipherGCM, createDecipherGCM, randomBytes } from 'crypto';

/**
 * Encryption utility for GitHub tokens using AES-256-GCM
 * 
 * This module provides secure encryption and decryption of GitHub access tokens
 * using AES-256-GCM encryption with authenticated encryption.
 */

const ALGORITHM = 'aes-256-gcm';
const IV_LENGTH = 16; // 128 bits
const TAG_LENGTH = 16; // 128 bits
const KEY_LENGTH = 32; // 256 bits

/**
 * Derives a 256-bit key from the provided encryption key string
 * Uses SHA-256 to ensure consistent key length
 */
function deriveKey(encryptionKey: string): Buffer {
  const crypto = require('crypto');
  return crypto.createHash('sha256').update(encryptionKey).digest();
}

/**
 * Encrypts a GitHub token using AES-256-GCM
 * 
 * @param token - The GitHub access token to encrypt
 * @param encryptionKey - The encryption key (will be hashed to 256 bits)
 * @returns Base64 encoded encrypted data containing IV, encrypted token, and auth tag
 * @throws Error if encryption fails or inputs are invalid
 */
export function encryptGitHubToken(token: string, encryptionKey: string): string {
  if (!token || typeof token !== 'string') {
    throw new Error('Token must be a non-empty string');
  }
  
  if (!encryptionKey || typeof encryptionKey !== 'string') {
    throw new Error('Encryption key must be a non-empty string');
  }

  try {
    // Generate a random IV for each encryption
    const iv = randomBytes(IV_LENGTH);
    
    // Derive the encryption key
    const key = deriveKey(encryptionKey);
    
    // Create cipher
    const cipher = createCipherGCM(ALGORITHM, key, iv);
    
    // Encrypt the token
    let encrypted = cipher.update(token, 'utf8', 'base64');
    encrypted += cipher.final('base64');
    
    // Get the authentication tag
    const tag = cipher.getAuthTag();
    
    // Combine IV, encrypted data, and tag into a single base64 string
    const combined = Buffer.concat([iv, Buffer.from(encrypted, 'base64'), tag]);
    return combined.toString('base64');
  } catch (error) {
    throw new Error(`Failed to encrypt token: ${error instanceof Error ? error.message : 'Unknown error'}`);
  }
}

/**
 * Decrypts a GitHub token using AES-256-GCM
 * 
 * @param encryptedToken - Base64 encoded encrypted data from encryptGitHubToken
 * @param encryptionKey - The encryption key used for encryption
 * @returns The decrypted GitHub access token
 * @throws Error if decryption fails or inputs are invalid
 */
export function decryptGitHubToken(encryptedToken: string, encryptionKey: string): string {
  if (!encryptedToken || typeof encryptedToken !== 'string') {
    throw new Error('Encrypted token must be a non-empty string');
  }
  
  if (!encryptionKey || typeof encryptionKey !== 'string') {
    throw new Error('Encryption key must be a non-empty string');
  }

  try {
    // Decode the combined data
    const combined = Buffer.from(encryptedToken, 'base64');
    
    if (combined.length < IV_LENGTH + TAG_LENGTH + 1) {
      throw new Error('Invalid encrypted token format');
    }
    
    // Extract IV, encrypted data, and tag
    const iv = combined.subarray(0, IV_LENGTH);
    const tag = combined.subarray(-TAG_LENGTH);
    const encrypted = combined.subarray(IV_LENGTH, -TAG_LENGTH);
    
    // Derive the encryption key
    const key = deriveKey(encryptionKey);
    
    // Create decipher
    const decipher = createDecipherGCM(ALGORITHM, key, iv);
    decipher.setAuthTag(tag);
    
    // Decrypt the token
    let decrypted = decipher.update(encrypted, undefined, 'utf8');
    decrypted += decipher.final('utf8');
    
    return decrypted;
  } catch (error) {
    throw new Error(`Failed to decrypt token: ${error instanceof Error ? error.message : 'Unknown error'}`);
  }
}

