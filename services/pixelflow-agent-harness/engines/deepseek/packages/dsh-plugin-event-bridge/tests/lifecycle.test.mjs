import assert from "node:assert/strict";
import { apply } from "../dist/index.js";

let released = 0;
let bridge;
const dispose = apply({ provide(_name, value) { bridge = value; return () => { released += 1; }; } });
assert.deepEqual(bridge.publish({ type: "public_summary", text: "正在检查工作区" }), { type: "public_summary", text: "正在检查工作区" });
assert.throws(() => bridge.publish({ type: "response", text: "https://unsafe" }), /public_event_unsafe/);
assert.equal(bridge.drain().length, 1);
dispose();
assert.equal(released, 1);
assert.throws(() => bridge.publish({ type: "response", text: "已完成" }), /event_bridge_disposed/);
