<template>
  <div>
    <!-- 面包屑导航 -->
    <nav class="breadcrumb">
      <router-link to="/">知识库管理</router-link> / <strong>{{ kbInfo?.name || '加载中…' }}</strong>
    </nav>

    <p v-if="error" class="err">{{ error }}</p>

    <!-- KB 元数据卡片 -->
    <div v-if="kbInfo" class="panel metadata-card">
      <h3>
        {{ kbInfo.name }}
        <span class="type-tag">{{ typeLabel(kbInfo.kb_type) }}</span>
      </h3>
      <div class="meta-grid">
        <div class="meta-item"><span class="meta-label">ID</span><span class="meta-value mono">{{ kbInfo.id }}</span></div>
        <div class="meta-item"><span class="meta-label">创建时间</span><span class="meta-value">{{ formatDate(kbInfo.created_at) }}</span></div>
        <div class="meta-item"><span class="meta-label">文档数</span><span class="meta-value">{{ kbInfo.doc_count ?? '-' }}</span></div>
        <div class="meta-item"><span class="meta-label">碎片数</span><span class="meta-value">{{ kbInfo.chunk_count ?? '-' }}</span></div>
        <div class="meta-item"><span class="meta-label">失败数</span><span class="meta-value">{{ failureBadge(kbInfo.failed_count) }}</span></div>
      </div>
      <div v-if="kbInfo.description" class="desc-block">
        <span class="meta-label">简介</span>
        <span class="meta-value desc-text">{{ kbInfo.description }}</span>
      </div>
    </div>

    <!-- ===== 文档管理区域 ===== -->
    <div v-if="kbInfo" class="panel doc-section">
      <div class="section-header">
        <h3>文档管理</h3>
        <div class="header-actions">
          <label class="upload-btn">
            上传文档
            <input type="file" ref="fileInput" style="display:none" @change="onFileSelect" />
          </label>
          <button class="refresh-btn" @click="loadDocs()">刷新</button>
          <div v-if="uploading" class="upload-status">上传中 {{ uploadProgress }}%</div>
        </div>
      </div>

      <table v-if="documents.length > 0">
        <thead>
          <tr>
            <th>标题</th>
            <th>状态</th>
            <th>碎片数</th>
            <th>错误</th>
            <th>上传时间</th>
            <th style="width:60px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="doc in documents" :key="doc.id">
            <td>{{ doc.title || '(无标题)' }}</td>
            <td><span :class="statusClass(doc.status)">{{ statusLabel(doc.status) }}</span></td>
            <td>{{ doc.chunk_count || 0 }}</td>
            <td><span v-if="doc.error" class="error-text">{{ doc.error }}</span><span v-else>-</span></td>
            <td>{{ formatDate(doc.created_at) }}</td>
            <td><button class="del-doc-btn" @click="openDeleteDoc(doc)">删除</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="!loadingDocs" class="empty-hint">暂无文档，点击上方"上传文档"按钮添加文件</p>
    </div>

    <!-- ===== URL 订阅区域 ===== -->
    <div v-if="kbInfo" class="panel sub-section">
      <div class="section-header">
        <h3>URL 订阅</h3>
        <div class="header-actions">
          <button v-if="!showSubForm" class="primary" @click="showSubForm = true">新增订阅</button>
        </div>
      </div>

      <!-- 内联表单 -->
      <div v-if="showSubForm" class="panel sub-form-panel">
        <div class="row">
          <div style="flex:2">
            <label>URL</label>
            <input v-model.trim="subForm.url" placeholder="https://example.com/page" />
          </div>
          <div style="flex:0 0 120px">
            <label>间隔（小时）</label>
            <select v-model="subForm.interval_hours">
              <option :value="1">1 小时</option>
              <option :value="24">24 小时</option>
              <option :value="168">7 天</option>
            </select>
          </div>
        </div>
        <div class="dialog-actions" style="margin-top:10px">
          <button class="cancel-btn" @click="closeSubForm()">取消</button>
          <button class="primary" :disabled="creatingSub" @click="doCreateSub()">
            {{ creatingSub ? '添加中…' : '确认添加' }}
          </button>
        </div>
      </div>

      <table v-if="subscriptions.length > 0">
        <thead>
          <tr>
            <th>URL</th>
            <th>间隔</th>
            <th>状态</th>
            <th>上次抓取</th>
            <th style="width:120px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="sub in subscriptions" :key="sub.id">
            <td><a :href="sub.url" target="_blank" rel="noopener">{{ sub.url.slice(0, 60) }}{{ sub.url.length > 60 ? '…' : '' }}</a></td>
            <td>{{ sub.interval_hours }}h</td>
            <td><span :class="sub.enabled ? 'badge badge-ok' : 'badge badge-fail'">{{ sub.enabled ? '启用' : '暂停' }}</span></td>
            <td>{{ sub.last_fetched_at ? formatDate(sub.last_fetched_at) : '-' }}</td>
            <td class="actions-cell">
              <button class="toggle-btn" @click="doToggleSub(sub)">
                {{ sub.enabled ? '暂停' : '启用' }}
              </button>
              <button class="del-btn" @click="doDeleteSub(sub)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="!loadingSubs" class="empty-hint">暂无 URL 订阅</p>
    </div>

    <!-- ========== 删除文档确认对话框 ========== -->
    <div v-if="deletingDoc" class="dialog-overlay" @click.self="closeDeleteDoc()">
      <div class="dialog delete-dialog">
        <h3>⚠️ 确认删除文档</h3>
        <div class="kb-info-card">
          <div class="info-row"><span class="info-label">文档标题</span><span class="info-value warn-text">⚠️ 即将删除此文档</span></div>
          <div class="info-row"><span class="info-label">标题</span><span class="info-value">{{ deletingDoc.title || '(无标题)' }}</span></div>
          <div class="info-row"><span class="info-label">文档 ID</span><span class="info-value id-value">{{ deletingDoc.id }}</span></div>
        </div>
        <p class="confirm-hint">请输入完整 ID 以确认删除：</p>
        <input
          v-model="deleteDocConfirmId"
          placeholder="请粘贴完整的文档 ID（共32位十六进制字符）"
          autocomplete="off"
          spellcheck="false"
          @input="checkDeleteDocMatch()"
        />
        <p v-if="deleteDocError" class="err confirm-error">{{ deleteDocError }}</p>
        <div class="dialog-actions">
          <button class="cancel-btn" @click="closeDeleteDoc()">取消</button>
          <button
            class="danger"
            :disabled="!deleteDocMatchOk"
            @click="doDeleteDoc()"
          >
            {{ deletingDocAction ? '删除中…' : '确认删除' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api, apiUpload } from "../api.js";

const route = useRoute();
const kbId = route.params.kbId;

const kbInfo = ref(null);
const error = ref("");
const uploading = ref(false);
const uploadProgress = ref(0);

// Documents
const documents = ref([]);
const loadingDocs = ref(true);
const fileInput = ref(null);

// Subscriptions
const subscriptions = ref([]);
const loadingSubs = ref(true);
const showSubForm = ref(false);
const creatingSub = ref(false);
const subForm = ref({ url: "", interval_hours: 24 });

// Delete document dialog
const deletingDoc = ref(null);
const deleteDocConfirmId = ref("");
const deleteDocError = ref("");
const deleteDocMatchOk = ref(false);
const deletingDocAction = ref(false);

async function loadKb() {
  try {
    kbInfo.value = await api.getKbDetail(kbId);
  } catch (e) {
    error.value = e.message;
  }
}

async function loadDocs() {
  loadingDocs.value = true;
  try {
    documents.value = await api.listDocs(kbId);
  } catch (e) {
    if (!error.value) error.value = "加载文档列表失败: " + e.message;
  } finally {
    loadingDocs.value = false;
  }
}

async function loadSubscriptions() {
  loadingSubs.value = true;
  try {
    const all = await api.listSubscriptions?.(kbId) || [];
    subscriptions.value = all.filter(s => s.kb_id === kbId);
  } catch {
    // API may not exist yet, silently ignore
  } finally {
    loadingSubs.value = false;
  }
}

onMounted(async () => {
  await Promise.all([loadKb(), loadDocs(), loadSubscriptions()]);
});

// --- File upload ---

async function onFileSelect(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  uploading.value = true;
  uploadProgress.value = 0;
  error.value = "";
  try {
    // 通过 JSON + base64 发送（避免 multipart 经 Vite 代理时 boundary 丢失导致 422）
    const buffer = await file.arrayBuffer();
    const base64 = btoa(new Uint8Array(buffer).reduce((data, byte) => data + String.fromCharCode(byte), ""));
    const data = await api.uploadDocument(kbId, file.name, base64);
    uploadProgress.value = 100;
    await loadDocs();
    await loadKb(); // refresh stats
    if (fileInput.value) fileInput.value.value = "";
  } catch (e) {
    error.value = e.message;
  } finally {
    uploading.value = false;
  }
}

// --- Subscription CRUD ---

async function doCreateSub() {
  const url = subForm.value.url;
  if (!url || !/^https?:\/\//.test(url)) {
    error.value = "请输入有效的 URL";
    return;
  }
  creatingSub.value = true;
  try {
    if (api.createSubscription) {
      await api.createSubscription(kbId, url, subForm.value.interval_hours);
      showSubForm.value = false;
      subForm.value = { url: "", interval_hours: 24 };
      await loadSubscriptions();
    } else {
      error.value = "后端订阅 API 暂未实现";
    }
  } catch (e) {
    error.value = e.message;
  } finally {
    creatingSub.value = false;
  }
}

function closeSubForm() {
  showSubForm.value = false;
  subForm.value = { url: "", interval_hours: 24 };
}

async function doToggleSub(sub) {
  try {
    if (api.toggleSubscription) {
      await api.toggleSubscription(sub.id, !sub.enabled);
      await loadSubscriptions();
    }
  } catch (e) {
    error.value = e.message;
  }
}

async function doDeleteSub(sub) {
  if (!window.confirm(`确定要删除此 URL 订阅吗？`)) return;
  try {
    if (api.deleteSubscription) {
      await api.deleteSubscription(sub.id);
      await loadSubscriptions();
    }
  } catch (e) {
    error.value = e.message;
  }
}

// --- Delete document dialog ---

function openDeleteDoc(doc) {
  deletingDoc.value = { ...doc };
  deleteDocConfirmId.value = "";
  deleteDocError.value = "";
  deleteDocMatchOk.value = false;
}

function checkDeleteDocMatch() {
  const input = deleteDocConfirmId.value.trim();
  const target = deletingDoc.value?.id;
  if (!target) {
    deleteDocMatchOk.value = false;
    deleteDocError.value = "";
    return;
  }
  if (input === "") {
    deleteDocMatchOk.value = false;
    deleteDocError.value = "";
    return;
  }
  if (input === target) {
    deleteDocMatchOk.value = true;
    deleteDocError.value = "";
  } else {
    deleteDocMatchOk.value = false;
    deleteDocError.value = `输入的 ID 与目标不一致`;
  }
}

function closeDeleteDoc() {
  deletingDoc.value = null;
  deleteDocConfirmId.value = "";
  deleteDocError.value = "";
  deleteDocMatchOk.value = false;
}

async function doDeleteDoc() {
  if (!deleteDocMatchOk.value || !deletingDoc.value) return;
  deletingDocAction.value = true;
  try {
    await api.deleteDoc(kbId, deletingDoc.value.id);
    closeDeleteDoc();
    await loadDocs();
    await loadKb();
  } catch (e) {
    error.value = e.message;
  } finally {
    deletingDocAction.value = false;
  }
}

// --- Helpers ---

function formatDate(isoStr) {
  if (!isoStr) return "-";
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return isoStr;
  return d.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function typeLabel(t) {
  const map = { document: "📄 文档", code: "💻 代码", web: "🌐 网页" };
  return map[t] || t;
}

function statusLabel(s) {
  const map = {
    ready: "就绪", failed: "失败", indexed: "索引完成", embedded: "向量化完成",
    chunked: "分块完成", parsed: "解析完成", uploaded: "已上传",
  };
  return map[s] || s;
}

function statusClass(s) {
  const map = {
    ready: "badge badge-ok",
    failed: "badge badge-fail",
  };
  return map[s] || "badge";
}

function failureBadge(n) {
  if (!n || n === 0) return "<span class='badge'>-</span>";
  return `<span class="badge badge-fail">${n}</span>`;
}
</script>

<style scoped>
.breadcrumb { font-size: 13px; margin-bottom: 14px; color: var(--soft); }
.breadcrumb a { color: var(--accent); text-decoration: none; }
.breadcrumb a:hover { text-decoration: underline; }
.breadcrumb strong { color: var(--ink); }

/* ---- metadata card ---- */
.metadata-card h3 { display: flex; align-items: center; gap: 10px; margin: 0 0 14px; font-size: 17px; }
.type-tag { font-size: 12px; font-weight: normal; background: #f0ebe5; color: var(--accent); padding: 2px 8px; border-radius: 4px; }
.meta-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
.meta-item { display: flex; flex-direction: column; gap: 2px; }
.meta-label { font-size: 11px; color: var(--soft); text-transform: uppercase; letter-spacing: 0.04em; }
.meta-value { font-size: 14px; }
.mono { font-family: monospace; font-size: 12px; word-break: break-all; }
.desc-block { margin-top: 12px; display: flex; flex-direction: column; gap: 4px; }
.desc-text { font-size: 13px; color: var(--ink); }

/* ---- section header ---- */
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.section-header h3 { margin: 0; font-size: 15px; }
.header-actions { display: flex; gap: 8px; align-items: center; }

/* ---- upload btn ---- */
.upload-btn {
  background: none; border: 1px dashed var(--line); border-radius: 4px;
  padding: 6px 12px; cursor: pointer; font-size: 13px; color: var(--soft);
}
.upload-btn:hover { border-color: var(--accent); color: var(--accent); }
.refresh-btn {
  background: none; border: 1px solid var(--line); border-radius: 4px;
  padding: 6px 12px; cursor: pointer; font-size: 13px; color: var(--soft);
}
.upload-status { font-size: 12px; color: var(--accent); }

/* ---- docs table ---- */
.error-text { color: var(--bad); font-style: italic; font-size: 12px; font-family: monospace; }
.del-doc-btn {
  background: none; border: 1px solid var(--bad); color: var(--bad);
  border-radius: 4px; padding: 2px 8px; font-size: 12px; cursor: pointer;
}

/* ---- subscription form ---- */
.sub-form-panel { padding: 14px; background: #f8fafc; margin-bottom: 12px; }

/* ---- actions ---- */
.actions-cell { white-space: nowrap; }
.toggle-btn {
  background: none; border: 1px solid var(--ok); color: var(--ok);
  border-radius: 4px; padding: 3px 8px; font-size: 12px; cursor: pointer; margin-right: 4px;
}
.del-btn {
  background: none; border: 1px solid var(--bad); color: var(--bad);
  border-radius: 4px; padding: 3px 8px; font-size: 12px; cursor: pointer;
}

/* ---- dialog (shared) ---- */
.dialog-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.35);
  display: flex; align-items: center; justify-content: center; z-index: 100;
}
.dialog {
  background: var(--panel); border-radius: 8px; padding: 24px 28px;
  max-width: 520px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.15);
}
.dialog h3 { margin: 0 0 16px; font-size: 16px; }
.delete-dialog { max-width: 560px; }
.kb-info-card {
  background: #f4f1ef; border-left: 3px solid var(--accent); border-radius: 4px;
  padding: 12px 14px; margin-bottom: 12px; font-size: 13px;
}
.info-row { display: flex; gap: 8px; margin-bottom: 6px; }
.info-row:last-child { margin-bottom: 0; }
.info-label { color: var(--soft); min-width: 72px; }
.warn-text { color: var(--bad); font-weight: 600; }
.id-value { font-family: monospace; font-size: 12px; word-break: break-all; }
.confirm-hint { font-size: 13px; margin: 10px 0 8px; }
.confirm-error { margin-top: 6px; }
.dialog-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 16px; }
.cancel-btn {
  background: none; border: 1px solid var(--line); border-radius: 4px;
  padding: 7px 14px; cursor: pointer; font-size: 14px; color: var(--soft);
}
.cancel-btn:hover { background: #f5f5f5; }
.danger { background: var(--bad); color: #fff; border: none; padding: 7px 14px; border-radius: 4px; cursor: pointer; font-size: 14px; }
.danger:disabled { opacity: 0.4; cursor: not-allowed; }

.empty-hint { text-align: center; color: var(--soft); padding: 16px 0; font-size: 13px; }
</style>
