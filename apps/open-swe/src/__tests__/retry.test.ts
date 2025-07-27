import { describe, it, expect, jest } from "@jest/globals";
import { withRetry, createRetryWrapper } from "../utils/retry.js";

describe("withRetry", () => {
  it("should return result on first success", async () => {
    const mockFn = jest
      .fn<() => Promise<string>>()
      .mockResolvedValue("success");

    const result = await withRetry(mockFn);

    expect(result).toBe("success");
    expect(mockFn).toHaveBeenCalledTimes(1);
  });

  it("should retry on failure and eventually succeed", async () => {
    const mockFn = jest
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error("fail 1"))
      .mockRejectedValueOnce(new Error("fail 2"))
      .mockResolvedValue("success");

    const result = await withRetry(mockFn);

    expect(result).toBe("success");
    expect(mockFn).toHaveBeenCalledTimes(3);
  });

  it("should use default retries of 3", async () => {
    const mockFn = jest
      .fn<() => Promise<string>>()
      .mockRejectedValue(new Error("always fails"));

    const result = await withRetry(mockFn);
    expect(result).toBeInstanceOf(Error);
    expect((result as Error).message).toBe("always fails");
    expect(mockFn).toHaveBeenCalledTimes(4); // 1 initial + 3 retries
  });

  it("should respect custom retry count", async () => {
    const mockFn = jest
      .fn<() => Promise<string>>()
      .mockRejectedValue(new Error("always fails"));

    const result = await withRetry(mockFn, { retries: 2 });
    expect(result).toBeInstanceOf(Error);
    expect((result as Error).message).toBe("always fails");
    expect(mockFn).toHaveBeenCalledTimes(3); // 1 initial + 2 retries
  });

  it("should respect custom delay", async () => {
    const mockFn = jest
      .fn<() => Promise<string>>()
      .mockRejectedValue(new Error("always fails"));
    const startTime = Date.now();

    const result = await withRetry(mockFn, { retries: 2, delay: 100 });
    const endTime = Date.now();

    expect(result).toBeInstanceOf(Error);
    expect((result as Error).message).toBe("always fails");
    expect(mockFn).toHaveBeenCalledTimes(3);
    expect(endTime - startTime).toBeGreaterThanOrEqual(200); // 2 delays of 100ms each
  });

  it("should not delay with default delay of 0", async () => {
    const mockFn = jest
      .fn<() => Promise<string>>()
      .mockRejectedValue(new Error("always fails"));
    const startTime = Date.now();

    const result = await withRetry(mockFn, { retries: 2 });
    const endTime = Date.now();

    expect(result).toBeInstanceOf(Error);
    expect((result as Error).message).toBe("always fails");
    expect(mockFn).toHaveBeenCalledTimes(3);
    expect(endTime - startTime).toBeLessThan(50); // Should be very fast with no delay
  });

  it("should handle non-Error objects", async () => {
    const mockFn = jest
      .fn<() => Promise<string>>()
      .mockRejectedValue("string error");

    const result = await withRetry(mockFn, { retries: 1 });
    expect(result).toBeInstanceOf(Error);
    expect((result as Error).message).toBe("string error");
    expect(mockFn).toHaveBeenCalledTimes(2);
  });

  it("should return the last error after all retries", async () => {
    const error1 = new Error("first error");
    const error2 = new Error("second error");
    const lastError = new Error("last error");

    const mockFn = jest
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(error1)
      .mockRejectedValueOnce(error2)
      .mockRejectedValue(lastError);

    const result = await withRetry(mockFn, { retries: 2 });
    expect(result).toBeInstanceOf(Error);
    expect((result as Error).message).toBe("last error");
    expect(mockFn).toHaveBeenCalledTimes(3);
  });

  it("should work with async functions that return different types", async () => {
    const numberFn = jest.fn<() => Promise<number>>().mockResolvedValue(42);
    const objectFn = jest
      .fn<() => Promise<{ key: string }>>()
      .mockResolvedValue({ key: "value" });
    const arrayFn = jest
      .fn<() => Promise<number[]>>()
      .mockResolvedValue([1, 2, 3]);

    expect(await withRetry(numberFn)).toBe(42);
    expect(await withRetry(objectFn)).toEqual({ key: "value" });
    expect(await withRetry(arrayFn)).toEqual([1, 2, 3]);
  });
});

describe("createRetryWrapper", () => {
  it("should create a wrapper that retries with default options", async () => {
    const originalFn = jest
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error("fail"))
      .mockResolvedValue("success");

    const wrappedFn = createRetryWrapper(originalFn);
    const result = await wrappedFn();

    expect(result).toBe("success");
    expect(originalFn).toHaveBeenCalledTimes(2);
  });

  it("should create a wrapper that retries with custom options", async () => {
    const originalFn = jest
      .fn<() => Promise<string>>()
      .mockRejectedValue(new Error("always fails"));

    const wrappedFn = createRetryWrapper(originalFn, { retries: 1 });

    const result = await wrappedFn();
    expect(result).toBeInstanceOf(Error);
    expect((result as Error).message).toBe("always fails");
    expect(originalFn).toHaveBeenCalledTimes(2); // 1 initial + 1 retry
  });

  it("should preserve function arguments", async () => {
    const originalFn = jest
      .fn<(a: string, b: string, c: number) => Promise<string>>()
      .mockResolvedValue("success");

    const wrappedFn = createRetryWrapper(originalFn);
    const result = await wrappedFn("arg1", "arg2", 123);

    expect(result).toBe("success");
    expect(originalFn).toHaveBeenCalledWith("arg1", "arg2", 123);
  });

  it("should work with functions that have multiple parameters", async () => {
    const originalFn = jest.fn((a: string, b: number, c: boolean) =>
      Promise.resolve(`${a}-${b}-${c}`),
    );

    const wrappedFn = createRetryWrapper(originalFn);
    const result = await wrappedFn("test", 42, true);

    expect(result).toBe("test-42-true");
    expect(originalFn).toHaveBeenCalledWith("test", 42, true);
  });

  it("should retry with the same arguments on each attempt", async () => {
    const originalFn = jest
      .fn<(a: string, b: string) => Promise<string>>()
      .mockRejectedValueOnce(new Error("fail"))
      .mockResolvedValue("success");

    const wrappedFn = createRetryWrapper(originalFn);
    await wrappedFn("arg1", "arg2");

    expect(originalFn).toHaveBeenCalledTimes(2);
    expect(originalFn).toHaveBeenNthCalledWith(1, "arg1", "arg2");
    expect(originalFn).toHaveBeenNthCalledWith(2, "arg1", "arg2");
  });
});
