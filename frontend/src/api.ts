// API client for the AthenaScout backend.
//
// Thin wrapper over fetch() that prefixes /api, handles JSON request
// bodies and responses, and throws an Error on any non-2xx response.
// The thrown Error carries a `.status` property (numeric HTTP code) so
// callers can branch on specific failures.
//
// Two app-wide behaviors worth knowing about:
//
//  1. Any 401 response dispatches the `athenascout:auth-required`
//     window event. App.tsx listens for it and drops the user back to
//     the login screen — no callers have to handle the auth case
//     individually.
//
//  2. Every method accepts an optional AbortSignal as its last
//     argument. Pass `controller.signal` and call `controller.abort()`
//     on component unmount / page change to drop in-flight requests.
//     Aborted fetches throw `DOMException("AbortError")`; the helper
//     `api.isAbort(e)` recognizes the shape so call sites can ignore
//     them cleanly.
//
// Usage:
//   import { api } from "./api";
//   const books = await api.get<{ books: Book[] }>("/books");
//   await api.post("/libraries/active", { slug: "my-library" });

import { EVT } from "./types";

export interface ApiError extends Error {
  status?: number;
}

// Throws an Error whose message is FastAPI's `detail` field if available,
// otherwise the bare status code. The error carries a `.status` property
// (numeric HTTP status) so callers can branch on specific failures.
async function _check<T = any>(r: Response): Promise<T> {
  if (r.ok) return r.json() as Promise<T>;
  let detail = String(r.status);
  try {
    const j = await r.json();
    if (j && j.detail) {
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    }
  } catch {
    /* ignore */
  }
  const err: ApiError = new Error(detail);
  err.status = r.status;
  if (r.status === 401) {
    window.dispatchEvent(new CustomEvent(EVT.AuthRequired));
  }
  throw err;
}

export const api = {
  get: async <T = any>(u: string, signal?: AbortSignal): Promise<T> =>
    _check<T>(await fetch(`/api${u}`, signal ? { signal } : undefined)),

  post: async <T = any>(u: string, body?: unknown, signal?: AbortSignal): Promise<T> => {
    const o: RequestInit = { method: "POST" };
    if (body !== undefined) {
      o.headers = { "Content-Type": "application/json" };
      o.body = JSON.stringify(body);
    }
    if (signal) o.signal = signal;
    return _check<T>(await fetch(`/api${u}`, o));
  },

  put: async <T = any>(u: string, body: unknown, signal?: AbortSignal): Promise<T> => {
    const o: RequestInit = {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
    if (signal) o.signal = signal;
    return _check<T>(await fetch(`/api${u}`, o));
  },

  del: async <T = any>(u: string, signal?: AbortSignal): Promise<T> => {
    const o: RequestInit = { method: "DELETE" };
    if (signal) o.signal = signal;
    return _check<T>(await fetch(`/api${u}`, o));
  },

  isAbort: (e: unknown): boolean => {
    if (!e || typeof e !== "object") return false;
    const ex = e as { name?: string; code?: number };
    return ex.name === "AbortError" || ex.code === 20;
  },
};
