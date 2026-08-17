---
topic: 分镜故事线误抽成时间元数据
module: video-agent
date: 2026-08-13
keywords:
  - **时间**
  - shot_description
  - storyline
  - _block_shot_fields
  - episode
  - 视频场景包
---

## 结论摘要

场景包镜数/全局素材对了，但分镜 1 故事线/镜头描述变成 `* **时间**: 00:00 - 00:10`：镜块抽取把首行时间元数据当画面摘要。须跳过时间/时长行，优先读画面/剧情/对白，并保留多行正文。

## 相关文件

- `backend/pixelflow/creative/script_shots.py`
- `backend/tests/test_script_shot_extraction.py`

## 核心逻辑

1. `_block_shot_fields`：跳过 `* **时间**:` / 纯时码行
2. 识别 `**画面**:`、`【剧情/动作】`、对白 → storyline / shot_description / narration
3. blueprint 保留成稿镜块正文，不整段压成时间行

## 注意事项

- 已生成的脏场景包需再确认脚本才会重投影
- 全局素材来自 characters，与本修复无关
