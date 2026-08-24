<template>
  <div class="shell">
    <aside class="nav">
      <h1 class="brand">LocalRAG</h1>
      <router-link to="/">知识库管理</router-link>
      <router-link to="/playground">检索调试台</router-link>
      <router-link to="/keys">API Key</router-link>
      <router-link to="/monitor">系统监控</router-link>
      <router-link to="/eval">评估面板</router-link>
      <div class="spacer"></div>
      <div v-if="me" class="who">{{ me.username }}（{{ me.role }}）</div>
      <button v-if="me" class="logout" @click="doLogout">登出</button>
    </aside>
    <main class="content">
      <router-view @logged-in="refreshMe" />
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, clearCsrfToken, setCsrfToken } from "./api.js";

const router = useRouter();
const me = ref(null);

async function refreshMe() {
  try {
    const data = await api.me();
    me.value = data;
    // 多标签页自愈（代码审查 MEDIUM-1）：以当前会话的 token 覆盖本地存储
    setCsrfToken(data.csrf_token || "");
  } catch {
    clearCsrfToken();
    me.value = null;
    if (router.currentRoute.value.path !== "/login") router.push("/login");
  }
}

async function doLogout() {
  await api.logout();
  clearCsrfToken();
  me.value = null;
  router.push("/login");
}

onMounted(refreshMe);
</script>

<style>
:root {
  --bg: #f5f7fa;
  --panel: #ffffff;
  --ink: #1b2a3a;
  --soft: #5b6b7d;
  --line: #dde4ec;
  --accent: #b6531f;
  --ok: #2e7a45;
  --bad: #b23a2d;
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--ink); }
.shell { display: flex; min-height: 100vh; }
.nav {
  width: 200px; background: #14212f; color: #dfe7ef; padding: 18px 12px;
  display: flex; flex-direction: column; gap: 6px; font-size: 14px;
}
.brand { font-size: 18px; margin-bottom: 14px; letter-spacing: 0.06em; }
.nav a { color: #aebdcc; text-decoration: none; padding: 8px 10px; border-radius: 4px; }
.nav a.router-link-active { background: #22354a; color: #fff; }
.spacer { flex: 1; }
.who { font-size: 12px; color: #8fa2b5; padding: 4px 10px; }
.logout {
  background: none; border: 1px solid #3d5268; color: #c9d5e0; padding: 6px;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.content { flex: 1; padding: 24px 28px; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 18px; margin-bottom: 16px; }
h2 { font-size: 17px; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }
th { color: var(--soft); font-weight: 600; }
button.primary { background: var(--accent); color: #fff; border: none; padding: 7px 14px; border-radius: 4px; cursor: pointer; font-size: 14px; }
input, textarea, select {
  padding: 7px 10px; border: 1px solid var(--line); border-radius: 4px;
  font-size: 14px; width: 100%;
}
.row { display: flex; gap: 10px; margin-bottom: 12px; align-items: flex-end; }
.row > div { flex: 1; }
label { display: block; font-size: 12px; color: var(--soft); margin-bottom: 4px; }
.err { color: var(--bad); font-size: 13px; margin-top: 8px; }
.ok { color: var(--ok); }

/* ---- Shared utility classes ---- */
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.dialog-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 100; }
.dialog { background: var(--panel); border-radius: 8px; padding: 24px 28px; max-width: 520px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.15); }
.dialog h3 { margin: 0 0 16px; font-size: 16px; }
.dialog-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 16px; }
.cancel-btn { background: none; border: 1px solid var(--line); border-radius: 4px; padding: 7px 14px; cursor: pointer; font-size: 14px; color: var(--soft); }
.cancel-btn:hover { background: #f5f5f5; }
.badge { display: inline-block; min-width: 20px; text-align: center; border-radius: 10px; font-size: 11px; padding: 2px 6px; font-weight: 600; }
.badge-fail { background: var(--bad); color: #fff; }
.badge-ok { color: var(--soft); background: transparent; }
.mono { font-family: monospace; }
.empty-hint { text-align: center; color: var(--soft); padding: 16px 0; font-size: 13px; }
</style>
