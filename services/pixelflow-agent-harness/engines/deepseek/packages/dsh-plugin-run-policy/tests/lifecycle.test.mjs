import assert from "node:assert/strict";
import { apply } from "../dist/index.js";

let released = 0;
let policy;
const listeners = new Map();
const dispose = apply({
  provide(_name, value) { policy = value; return () => { released += 1; }; },
  on(name, listener) { listeners.set(name, listener); return () => listeners.delete(name); },
}, { maxModelSteps: 1, maxBusinessTools: 1, deadlineSeconds: 90 });
listeners.get("agent/request")({}, () => "next");
assert.throws(() => listeners.get("agent/request")({}, () => "next"), /max_model_steps/);
listeners.get("tools/pre-execute")({}, () => "next");
dispose();
assert.equal(released, 1);
assert.throws(() => policy.assertBusinessTool(), /cancelled/);
assert.equal(listeners.size, 0);
