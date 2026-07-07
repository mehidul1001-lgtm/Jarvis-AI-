import type { ApiErrorBody, TokenPair } from '../types';

const API_BASE = '/api/v1';
const ACCESS_TOKEN_KEY = 'jarvis.access_token';
const REFRESH_TOKEN_KEY = 'jarvis.refresh_token';

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const tokenStore = {
  getAccess: (): string | null => localStorage.getItem(ACCESS_TOKEN_KEY),
  getRefresh: (): string | null => localStorage.getItem(REFRESH_TOKEN_KEY),
  set(pair: TokenPair): void {
    localStorage.setItem(ACCESS_TOKEN_KEY, pair.access_token);
    localStorage.setItem(REFRESH_TOKEN_KEY, pair.refresh_token);
  },
  clear(): void {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  },
};

/** Callback invoked when the session cannot be recovered (forces re-login). */
let onSessionExpired: (() => void) | null = null;
export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler;
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody = {};
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    // Non-JSON error body; fall through to generic message.
  }
  if (body.error) {
    return new ApiError(response.status, body.error.code, body.error.message);
  }
  if (typeof body.detail === 'string') {
    return new ApiError(response.status, 'error', body.detail);
  }
  if (Array.isArray(body.detail) && body.detail.length > 0) {
    const first = body.detail[0] as { msg?: string };
    return new ApiError(response.status, 'validation_error', first.msg ?? 'Validation error');
  }
  return new ApiError(response.status, 'error', `Request failed (${response.status})`);
}

/** Single-flight refresh so concurrent 401s trigger only one refresh call. */
let refreshPromise: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const refreshToken = tokenStore.getRefresh();
      if (!refreshToken) return false;
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!response.ok) {
        tokenStore.clear();
        return false;
      }
      tokenStore.set((await response.json()) as TokenPair);
      return true;
    })().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  auth?: boolean;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true } = options;

  const doFetch = (): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (auth) {
      const token = tokenStore.getAccess();
      if (token) headers.Authorization = `Bearer ${token}`;
    }
    return fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  };

  let response = await doFetch();
  if (response.status === 401 && auth) {
    const recovered = await tryRefresh();
    if (recovered) {
      response = await doFetch();
    } else {
      onSessionExpired?.();
      throw await parseError(response);
    }
  }
  if (!response.ok) {
    if (response.status === 401) onSessionExpired?.();
    throw await parseError(response);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function websocketUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const token = tokenStore.getAccess() ?? '';
  return `${protocol}://${window.location.host}${API_BASE}/ws?token=${encodeURIComponent(token)}`;
}
