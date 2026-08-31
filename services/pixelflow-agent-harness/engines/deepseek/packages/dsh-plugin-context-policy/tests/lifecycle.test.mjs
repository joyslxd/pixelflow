import assert from "node:assert/strict";
import { apply } from "../dist/index.js";

let released = 0;
let policy;
let listener;
let listenerReleased = 0;
const dispose = apply({
  provide(_name, value) { policy = value; return () => { released += 1; }; },
  on(_name, value) { listener = value; return () => { listenerReleased += 1; }; },
});
policy.validate({ workspace: { revision: 1 } });
assert.throws(() => policy.validate({ provider_token: "forbidden" }), /context_forbidden_field/);
listener({ pixelflow_context_projection: { workspace: { revision: 2 } } });
assert.throws(
  () => listener({ pixelflow_context_projection: { api_key: "forbidden" } }),
  /context_forbidden_field/,
);
// Provider 元数据不属于 Gateway 上下文投影，不能导致请求被误拒绝。
listener({ provider: { api_key: "runtime-only" } });
dispose();
assert.equal(released, 1);
assert.equal(listenerReleased, 1);
