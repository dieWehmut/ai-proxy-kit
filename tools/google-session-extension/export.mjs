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

if (typeof document !== "undefined") {
  const button = document.getElementById("export");
  const status = document.getElementById("status");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const cookies = await chrome.cookies.getAll({domain: "google.com"});
      const state = toStorageState(cookies);
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
