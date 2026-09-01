/** 通过受控 Gateway 读取资产缩略图，浏览器不取得 TOS 原始地址。 */

import { useEffect, useState } from "react";

import { agentApiUrl, agentHeaders } from "@/api/http";

type Props = {
  conversationId: string;
  workspaceId: string;
  assetId: string;
  alt: string;
};

export function WorkspaceAssetThumbnail({ conversationId, workspaceId, assetId, alt }: Props) {
  const [url, setUrl] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    void fetch(
      agentApiUrl(`/conversations/${encodeURIComponent(conversationId)}/workspaces/${encodeURIComponent(workspaceId)}/assets/${encodeURIComponent(assetId)}/thumbnail`),
      { headers: agentHeaders({ Accept: "image/*" }), signal: controller.signal },
    ).then(async (response) => {
      if (!response.ok) throw new Error("thumbnail_unavailable");
      objectUrl = URL.createObjectURL(await response.blob());
      setUrl(objectUrl);
    }).catch(() => undefined);
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [assetId, conversationId, workspaceId]);

  return url ? (
    <img src={url} alt={alt} className="h-16 w-16 shrink-0 rounded-lg border border-line object-cover" />
  ) : (
    <div className="grid h-16 w-16 shrink-0 place-items-center rounded-lg border border-dashed border-line bg-canvas text-[10px] text-ink-soft">图片</div>
  );
}
