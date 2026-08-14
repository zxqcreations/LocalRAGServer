<template>
  <div class="login-wrap">
    <div class="panel login-panel">
      <h2>LocalRAG 管理端登录</h2>
      <form @submit.prevent="submit">
        <label>用户名</label>
        <input v-model="username" autocomplete="username" />
        <label>密码</label>
        <input v-model="password" type="password" autocomplete="current-password" />
        <button class="primary" type="submit" style="margin-top: 14px; width: 100%">登录</button>
      </form>
      <p v-if="mustChange" class="err">首次登录须修改密码（下方表单）。</p>
      <form v-if="mustChange" @submit.prevent="changePw" style="margin-top: 14px">
        <label>新密码（≥8 位）</label>
        <input v-model="newPassword" type="password" />
        <button class="primary" type="submit" style="margin-top: 10px; width: 100%">修改密码</button>
      </form>
      <p v-if="error" class="err">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api, setCsrfToken } from "../api.js";

const emit = defineEmits(["logged-in"]);
const router = useRouter();
const username = ref("admin");
const password = ref("");
const newPassword = ref("");
const mustChange = ref(false);
const error = ref("");

async function submit() {
  error.value = "";
  try {
    const data = await api.login(username.value, password.value);
    setCsrfToken(data.csrf_token);
    mustChange.value = data.must_change_password;
    if (!mustChange.value) {
      emit("logged-in");
      router.push("/");
    }
  } catch (e) {
    error.value = e.message;
  }
}

async function changePw() {
  error.value = "";
  try {
    await api.changePassword(password.value, newPassword.value);
    mustChange.value = false;
    emit("logged-in");
    router.push("/");
  } catch (e) {
    error.value = e.message;
  }
}
</script>

<style scoped>
.login-wrap { display: flex; justify-content: center; padding-top: 12vh; }
.login-panel { width: 360px; }
</style>
