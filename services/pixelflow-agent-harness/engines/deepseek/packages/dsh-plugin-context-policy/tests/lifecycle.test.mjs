import assert from "node:assert/strict";
import { apply } from "../dist/index.js";

let released = 0;
let policy;
const dispose = apply({ provide(_name, value) { policy = value; return () => { released += 1; }; } });
policy.validate({ workspace: { revision: 1 } });
assert.throws(() => policy.validate({ provider_token: "forbidden" }), /context_forbidden_field/);
dispose();
assert.equal(released, 1);
