<template>
  <div>
    <h2>检索调试台</h2>
    <div class="panel">
      <div class="row">
        <div>
          <label>知识库</label>
          <select v-model="kbId">
            <option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
          </select>
        </div>
        <div style="flex: 2">
          <label>查询</label>
          <input v-model="query" @keyup.enter="run" />
        </div>
        <div style="flex: 0">
          <button class="primary" @click="run">检索</button>
        </div>
      </div>
      <div v-if="debug" class="stages">
        <section>
          <h3>① 粗排（dense）</h3>
          <p v-for="d in debug.dense" :key="d.chunk_id" class="hit">
            {{ d.chunk_id.slice(0, 8) }}… · {{ d.score.toFixed(3) }}
          </p>
        </section>
        <section>
          <h3>② 稀疏（BM25）</h3>
          <p v-for="s in debug.sparse" :key="s.chunk_id" class="hit">
            {{ s.chunk_id.slice(0, 8) }}… · {{ s.score.toFixed(3) }}
          </p>
        </section>
        <section>
          <h3>③ 融合（RRF）</h3>
          <div v-for="f in debug.fused" :key="f.chunk_id" class="hit">
            <strong>{{ f.chunk_id.slice(0, 8) }}…</strong>
            <p class="snippet">{{ f.content }}</p>
            <button class="mini" @click="annotate(f.chunk_id, '', true)">✓ 有用</button>
            <button class="mini" @click="annotate(f.chunk_id, '', false)">✗ 无用</button>
          </div>
        </section>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api.js";

const kbs = ref([]);
const kbId = ref("");
const query = ref("");
const debug = ref(null);
const error = ref("");

onMounted(async () => {
  try {
    kbs.value = await api.listKbs();
    kbId.value = kbs.value[0]?.id || "";
  } catch (e) {
    error.value = e.message;
  }
});

async function run() {
  error.value = "";
  try {
    debug.value = await api.searchDebug(kbId.value, query.value);
  } catch (e) {
    error.value = e.message;
  }
}

async function annotate(chunkId, docId, isHelpful) {
  try {
    await api.annotate({
      kb_id: kbId.value,
      query: query.value,
      doc_id: docId,
      chunk_id: chunkId,
      is_helpful: isHelpful,
    });
  } catch (e) {
    error.value = e.message;
  }
}
</script>

<style scoped>
.stages { display: flex; gap: 14px; }
.stages section { flex: 1; }
.hit { font-size: 13px; padding: 5px 0; border-bottom: 1px dashed var(--line); }
.snippet { color: var(--soft); font-size: 12px; margin: 3px 0; }
button.mini { font-size: 12px; margin-right: 6px; cursor: pointer; }
</style>
