<template>
  <div>
    <h2>系统监控</h2>
    <div class="panel">
      <h3>指标</h3>
      <table>
        <thead><tr><th>计数器</th><th>值</th></tr></thead>
        <tbody>
          <tr v-for="(v, k) in metrics.counters" :key="k">
            <td>{{ k }}</td><td>{{ v }}</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin-top: 14px">延迟</h3>
      <table>
        <thead><tr><th>指标</th><th>n</th><th>P50</th><th>P95</th><th>P99</th></tr></thead>
        <tbody>
          <tr v-for="(v, k) in metrics.latencies" :key="k">
            <td>{{ k }}</td><td>{{ v.n }}</td>
            <td>{{ v.p50?.toFixed(1) }}</td><td>{{ v.p95?.toFixed(1) }}</td><td>{{ v.p99?.toFixed(1) }}</td>
          </tr>
        </tbody>
      </table>
      <button class="primary" style="margin-top: 12px" @click="load">刷新</button>
      <p v-if="error" class="err">{{ error }}</p>
    </div>
    <div class="panel">
      <h3>审计日志（最近 50 条）</h3>
      <table>
        <thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>KB</th><th>IP</th></tr></thead>
        <tbody>
          <tr v-for="l in audit" :key="l.id">
            <td>{{ l.created_at }}</td><td>{{ l.actor }}</td><td>{{ l.action }}</td>
            <td>{{ l.kb_id || "-" }}</td><td>{{ l.ip }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api.js";

const metrics = ref({ counters: {}, latencies: {} });
const audit = ref([]);
const error = ref("");

async function load() {
  error.value = "";
  try {
    metrics.value = await api.metrics();
    audit.value = await api.audit(50);
  } catch (e) {
    error.value = e.message;
  }
}

onMounted(load);
</script>
