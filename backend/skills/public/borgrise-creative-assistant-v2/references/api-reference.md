# Borgrise API Reference (精简版)

完整 API 文档: `https://test-video.borgrise.com/api/doc.html#/home`

## 基础信息

- **Base URL**: `https://test-video.borgrise.com/api`
- **Auth**: `Authorization: Bearer <token>`
- **所有生图/生视频接口都是异步的**: 返回 `taskId`，需要通过 polling 获取结果

## 自定义请求头 (生图/生视频接口必须)

除了 `Authorization` 和 `Content-Type` 外，生图和生视频接口还需要以下自定义 Header：

| Header | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `ModelType` / `modelType` | string | 模型名。图片接口用 `ModelType`，视频接口用 `modelType` | `gpt-image-2`, `seedance-2.0` |
| `billType` | int (string) | 计费类型: `2`=图片(按张), `3`=视频(按秒) | `3` |
| `apiModelParamObj` | JSON string | 模型参数配置 | `{"size":"720p"}` |
| `duration` | int (string) | 时长(秒)，视频填实际秒数，图片填 `1` | `5` |

> **注意**: `channelId` 不需要传，后端自动路由。生图/生视频接口建议带 `projectId` 参数。

---

## 图片生成接口

### POST /api/picture/text_to_image — 文生图

纯文本描述生成图片。**不支持参考图**。

**请求体**:
```json
{
  "prompt": "A minimalist white ceramic coffee mug on an oak table...",
  "negative_prompt": "blurry, low quality, watermark",
  "model": "gpt-image-2",
  "ratio": "1:1",
  "size": "1024x1024",
  "num_images": 1
}
```

**Headers**: `ModelType: gpt-image-2`, `billType: 2`, `duration: 1`, `apiModelParamObj: {"size":"1024x1024"}`

**响应**: `{ "code": 200, "data": { "taskId": "xxx", "status": "PENDING" } }`

---

### POST /api/picture/multi_reference_image_generation?projectId=1 — 参考图生图

使用一张或多张参考图生成新图片。**有参考图时必须用这个接口**。

**请求体**:
```json
{
  "prompt": "Place this product on a marble countertop with natural lighting",
  "reference_image_urls": ["https://example.com/product.jpg"],
  "model": "gpt-image-2",
  "width": 1,
  "height": 1,
  "imageSize": "4K",
  "max_images": 1
}
```

**重要**:
- 比例通过 `width`/`height` 数值传递: `1:1` → `width:1, height:1`, `9:16` → `width:9, height:16`, `16:9` → `width:16, height:9`
- `imageSize` 是输出质量: `4K`, `1080p` 等
- **不要**传 `ratio`、`size`、`num_images` 字段

---

### POST /api/picture/image_edit — 图片编辑

编辑已有图片（换背景、修图等）。

**请求体**:
```json
{
  "image_url": "https://example.com/original.jpg",
  "prompt": "Replace background with clean white studio backdrop",
  "model": "gpt-image-2"
}
```

---

### POST /api/picture/batch_text_to_image — 批量文生图

批量生成多张图片。

**请求体**: `TextToImageRequest` 对象数组

---

## 视频生成接口

### POST /api/video/text-to-video — 文生视频

纯文本描述生成视频。**不支持参考素材**。

**请求体**:
```json
{
  "prompt": "Product showcase video, smooth camera movement...",
  "negative_prompt": "blurry, distorted, low quality",
  "model": "seedance-2.0",
  "duration": 10,
  "ratio": "9:16"
}
```

**Headers**: `modelType: seedance-2.0`, `billType: 3`, `duration: 10`, `apiModelParamObj: {"size":"720p"}`

---

### POST /api/video/image-to-video — 图生视频

上传的图片作为**视频的第一帧**。

**请求体**:
```json
{
  "image_url": "https://example.com/product.jpg",
  "prompt": "Slow zoom in with gentle lighting shift",
  "model": "seedance-2.0",
  "duration": 5,
  "ratio": "9:16"
}
```

---

### POST /api/video/reference-mode-video?projectId=1 — 参考模式生视频

使用参考图片/视频/音频生成视频。参考素材通过 `imageUrls`/`videoUrls`/`audioUrls` 传入。

**请求体**:
```json
{
  "prompt": "Video description...",
  "imageUrls": ["https://example.com/reference.png"],
  "videoUrls": [],
  "audioUrls": [],
  "duration": 10,
  "ratio": "9:16",
  "sound": "on",
  "model": "seedance-2.0",
  "size": "720p",
  "videoCount": 1
}
```

> **图片是参考还是第一帧？** 参考素材 → `reference-mode-video`。精确第一帧 → `image-to-video`。

---

### POST /api/video/extend-video?projectId=108 — 视频延长

延长已有视频。

**请求体**:
```json
{
  "refVideoList": ["https://example.com/existing-video.mp4"],
  "prompt": "将@existing-video.mp4向后延伸，延长内容为[续写描述]",
  "model": "seedance-2.0",
  "duration": 10,
  "size": "720p",
  "ratio": "9:16",
  "sound": "on",
  "videoCount": "1"
}
```

**关键要求**:
- `refVideoList` 是数组，不是单字符串
- prompt 必须包含 `@filename` 引用
- `size` 和 `sound` 必须与第一段保持一致

---

### POST /api/video/merge — 视频合并

合并多个视频片段。

**请求体**:
```json
{
  "projectId": 108,
  "videoUrls": [
    "https://example.com/segment1.mp4",
    "https://example.com/segment2.mp4"
  ]
}
```

---

## 通用接口

### GET /api/task/{taskId}/status — 查询任务状态

**响应状态值**: `PENDING` → `PROCESSING` → `COMPLETED` / `FAILED`

### POST /api/auth/login — 登录获取 Token

```bash
curl -X POST 'https://test-video.borgrise.com/api/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"username": "your-username", "password": "your-password"}'
```

### POST /api/upload — 文件上传

multipart/form-data, 字段名 `file`。

---

## 模型与参数限制

| 模型 | 类型 | 限制 |
|------|------|------|
| `gpt-image-2` | 图片 | 支持比例: `1:1`, `9:16`, `16:9` |
| `seedance-2.0` | 视频 | 单次最大 10s，多次 extend 可拼接更长 |

## 支持的比例

| 比例 | 用途 |
|------|------|
| `1:1` | 电商主图 (淘宝、京东、Amazon) |
| `9:16` | 短视频 (抖音、TikTok)、竖版海报 |
| `16:9` | Banner、横版视频 |

> 不要传 `3:4`、`4:3`、`21:9` 等后端文档未确认支持的比例。
