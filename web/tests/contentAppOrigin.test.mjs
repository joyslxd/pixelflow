import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.CONTENT_APP_ORIGIN_TEST_MODULE;
assert.ok(moduleUrl, "CONTENT_APP_ORIGIN_TEST_MODULE 必须指向已编译模块");
const { contentAppRequestUrl } = await import(moduleUrl);

test("Content-App 同域地址按当前部署 Profile 使用其公开根", () => {
  assert.equal(
    contentAppRequestUrl({
      browserOrigin: "https://video.borgrise.com",
      contentAppOrigin: "https://video.borgrise.com",
      path: "/api/upload",
    }),
    "https://video.borgrise.com/api/upload",
  );
  assert.equal(
    contentAppRequestUrl({
      browserOrigin: "http://creator.vitamazing.top",
      contentAppOrigin: "http://creator.vitamazing.top",
      path: "/api/upload",
    }),
    "http://creator.vitamazing.top/api/upload",
  );
});

test("Content-App 非同域地址保持相对路径并交给同域 Nginx 代理", () => {
  assert.equal(
    contentAppRequestUrl({
      browserOrigin: "http://creator.vitamazing.top",
      contentAppOrigin: "https://test-video.borgrise.com",
      path: "/api/upload",
    }),
    "/api/upload",
  );
});
