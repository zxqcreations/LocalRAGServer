import { createApp } from "vue";
import { createPinia } from "pinia";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "./App.vue";
import Login from "./views/Login.vue";
import KbManage from "./views/KbManage.vue";
import KbDetail from "./views/KbDetail.vue";
import Playground from "./views/Playground.vue";
import KeyManage from "./views/KeyManage.vue";
import Monitor from "./views/Monitor.vue";
import EvalPanel from "./views/EvalPanel.vue";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/login", component: Login },
    { path: "/", component: KbManage },
    { path: "/kb/:kbId", component: KbDetail },
    { path: "/playground", component: Playground },
    { path: "/keys", component: KeyManage },
    { path: "/monitor", component: Monitor },
    { path: "/eval", component: EvalPanel },
  ],
});

createApp(App).use(createPinia()).use(router).mount("#app");
