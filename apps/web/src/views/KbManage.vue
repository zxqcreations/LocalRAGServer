<template>
  <div>
    <h2>知识库管理</h2>

    <!-- 工具栏 -->
    <div class="toolbar">
      <span></span>
      <button v-if="!showCreateForm" class="primary" @click="showCreateForm = true">创建知识库</button>
      <div v-if="error" class="err">{{ error }}</div>
    </div>

    <!-- 内联创建表单 -->
    <div v-if="showCreateForm" class="panel create-panel">
      <div class="row">
        <div>
          <label>名称 *</label>
          <input v-model.trim="createForm.name" placeholder="输入知识库名称（1-100字符）" maxlength="100" />
        </div>
        <div style="flex: 0 0 140px">
          <label>类型</label>
          <select v-model="createForm.kb_type">
            <option value="document">文档</option>
            <option value="code">代码</option>
            <option value="web">网页</option>
          </select>
        </div>
      </div>
      <div class="row">
        <div>
          <label>简介（可选）</label>
          <textarea v-model="createForm.description" placeholder="知识库简介，最多500字符" rows="2" maxlength="500"></textarea>
        </div>
      </div>
      <div class="dialog-actions">
        <button class="cancel-btn" @click="resetCreateForm()">取消</button>
        <button class="primary" :disabled="creating" @click="doCreate()">
          {{ creating ? '创建中…' : '确认创建' }}
        </button>
      </div>
    </div>

    <!-- KB 列表表格 -->
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>文档数</th>
            <th>失败数</th>
            <th>创建时间</th>
            <th style="width: 130px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="kb in kbs" :key="kb.id">
            <td>
              <router-link :to="`/kb/${kb.id}`" class="kb-link">{{ kb.name || '(未命名)' }}</router-link>
            </td>
            <td>{{ kb.kb_type }}</td>
            <td>{{ kb.doc_count ?? '-' }}</td>
            <td>
              <span v-if="(kb.failed_count || 0) > 0" class="badge badge-fail">{{ kb.failed_count }}</span>
              <span v-else class="badge badge-ok">-</span>
            </td>
            <td>{{ formatDate(kb.created_at) }}</td>
            <td class="actions-cell">
              <button class="edit-btn" @click="openEdit(kb)">编辑</button>
              <button class="del-btn" @click="openDelete(kb)">删除</button>
            </td>
          </tr>
          <tr v-if="kbs.length === 0 && !loading">
            <td colspan="6" class="empty-hint">暂无知识库，点击上方按钮创建第一个</td>
          </tr>
        </tbody>
      </table>

      <!-- 分页器 -->
      <div v-if="total > pageSize" class="pagination">
        <button
          class="page-btn"
          :disabled="page <= 1"
          @click="gotoPage(page - 1)"
        >上一页</button>
        <span class="page-info">第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 条</span>
        <button
          class="page-btn"
          :disabled="page >= totalPages"
          @click="gotoPage(page + 1)"
        >下一页</button>
      </div>
    </div>

    <!-- ========== 编辑对话框覆盖层 ========== -->
    <div v-if="editingKb" class="dialog-overlay" @click.self="closeEdit()">
      <div class="dialog">
        <h3>编辑知识库</h3>
        <div class="row">
          <div>
            <label>名称</label>
            <input v-model.trim="editForm.name" maxlength="100" />
          </div>
          <div style="flex: 0 0 140px">
            <label>类型</label>
            <select v-model="editForm.kb_type">
              <option value="document">文档</option>
              <option value="code">代码</option>
              <option value="web">网页</option>
            </select>
          </div>
        </div>
        <div class="row">
          <div>
            <label>简介</label>
            <textarea v-model="editForm.description" rows="3" maxlength="500"></textarea>
          </div>
        </div>
        <p v-if="error" class="err">{{ error }}</p>
        <div class="dialog-actions">
          <button class="cancel-btn" @click="closeEdit()">取消</button>
          <button class="primary" :disabled="saving" @click="doSaveEdit()">
            {{ saving ? '保存中…' : '保存更改' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ========== 删除确认对话框 ========== -->
    <div v-if="deletingKb" class="dialog-overlay" @click.self="closeDelete()">
      <div class="dialog delete-dialog">
        <h3>⚠️ 确认删除知识库</h3>
        <div class="kb-info-card">
          <div class="info-row"><span class="info-label">KB 名称</span><span class="info-value warn-text">⚠️ 即将删除此库：{{ deletingKb.name }}</span></div>
          <div class="info-row"><span class="info-label">KB 类型</span><span class="info-value">{{ deletingKb.kb_type }}</span></div>
          <div class="info-row"><span class="info-label">KB ID</span><span class="info-value id-value">{{ deletingKb.id }}</span></div>
          <div class="info-row"><span class="info-label">创建时间</span><span class="info-value">{{ formatDate(deletingKb.created_at) }}</span></div>
          <div class="info-row"><span class="info-label">统计</span><span class="info-value">文档 {{ deletingKb.doc_count ?? 0 }} · 碎片 {{ deletingKb.chunk_count ?? 0 }} · 失败 {{ deletingKb.failed_count ?? 0 }}</span></div>
        </div>
        <p class="confirm-hint">为确保不会误删，请在下方文本框中完整输入上述 <strong>{{ deletingKb.id.slice(0, 8) }}…</strong> 的完整 ID 以确认：</p>
        <input
          v-model="deleteConfirmId"
          placeholder="请粘贴完整的 KB ID（共32位十六进制字符）"
          autocomplete="off"
          spellcheck="false"
          @input="checkDeleteMatch()"
        />
        <p v-if="deleteConfirmError" class="err confirm-error">{{ deleteConfirmError }}</p>
        <div class="dialog-actions">
          <button class="cancel-btn" @click="closeDelete()">取消</button>
          <button
            class="danger"
            :disabled="!deleteMatchOk"
            @click="doDelete()"
          >
            {{ deleting ? '删除中…' : '确认删除' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { api, listKbStats } from "../api.js";

const kbs = ref([]);
const loading = ref(true);
const error = ref("");
const showCreateForm = ref(false);

// ---- 分页状态 ----
const page = ref(1);
const pageSize = ref(50);
const total = ref(0);
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)));

// Create form state
const creating = ref(false);
const createForm = ref({ name: "", kb_type: "document", description: "" });

// Edit dialog state
const editingKb = ref(null);
const saving = ref(false);
const editForm = ref({ name: "", kb_type: "document", description: "" });

// Delete dialog state
const deletingKb = ref(null);
const deleteConfirmId = ref("");
const deleteConfirmError = ref("");
const deleteMatchOk = ref(false);
const deleting = ref(false);

async function load() {
  loading.value = true;
  try {
    const res = await api.listKbStats(page.value, pageSize.value);
    kbs.value = res.items;
    total.value = res.total;
    // 删除后当前页可能越界：回退到最后一页再加载一次
    const max = Math.max(1, Math.ceil(res.total / pageSize.value));
    if (page.value > max) {
      page.value = max;
      return load();
    }
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

function gotoPage(p) {
  if (p < 1 || p > totalPages.value) return;
  page.value = p;
  load();
}

onMounted(load);

// --- Format ---

function formatDate(isoStr) {
  if (!isoStr) return "-";
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return isoStr;
  return d.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

// --- Create ---

function resetCreateForm() {
  createForm.value = { name: "", kb_type: "document", description: "" };
}

async function doCreate() {
  const n = createForm.value.name;
  if (!n || n.length < 1 || n.length > 100) {
    error.value = "名称必须为 1-100 个字符";
    return;
  }
  creating.value = true;
  error.value = "";
  try {
    await api.createKb(n, createForm.value.kb_type, createForm.value.description);
    showCreateForm.value = false;
    resetCreateForm();
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    creating.value = false;
  }
}

// --- Edit ---

function openEdit(kb) {
  editingKb.value = { ...kb };
  editForm.value = { name: kb.name, kb_type: kb.kb_type, description: kb.description || "" };
  error.value = "";
}

function closeEdit() {
  editingKb.value = null;
  editForm.value = {};
}

async function doSaveEdit() {
  if (!editForm.value.name) {
    error.value = "名称不能为空";
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    await api.updateKb(editingKb.value.id, {
      name: editForm.value.name,
      kb_type: editForm.value.kb_type,
      description: editForm.value.description,
    });
    closeEdit();
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    saving.value = false;
  }
}

// --- Delete ---

function openDelete(kb) {
  // Ensure stats are available
  deletingKb.value = { ...kb };
  deleteConfirmId.value = "";
  deleteConfirmError.value = "";
  deleteMatchOk.value = false;
  error.value = "";
}

function checkDeleteMatch() {
  const input = deleteConfirmId.value.trim();
  const target = deletingKb.value?.id;
  if (!target) {
    deleteMatchOk.value = false;
    deleteConfirmError.value = "";
    return;
  }
  if (input === "") {
    deleteMatchOk.value = false;
    deleteConfirmError.value = "";
    return;
  }
  if (input === target) {
    deleteMatchOk.value = true;
    deleteConfirmError.value = "";
  } else {
    deleteMatchOk.value = false;
    deleteConfirmError.value = `输入的 ID 与目标不一致（需 ${target.length} 位字符）`;
  }
}

function closeDelete() {
  deletingKb.value = null;
  deleteConfirmId.value = "";
  deleteConfirmError.value = "";
  deleteMatchOk.value = false;
}

async function doDelete() {
  if (!deleteMatchOk.value) return;
  deleting.value = true;
  error.value = "";
  try {
    await api.deleteKb(deletingKb.value.id);
    closeDelete();
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    deleting.value = false;
  }
}
</script>

<style scoped>
.toolbar {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;
}
.toolbar .err { width: auto; }

/* ---- create panel ---- */
.create-panel { padding: 14px; margin-bottom: 12px; background: #f8fafc; }

/* ---- table ---- */
tr:hover { background: #fafbfd; }
.kb-link { color: var(--accent); text-decoration: none; font-weight: 600; cursor: pointer; }
.kb-link:hover { text-decoration: underline; }
.empty-hint { text-align: center; color: var(--soft); padding: 24px 0; }

/* ---- badges ---- */
.badge {
  display: inline-block; min-width: 20px; text-align: center; border-radius: 10px;
  font-size: 11px; padding: 2px 6px; font-weight: 600;
}
.badge-fail { background: var(--bad); color: #fff; }
.badge-ok { color: var(--soft); background: transparent; }

/* ---- actions ---- */
.actions-cell { white-space: nowrap; }
.edit-btn, .del-btn {
  background: none; border: 1px solid var(--line); border-radius: 4px;
  padding: 3px 8px; font-size: 12px; cursor: pointer; margin-right: 4px;
}
.del-btn { border-color: var(--bad); color: var(--bad); }

/* ---- dialog ---- */
.dialog-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.35);
  display: flex; align-items: center; justify-content: center; z-index: 100;
}
.dialog {
  background: var(--panel); border-radius: 8px; padding: 24px 28px;
  max-width: 520px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.15);
}
.dialog h3 { margin: 0 0 16px; font-size: 16px; }

/* ---- delete dialog extras ---- */
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
.confirm-hint { font-size: 13px; color: var(--ink); margin: 10px 0 8px; }
.confirm-error { margin-top: 6px; }

/* ---- dialog-actions ---- */
.dialog-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 16px; }
.cancel-btn {
  background: none; border: 1px solid var(--line); border-radius: 4px;
  padding: 7px 14px; cursor: pointer; font-size: 14px; color: var(--soft);
}
.cancel-btn:hover { background: #f5f5f5; }
.danger { background: var(--bad); color: #fff; border: none; padding: 7px 14px; border-radius: 4px; cursor: pointer; font-size: 14px; }
.danger:disabled { opacity: 0.4; cursor: not-allowed; }

/* ---- 分页器 ---- */
.pagination {
  display: flex; align-items: center; justify-content: flex-end; gap: 12px;
  padding-top: 12px; margin-top: 12px; border-top: 1px solid var(--line);
}
.page-btn {
  background: none; border: 1px solid var(--line); border-radius: 4px;
  padding: 4px 10px; cursor: pointer; font-size: 13px; color: var(--soft);
}
.page-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.page-info { font-size: 13px; color: var(--soft); }
</style>
