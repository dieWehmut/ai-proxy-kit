export function toStorageState(cookies, now = Date.now() / 1000) {
  const selected = cookies.filter(cookie => {
    const host = cookie.domain.replace(/^\./, "").toLowerCase();
    return (host === "google.com" || host.endsWith(".google.com")) &&
      !cookie.partitionKey && (cookie.session || cookie.expirationDate > now);
  });
  if (!selected.some(cookie => /^(SID|__Secure-[13]PSID)$/.test(cookie.name) && cookie.value)) {
    throw new Error("当前浏览器资料没有有效的 Google 登录会话，请切换到已登录的资料。");
  }
  return {
    accountName: "Local Google",
    cookies: selected.map(cookie => ({
      name: cookie.name, value: cookie.value, domain: cookie.domain, path: cookie.path,
      expires: cookie.session ? -1 : cookie.expirationDate,
      httpOnly: cookie.httpOnly, secure: cookie.secure,
      sameSite: ({strict: "Strict", lax: "Lax", no_restriction: "None"})[cookie.sameSite] || "Lax"
    })),
    origins: []
  };
}

// chrome.cookies.getAll without storeId reads the extension's own execution
// context, which resolves to the default store even when the extension is
// running in another Chrome profile. Binding the export to the store that owns
// the active tab keeps multi-profile setups on the profile the user sees.
export function pickCookieStore(tabs, stores) {
  const tab = (tabs || []).find(candidate => Number.isInteger(candidate?.id) && candidate.id >= 0);
  if (tab) {
    const store = (stores || []).find(candidate =>
      Array.isArray(candidate?.tabIds) && candidate.tabIds.includes(tab.id));
    if (!store) {
      throw new Error(`找不到标签页 #${tab.id} 所属的 Cookie 存储，请确认扩展已在当前浏览器资料中启用，然后重新打开本页面。`);
    }
    return store;
  }
  const available = stores || [];
  if (available.length === 1) return available[0];
  if (available.length === 0) {
    throw new Error("浏览器没有返回可用的 Cookie 存储，无法导出 Google 会话。请重新打开本页面后重试。");
  }
  throw new Error(`无法确定当前浏览器资料的 Cookie 存储：没有活动标签页，但浏览器中有 ${available.length} 个存储。请从浏览器窗口打开本页面后重试。`);
}

export async function exportGoogleSession(chromeApi = globalThis.chrome) {
  const tabs = await chromeApi.tabs.query({active: true, currentWindow: true});
  const stores = await chromeApi.cookies.getAllCookieStores();
  const store = pickCookieStore(tabs, stores);
  const cookies = await chromeApi.cookies.getAll({domain: "google.com", storeId: store.id});
  return toStorageState(cookies);
}

if (typeof document !== "undefined") {
  const button = document.getElementById("export");
  const status = document.getElementById("status");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const state = await exportGoogleSession();
      const url = URL.createObjectURL(new Blob([JSON.stringify(state)], {type: "application/json"}));
      const link = document.createElement("a");
      link.href = url;
      link.download = "ai-proxy-kit-google-session.json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = `已导出 ${state.cookies.length} 个 Google Cookie。文件保存在本机下载目录，供本机导入脚本使用。`;
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}
