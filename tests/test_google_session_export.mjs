import assert from "node:assert/strict";
import {toStorageState} from "../tools/google-session-extension/export.mjs";

const signIn = {name: "SID", value: "synthetic-test-session", domain: ".google.com", path: "/",
  expirationDate: 200, session: false, httpOnly: true, secure: true, sameSite: "lax"};
const state = toStorageState([
  signIn,
  {...signIn, domain: "google.com.example.org"},
  {...signIn, domain: "example.com"},
  {...signIn, name: "expired", expirationDate: 50},
  {...signIn, name: "partitioned", partitionKey: {topLevelSite: "https://example.com"}}
], 100);
assert.deepEqual(state.cookies, [{name: "SID", value: "synthetic-test-session", domain: ".google.com", path: "/",
  expires: 200, httpOnly: true, secure: true, sameSite: "Lax"}]);
assert.throws(() => toStorageState([signIn], 201), /没有有效/);
assert.equal(toStorageState([{...signIn, session: true}], 300).cookies[0].expires, -1);
console.log("Google export: scope, expiration, partitioning and storage-state conversion PASS");
