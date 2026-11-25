import assert from "node:assert/strict";
import {toStorageState, pickCookieStore, exportGoogleSession} from "../tools/google-session-extension/export.mjs";

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

// Two browser profiles: the store must follow the active tab, not the extension's default store.
const profileDefault = {id: "0", tabIds: [11, 12]};
const profileWork = {id: "9c41f0", tabIds: [21, 22]};
assert.equal(pickCookieStore([{id: 21}], [profileDefault, profileWork]).id, "9c41f0");
assert.equal(pickCookieStore([{id: 12}], [profileDefault, profileWork]).id, "0");
assert.equal(pickCookieStore([{id: 22}, {id: 21}], [profileDefault, profileWork]).id, "9c41f0");

// A tab exists but no store claims it: fail loudly instead of falling back to store "0".
const errorCases = [
  [[{id: 99}], [profileDefault, profileWork]],
  [[{id: 99}], [{id: "0", tabIds: []}]]
];
for (const [tabs, stores] of errorCases) {
  assert.throws(() => pickCookieStore(tabs, stores), /找不到标签页 #99 所属的 Cookie 存储/);
}

// No active tab: fall back only when the choice is unambiguous.
assert.equal(pickCookieStore([], [{id: "3", tabIds: [21]}]).id, "3");
assert.equal(pickCookieStore([], [{id: "3", tabIds: []}]).id, "3");
assert.throws(() => pickCookieStore([], [profileDefault, profileWork]), /没有活动标签页/);
assert.throws(() => pickCookieStore([], []), /没有返回可用的 Cookie 存储/);

function createChrome({tabs, stores, cookies}) {
  const calls = [];
  return {
    calls,
    tabs: {query: async query => { calls.push({api: "tabs.query", query}); return tabs; }},
    cookies: {
      getAllCookieStores: async () => { calls.push({api: "cookies.getAllCookieStores"}); return stores; },
      getAll: async details => { calls.push({api: "cookies.getAll", details}); return cookies; }
    }
  };
}

const liveSignIn = {...signIn, expirationDate: 4102444800};
const chromeApi = createChrome({
  tabs: [{id: 21}],
  stores: [profileDefault, profileWork],
  cookies: [liveSignIn, {...liveSignIn, domain: "google.com.example.org"}, {...liveSignIn, name: "partitioned", partitionKey: {topLevelSite: "https://example.com"}}]
});
const exported = await exportGoogleSession(chromeApi);
assert.deepEqual(chromeApi.calls[0], {api: "tabs.query", query: {active: true, currentWindow: true}});
assert.deepEqual(chromeApi.calls[1], {api: "cookies.getAllCookieStores"});
assert.deepEqual(chromeApi.calls[2], {api: "cookies.getAll", details: {domain: "google.com", storeId: "9c41f0"}});
assert.equal(chromeApi.calls.length, 3);
assert.deepEqual(exported.cookies.map(cookie => cookie.name), ["SID"], "partitionKey and non-Google cookies stay filtered");

const singleStoreChrome = createChrome({tabs: [], stores: [{id: "0", tabIds: []}], cookies: [liveSignIn]});
await exportGoogleSession(singleStoreChrome);
assert.deepEqual(singleStoreChrome.calls[2], {api: "cookies.getAll", details: {domain: "google.com", storeId: "0"}});

const ambiguousChrome = createChrome({tabs: [], stores: [profileDefault, profileWork], cookies: [liveSignIn]});
await assert.rejects(exportGoogleSession(ambiguousChrome), /没有活动标签页/);
assert.ok(!ambiguousChrome.calls.some(call => call.api === "cookies.getAll"), "cookies.getAll is never called without a resolved store");

const orphanTabChrome = createChrome({tabs: [{id: 99}], stores: [profileDefault], cookies: [liveSignIn]});
await assert.rejects(exportGoogleSession(orphanTabChrome), /找不到标签页 #99 所属的 Cookie 存储/);
assert.ok(!orphanTabChrome.calls.some(call => call.api === "cookies.getAll"), "missing store never falls back to the default store");

console.log("Google export: scope, expiration, partitioning, storage-state conversion, multi-profile store selection and no-tab fallback PASS");
