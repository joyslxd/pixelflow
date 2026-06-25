export const AUTHORIZATION_STORAGE_KEY = "Authorization";
export const CONTENT_APP_AUTH_MESSAGE_TYPE = "CONTENT_APP_AUTHORIZATION";
export const AGENT_USER_MESSAGE_TYPE = "AGENT_USER_MESSAGE";
export const TRUSTED_CONTENT_APP_ORIGINS = ["https://test-video.borgrise.com", "http://localhost:5174"];

declare global {
  interface Window {
    __CONTENT_APP_AUTHORIZATION__?: string;
    __CONTENT_APP_USER_MESSAGE__?: string;
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

export function isTrustedContentAppOrigin(origin: string): boolean {
  if (!origin) return false;
  if (typeof window !== "undefined" && origin === window.location.origin) return true;
  return TRUSTED_CONTENT_APP_ORIGINS.includes(origin);
}

export function setupContentAppAuthorizationListener(): () => void {
  if (typeof window === "undefined") return () => undefined;

  const handleMessage = (event: MessageEvent) => {
    if (!isTrustedContentAppOrigin(event.origin)) return;
    const data = event.data;
    if (!data || typeof data !== "object") return;

    if (data.type === CONTENT_APP_AUTH_MESSAGE_TYPE && typeof data.authorization === "string") {
      saveAuthorization(data.authorization);
      window.__CONTENT_APP_AUTHORIZATION__ = normalizeAuthorization(data.authorization);
      return;
    }

    if (data.type === AGENT_USER_MESSAGE_TYPE && typeof data.content === "string") {
      window.__CONTENT_APP_USER_MESSAGE__ = data.content;
      window.dispatchEvent(new CustomEvent("contentAppUserMessage", { detail: data.content }));
      return;
    }
  };

  window.addEventListener("message", handleMessage);
  return () => window.removeEventListener("message", handleMessage);
}
