import { encryptSecret, decryptSecret } from "../crypto.js";

describe("crypto utilities", () => {
  const key = "test-key";

  it("should encrypt and decrypt a secret", () => {
    const secret = "hello world";
    const encrypted = encryptSecret(secret, key);
    expect(encrypted).not.toBe(secret);
    const decrypted = decryptSecret(encrypted, key);
    expect(decrypted).toBe(secret);
  });

  it("should throw for empty secret", () => {
    expect(() => encryptSecret("", key)).toThrow(
      "Secret must be a non-empty string",
    );
  });

  it("should throw for empty encryption key during encryption", () => {
    expect(() => encryptSecret("data", "")).toThrow(
      "Encryption key must be a non-empty string",
    );
  });

  it("should throw for empty encrypted secret", () => {
    expect(() => decryptSecret("", key)).toThrow(
      "Encrypted secret must be a non-empty string",
    );
  });

  it("should throw for empty encryption key during decryption", () => {
    const encrypted = encryptSecret("data", key);
    expect(() => decryptSecret(encrypted, "")).toThrow(
      "Encryption key must be a non-empty string",
    );
  });

  it("should throw for malformed encrypted data", () => {
    const malformed = Buffer.from("short").toString("base64");
    expect(() => decryptSecret(malformed, key)).toThrow(
      /Invalid encrypted secret format/,
    );
  });
});
