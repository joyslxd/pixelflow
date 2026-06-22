export const AUTHORIZATION_STORAGE_KEY = "Authorization";

declare global {
  interface Window {
    __CONTENT_APP_AUTHORIZATION__?: string;
  }
}

export const AUTH_STORAGE_KEYS = [
  AUTHORIZATION_STORAGE_KEY,
  "authorization",
  "contentAppAuthorization",
  "content_app_authorization",
  "token",
  "access_token",
];

export interface BrowserStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface AuthorizationSources {
  injected?: string;
  localStorage?: BrowserStorageLike;
  sessionStorage?: BrowserStorageLike;
}

export function normalizeAuthorization(raw: string | null | undefined): string {
  const value = (raw || "").trim();
  if (!value) return "";
  return value.toLowerCase().startsWith("bearer ") ? value : `Bearer ${value}`;
}

export function getAuthorizationFromSources(sources: AuthorizationSources): string {
  const injected = normalizeAuthorization(sources.injected);
  if (injected) return injected;

  for (const key of AUTH_STORAGE_KEYS) {
    const local = normalizeAuthorization(sources.localStorage?.getItem(key));
    if (local) return local;
  }

  for (const key of AUTH_STORAGE_KEYS) {
    const session = normalizeAuthorization(sources.sessionStorage?.getItem(key));
    if (session) return session;
  }

  return "";
}

export function saveAuthorization(raw: string, storage: BrowserStorageLike = window.localStorage): string {
  const authorization = normalizeAuthorization(raw);
  if (!authorization) {
    throw new Error("Authorization 不能为空");
  }
  storage.setItem(AUTHORIZATION_STORAGE_KEY, authorization);
  return authorization;
}

export function clearSavedAuthorization(storage: BrowserStorageLike = window.localStorage): void {
  for (const key of AUTH_STORAGE_KEYS) {
    storage.removeItem(key);
  }
}

export function getBrowserAuthorization(): string {
  if (typeof window === "undefined") return "";
  return getAuthorizationFromSources({
    injected: window.__CONTENT_APP_AUTHORIZATION__,
    localStorage: window.localStorage,
    sessionStorage: window.sessionStorage,
  });
}
