<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useMock } from '@/api'

const route = useRoute()
const mock = useMock()
const active = computed(() => route.path)
</script>

<template>
  <el-container class="app">
    <el-header class="bar">
      <div class="brand">SGU · 教学通知问答</div>
      <el-menu :default-active="active" mode="horizontal" router :ellipsis="false" class="nav">
        <el-menu-item index="/chat">问答</el-menu-item>
        <el-menu-item index="/notices">通知清单</el-menu-item>
        <el-menu-item index="/eval">评估看板</el-menu-item>
      </el-menu>
      <el-tag v-if="mock" type="warning" size="small" effect="plain" class="mock">MOCK</el-tag>
    </el-header>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.app {
  min-height: 100vh;
}
.bar {
  display: flex;
  align-items: center;
  gap: 24px;
  border-bottom: 1px solid var(--el-border-color);
  background: var(--el-bg-color);
  position: sticky;
  top: 0;
  z-index: 10;
}
.brand {
  font-weight: 700;
  white-space: nowrap;
}
.nav {
  border-bottom: none;
  flex: 1;
}
.mock {
  margin-left: auto;
}
.main {
  background: var(--el-fill-color-blank);
  padding: 0;
}
</style>
