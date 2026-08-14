<template>
  <div>
    <h2>知识库管理</h2>
    <div class="panel">
      <table>
        <thead><tr><th>名称</th><th>类型</th><th>ID</th></tr></thead>
        <tbody>
          <tr v-for="kb in kbs" :key="kb.id">
            <td>{{ kb.name }}</td>
            <td>{{ kb.kb_type }}</td>
            <td>{{ kb.id }}</td>
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
const error = ref("");

onMounted(async () => {
  try {
    kbs.value = await api.listKbs();
  } catch (e) {
    error.value = e.message;
  }
});
</script>
