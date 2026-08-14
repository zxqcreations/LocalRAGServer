<template>
  <div>
    <h2>评估面板</h2>
    <div class="panel">
      <div class="row">
        <div>
          <label>知识库</label>
          <select v-model="kbId" @change="load">
            <option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
          </select>
        </div>
        <div style="flex: 0">
          <button class="primary" @click="load">加载标注</button>
        </div>
      </div>
      <table>
        <thead><tr><th>查询</th><th>文档</th><th>chunk</th><th>判定</th><th>标注人</th></tr></thead>
        <tbody>
          <tr v-for="a in annotations" :key="a.id">
            <td>{{ a.query }}</td>
            <td>{{ a.doc_id.slice(0, 8) }}</td>
            <td>{{ a.chunk_id.slice(0, 8) }}</td>
            <td>{{ a.is_helpful ? "有用" : "无用" }}</td>
            <td>{{ a.created_by }}</td>
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

const kbs = ref([]);
const kbId = ref("");
const annotations = ref([]);
const error = ref("");

async function load() {
  error.value = "";
  if (!kbId.value) return;
  try {
    annotations.value = await api.listAnnotations(kbId.value);
  } catch (e) {
    error.value = e.message;
  }
}

onMounted(async () => {
  try {
    kbs.value = await api.listKbs();
    kbId.value = kbs.value[0]?.id || "";
    await load();
  } catch (e) {
    error.value = e.message;
  }
});
</script>
