import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const moduleUrl = process.env.VIDEO_AGENT_WORKSPACE_PROJECTION_TEST_MODULE;
assert.ok(moduleUrl, "VIDEO_AGENT_WORKSPACE_PROJECTION_TEST_MODULE 必须指向编译后的工作区投影模块");

const {
  applyVideoWorkspaceSnapshot,
  createVideoWorkspaceProjectionState,
  projectVideoWorkspaceSnapshot,
  resolveSelectedSceneId,
  selectSceneEvidence,
  selectVideoAssetPackage,
} = await import(moduleUrl);

function snapshot(revision, title, videoUrl) {
  return {
    workspace_id: "workspace-1",
    conversation_id: "conversation-1",
    revision,
    payload: {
      scenes: [{
        scene_id: "scene-1",
        scene_index: 1,
        title,
        approved_variant_id: `scene-1-v${revision}`,
        variants: [{
          variant_id: `scene-1-v${revision}`,
          artifact_ref: `artifact:scene-1-v${revision}`,
          review_status: "approved",
          selected: true,
          video_url: videoUrl,
        }],
      }],
      assets: [{
        artifact_ref: `artifact:scene-1-v${revision}`,
        media_type: "video",
        url: videoUrl,
        scene_id: "scene-1",
      }],
      qc: {
        "scene-1": {
          status: "resolved",
          issues: ["商品边缘轻微抖动"],
          repair_suggestion: "保持构图并稳定商品边缘",
          evidence_refs: [`artifact:scene-1-v${revision}`],
        },
      },
    },
  };
}

test("asset package and right evidence share one authoritative revision", () => {
  const state = applyVideoWorkspaceSnapshot(
    createVideoWorkspaceProjectionState("conversation-1"),
    projectVideoWorkspaceSnapshot(
      snapshot(3, "第三版镜头", "https://cdn.example.test/scene-v3.mp4"),
      "conversation-1",
    ),
  );

  const assetPackage = selectVideoAssetPackage(state);
  const evidence = selectSceneEvidence(state, "scene-1");
  assert.equal(assetPackage.revision, 3);
  assert.equal(evidence.revision, 3);
  assert.equal(assetPackage.scenes[0].title, evidence.scene.title);
  assert.equal(assetPackage.assets[0].url, evidence.scene.mediaUrl);
});

test("older and conflicting same revision snapshots cannot overwrite current preview", () => {
  const current = applyVideoWorkspaceSnapshot(
    createVideoWorkspaceProjectionState("conversation-1"),
    projectVideoWorkspaceSnapshot(
      snapshot(4, "权威第四版", "https://cdn.example.test/scene-v4.mp4"),
      "conversation-1",
    ),
  );
  const older = applyVideoWorkspaceSnapshot(
    current,
    projectVideoWorkspaceSnapshot(
      snapshot(3, "过期第三版", "https://cdn.example.test/scene-v3.mp4"),
      "conversation-1",
    ),
  );
  const conflicting = applyVideoWorkspaceSnapshot(
    current,
    projectVideoWorkspaceSnapshot(
      snapshot(4, "冲突第四版", "https://cdn.example.test/conflict.mp4"),
      "conversation-1",
    ),
  );

  assert.strictEqual(older, current);
  assert.strictEqual(conflicting, current);
  assert.equal(selectSceneEvidence(current, "scene-1").scene.title, "权威第四版");
});

test("multi-scene selection keeps a valid scene and falls back after scene removal", () => {
  const raw = snapshot(5, "第一条镜头", "https://cdn.example.test/scene-1-v5.mp4");
  raw.payload.scenes.push({
    scene_id: "scene-2",
    scene_index: 2,
    title: "第二条镜头",
    video_url: "https://cdn.example.test/scene-2-v5.mp4",
    variants: [],
  });
  const state = applyVideoWorkspaceSnapshot(
    createVideoWorkspaceProjectionState("conversation-1"),
    projectVideoWorkspaceSnapshot(raw, "conversation-1"),
  );

  assert.equal(resolveSelectedSceneId(state, "scene-2"), "scene-2");
  assert.equal(resolveSelectedSceneId(state, "missing-scene"), "scene-1");
  assert.equal(resolveSelectedSceneId(state, null), "scene-1");
});

test("confirmation card submits persisted identifiers without free-form workflow input", () => {
  const confirmation = readFileSync(
    new URL("../src/features/video-agent/AgentConfirmationCard.tsx", import.meta.url),
    "utf8",
  );
  const evidence = readFileSync(
    new URL("../src/features/video-agent/SceneEvidencePanel.tsx", import.meta.url),
    "utf8",
  );
  const legacyWorkspace = readFileSync(
    new URL("../src/features/legacy-workspace/LegacyWorkspace.tsx", import.meta.url),
    "utf8",
  );
  const hook = readFileSync(
    new URL("../src/features/video-agent/hooks/useVideoAgent.ts", import.meta.url),
    "utf8",
  );
  const storyboardSurface = readFileSync(
    new URL("../src/features/video-agent/VideoAgentStoryboardSurface.tsx", import.meta.url),
    "utf8",
  );

  assert.match(confirmation, /confirmationId/);
  assert.match(confirmation, /stepId/);
  assert.doesNotMatch(confirmation, /textarea/i);
  assert.match(evidence, /重新生成完成/);
  assert.match(evidence, /revision/);
  assert.match(legacyWorkspace, /AgentPlanTimeline/);
  assert.match(legacyWorkspace, /useVideoAgent/);
  assert.match(legacyWorkspace, /SceneEvidencePanel/);
  assert.match(legacyWorkspace, /AgentConfirmationCard/);
  assert.match(legacyWorkspace, /actionAvailable/);
  assert.match(legacyWorkspace, /onEditScene/);
  assert.match(legacyWorkspace, /请修改分镜/u);
  assert.match(hook, /selectedSceneId/);
  assert.match(hook, /selectScene/);
  assert.match(storyboardSurface, /StoryboardPanel/);
  assert.match(legacyWorkspace, /VideoAgentStoryboardSurface/);
  assert.doesNotMatch(legacyWorkspace, /@\/components\/canvas\/StoryboardPanel/);
});
