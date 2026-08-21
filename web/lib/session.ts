/**
 * Where the API lives and how we authenticate to it.
 *
 * `brahma serve` opens the browser at `/?token=…`. We stash that token in
 * sessionStorage and strip it from the address bar, so it does not sit in the
 * URL to be shoulder-surfed, copied into a bug report, or leaked through a
 * Referer header. sessionStorage rather than localStorage: the token rotates
 * with the runtime dir, and a stale one in permanent storage is just a
 * confusing 401 later.
 */

const TOKEN_KEY = "brahma.token";

/**
 * In `npm run dev` the page is served by Next on :3000 while the API is on
 * :8787. In production both are the same origin, so this is empty.
 */
export const API_BASE =
  process.env.NODE_ENV === "development" ? "http://127.0.0.1:8787" : "";

export function captureToken(): string {
  if (typeof window === "undefined") return "";

  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    sessionStorage.setItem(TOKEN_KEY, fromUrl);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.toString());
    return fromUrl;
  }
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(TOKEN_KEY);
}

export function wsUrl(): string {
  const base = API_BASE || window.location.origin;
  const url = new URL("/ws", base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("token", getToken());
  return url.toString();
}

/** Authenticated fetch against the REST surface. */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      Authorization: `Bearer ${getToken()}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}
