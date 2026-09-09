/** 按 Vite 部署 Profile 选择浏览器直连 Content-App 或同域 /api 代理。 */

type ContentAppRequestUrlInput = {
  browserOrigin: string;
  contentAppOrigin: string;
  path: string;
};

export function contentAppRequestUrl({
  browserOrigin,
  contentAppOrigin,
  path,
}: ContentAppRequestUrlInput): string {
  const origin = contentAppOrigin.trim().replace(/\/+$/u, "");
  if (!origin || browserOrigin !== origin) return path;
  return `${origin}${path.startsWith("/") ? path : `/${path}`}`;
}

export function canonicalUploadedMediaUrl(rawUrl: string): string {
  /** TOS 升 HTTPS；vitamazing 站点无 TLS，必须保持/改回 HTTP。 */

  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return rawUrl;
  }
  const host = parsed.hostname.toLowerCase();
  if (host.endsWith(".tos-cn-beijing.volces.com")) {
    parsed.protocol = "https:";
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  }
  if (host === "vitamazing.top" || host.endsWith(".vitamazing.top")) {
    parsed.protocol = "http:";
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  }
  return rawUrl.replace(/^http:\/\//u, "https://");
}
