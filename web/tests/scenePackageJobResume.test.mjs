import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SCENE_PACKAGE_JOB_RESUME_TEST_MODULE;
assert.ok(moduleUrl, "SCENE_PACKAGE_JOB_RESUME_TEST_MODULE must point to compiled module");

const {
  classifyScenePackageJobResume,
  isTransientScenePackageResumeError,
  scenePackageJobResumeDelayMs,
} = await import(moduleUrl);

test("auth_service_unavailable / 503 retains pending scene package job", () => {
  const error = {
    status: 503,
    message: '503 /flows/video/generate-scene-assets/jobs/x: {"detail":{"code":"auth_service_unavailable"}}',
  };
  assert.equal(isTransientScenePackageResumeError(error), true);
  assert.equal(classifyScenePackageJobResume(error), "retain_pending");
  assert.equal(
    classifyScenePackageJobResume(new Error("content-app 认证服务暂不可用")),
    "retain_pending",
  );
});

test("404 clears missing scene package job", () => {
  assert.equal(classifyScenePackageJobResume({ status: 404, message: "404 not found" }), "clear_not_found");
});

test("scene package resume delay backs off", () => {
  assert.equal(scenePackageJobResumeDelayMs(0), 1000);
  assert.equal(scenePackageJobResumeDelayMs(1), 2000);
  assert.equal(scenePackageJobResumeDelayMs(10), 30_000);
});
