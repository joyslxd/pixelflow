/**
 * 解析本批分镜视频总数。
 * 取 scene_video_progress.total 与 generation_jobs 计数的较大值；禁止回落到场景包全量长度
 *（单镜「确认并生成分镜 N」时全量兜底会误报「已启动 14 个」）。
 * 并发生成时 progress.total 可能只是最近一批，需与 jobTotal 取 max。
 */
export function resolveNativeSceneVideoBatchTotal(input: {
  progressTotal?: number | null;
  jobTotal?: number | null;
  finishedCount?: number | null;
  generatingFallback?: number | null;
}): number {
  const progressTotal = Number(input.progressTotal);
  const jobTotal = Number(input.jobTotal);
  const progressOk = Number.isFinite(progressTotal) && progressTotal > 0;
  const jobOk = Number.isFinite(jobTotal) && jobTotal > 0;
  if (progressOk && jobOk) {
    return Math.floor(Math.max(progressTotal, jobTotal));
  }
  if (progressOk) {
    return Math.floor(progressTotal);
  }
  if (jobOk) {
    return Math.floor(jobTotal);
  }
  const finishedCount = Number(input.finishedCount);
  if (Number.isFinite(finishedCount) && finishedCount > 0) {
    return Math.floor(finishedCount);
  }
  const generatingFallback = Number(input.generatingFallback);
  if (Number.isFinite(generatingFallback) && generatingFallback > 0) {
    return Math.floor(generatingFallback);
  }
  return 1;
}
