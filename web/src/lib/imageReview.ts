export interface ImageResultLike {
  ok?: boolean;
  images?: Array<{ url?: string; download_url?: string }>;
  message?: string;
}

export interface ImageRevisionPrepareInput {
  formValues?: Record<string, unknown>;
  selectedDirection?: Record<string, unknown>;
  planMarkdown?: string;
  feedback: string;
}

export interface ImageRevisionPreparePayload {
  form_values: Record<string, unknown>;
  selected_direction: Record<string, unknown>;
  plan_markdown: string;
  revision_feedback: string;
}

export function canAcceptImageResult(result: ImageResultLike | undefined): boolean {
  return Boolean(result?.ok && usableImageCount(result) > 0);
}

export function imageResultSummary(result: ImageResultLike): string {
  if (result.ok) return `${usableImageCount(result)} 张图片已返回`;
  return result.message || "图片生成失败";
}

export function buildImageRevisionPreparePayload(input: ImageRevisionPrepareInput): ImageRevisionPreparePayload {
  return {
    form_values: input.formValues || {},
    selected_direction: input.selectedDirection || {},
    plan_markdown: input.planMarkdown || "",
    revision_feedback: input.feedback.trim(),
  };
}

function usableImageCount(result: ImageResultLike): number {
  return (result.images || []).filter((image) => image.url || image.download_url).length;
}
