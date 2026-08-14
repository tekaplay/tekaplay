/**
 * Single HTTP client for the backend. Components never call fetch directly;
 * the standard error envelope is decoded here, the bearer token is attached
 * here, and an expired access token triggers exactly one single-flight
 * refresh (rotating the refresh token) before the request is retried.
 */
const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000/api/v1';

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public retryable: boolean,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export interface AuthHooks {
  getAccess(): string | null;
  getRefresh(): string | null;
  setTokens(tokens: { access: string; refresh: string }): void;
  clear(): void;
}

let auth: AuthHooks | null = null;
let refreshInFlight: Promise<boolean> | null = null;

export function configureAuth(hooks: AuthHooks) {
  auth = hooks;
}

async function rawRequest(path: string, init?: RequestInit): Promise<Response> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const access = auth?.getAccess();
  if (access) headers.Authorization = `Bearer ${access}`;
  return fetch(`${BASE_URL}${path}`, { ...init, headers });
}

async function tryRefresh(): Promise<boolean> {
  const refresh = auth?.getRefresh();
  if (!auth || !refresh) return false;
  refreshInFlight ??= (async () => {
    try {
      const res = await fetch(`${BASE_URL}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) {
        auth?.clear();
        return false;
      }
      const pair = (await res.json()) as { access_token: string; refresh_token: string };
      auth?.setTokens({ access: pair.access_token, refresh: pair.refresh_token });
      return true;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

async function decodeError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null);
  const err = body?.error;
  return new ApiError(
    err?.code ?? 'unknown_error',
    err?.message ?? `Request failed (${res.status})`,
    res.status,
    err?.retryable ?? false,
    err?.details ?? {},
  );
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res = await rawRequest(path, init);
  // A 401 from an auth-flow endpoint (bad credentials on login/register, an
  // already-expired refresh token) means the token itself is unusable —
  // refreshing and retrying would just repeat the same failure.
  if (res.status === 401 && !path.startsWith('/auth/') && auth?.getRefresh()) {
    const refreshed = await tryRefresh();
    if (refreshed) res = await rawRequest(path, init);
  }
  if (!res.ok) throw await decodeError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
export const del = <T>(path: string) => api<T>(path, { method: 'DELETE' });
