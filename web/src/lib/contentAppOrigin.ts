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
