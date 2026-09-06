/**
 * Backend API client.
 *
 * The base URL comes from VITE_API_BASE_URL so a deployed frontend can point at
 * a different origin without a source change. Nothing secret is read here:
 * everything in `import.meta.env` is baked into the browser bundle, so provider
 * keys stay server-side.
 */

/** Strip trailing slashes so path joining is predictable. Exported for testing. */
export function normalizeBaseUrl(raw: string): string {
  return raw.replace(/\/+$/, "");
}

export const API_BASE_URL = normalizeBaseUrl(
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
);

/** Mirrors `HealthResponse` in backend/app/schemas/api/health.py. */
export interface HealthResponse {
  status: string;
  version: string;
  app_env: string;
  demo_mode: boolean;
}

export class ApiError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Fetch backend liveness. Throws `ApiError` when the backend cannot be reached. */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/health`, { signal });
  } catch {
    // A network-level failure. Deliberately not surfacing the raw error text,
    // which varies by browser and says nothing useful to the user.
    throw new ApiError(`Could not reach the backend at ${API_BASE_URL}`);
  }

  if (!response.ok) {
    throw new ApiError(`Backend returned HTTP ${response.status}`, response.status);
  }

  return (await response.json()) as HealthResponse;
}
