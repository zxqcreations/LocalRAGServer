<template>
  <div>
    <h2>API Key 管理</h2>
    <div class="panel">
      <div class="row">
        <div>
          <label>名称</label>
          <input v-model="name" placeholder="agent-key" />
        </div>
        <div>
          <label>KB 白名单（逗号分隔，* = 全部）</label>
          <input v-model="acl" placeholder="*" />
        </div>
        <div style="flex: 0">
          <button class="primary" @click="issue">签发</button>
        </div>
      </div>
      <p v-if="issuedKey" class="ok">
        明文 Key（仅显示一次，请立即保存）：<code>{{ issuedKey }}</code>
      </p>
      <table>
        <thead><tr><th>名称</th><th>ACL</th><th>过期</th><th>最近使用</th><th></th></tr></thead>
        <tbody>
          <tr v-for="k in keys" :key="k.id">
            <td>{{ k.name }}</td>
            <td>{{ k.kb_acl }}</td>
            <td>{{ k.expires_at || "永不过期" }}</td>
            <td>{{ k.last_used_at || "-" }}</td>
            <td><button class="mini" @click="revoke(k.id)">吊销</button></td>
          </tr>
        </tbody>
      </table>
      <p v-if="error" class="err">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api.js";

const keys = ref([]);
const name = ref("");
const acl = ref("*");
const issuedKey = ref("");
const error = ref("");

async function load() {
  try {
    keys.value = await api.listKeys();
  } catch (e) {
    error.value = e.message;
  }
}

async function issue() {
  error.value = "";
  try {
    const data = await api.createKey(name.value, acl.value.split(",").map((s) => s.trim()));
    issuedKey.value = data.api_key;
    name.value = "";
    await load();
  } catch (e) {
    error.value = e.message;
  }
}

async function revoke(id) {
  try {
    await api.revokeKey(id);
    await load();
  } catch (e) {
    error.value = e.message;
  }
}

onMounted(load);
</script>
