import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, fetchHealth, normalizeBaseUrl } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("normalizeBaseUrl", () => {
  it("removes trailing slashes so path joining is predictable", () => {
    expect(normalizeBaseUrl("http://localhost:8000/")).toBe("http://localhost:8000");
    expect(normalizeBaseUrl("http://localhost:8000///")).toBe("http://localhost:8000");
  });

  it("leaves a clean URL untouched", () => {
    expect(normalizeBaseUrl("https://api.example.com")).toBe("https://api.example.com");
  });
});

describe("fetchHealth", () => {
  it("returns the parsed body on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          status: "ok",
          version: "0.1.0",
          app_env: "development",
          demo_mode: true,
        }),
      }),
    );

    await expect(fetchHealth()).resolves.toEqual({
      status: "ok",
      version: "0.1.0",
      app_env: "development",
      demo_mode: true,
    });
  });

  it("throws ApiError carrying the status code on an HTTP error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));

    await expect(fetchHealth()).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
    });
  });

  it("throws ApiError when the network call itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await fetchHealth().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toContain("Could not reach the backend");
  });
});
