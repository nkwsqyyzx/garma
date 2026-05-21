<template>
  <el-container class="main-layout">
    <!-- PC: 侧边栏 -->
    <el-aside v-if="!isMobile" width="180px" class="sidebar">
      <div class="sidebar-logo">Garma QMT</div>
      <el-menu
        :default-active="activeRoute"
        router
        class="sidebar-menu"
        background-color="#1d1e2c"
        text-color="#a0a0b0"
        active-text-color="#409eff"
      >
        <el-menu-item index="/">
          <span>总览</span>
        </el-menu-item>
        <el-menu-item index="/orders">
          <span>委托</span>
        </el-menu-item>
        <el-menu-item index="/trades">
          <span>成交</span>
        </el-menu-item>
        <el-menu-item index="/trade">
          <span>下单</span>
        </el-menu-item>
        <el-menu-item index="/strategies">
          <span>策略持仓</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶部状态栏 -->
      <el-header class="status-header" height="40px">
        <StatusBar />
      </el-header>

      <!-- 主内容区 -->
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>

    <!-- 手机: 底部导航 -->
    <div v-if="isMobile" class="bottom-nav">
      <div
        v-for="item in navItems"
        :key="item.path"
        class="bottom-nav-item"
        :class="{ active: activeRoute === item.path }"
        @click="router.push(item.path)"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span class="nav-label">{{ item.label }}</span>
      </div>
    </div>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useBreakpoint } from '@/composables/useBreakpoint'
import StatusBar from '@/components/StatusBar.vue'
import { onMounted, onUnmounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'

const route = useRoute()
const router = useRouter()
const { isMobile } = useBreakpoint()
const { connect, disconnect } = useWebSocket()

const activeRoute = computed(() => route.path)

const navItems = [
  { path: '/', label: '总览', icon: '📊' },
  { path: '/orders', label: '委托', icon: '📋' },
  { path: '/trades', label: '成交', icon: '✅' },
  { path: '/trade', label: '下单', icon: '💰' },
]

onMounted(() => {
  connect()
})

onUnmounted(() => {
  disconnect()
})
</script>

<style scoped>
.main-layout {
  height: 100vh;
}
.sidebar {
  background: #1d1e2c;
  border-right: 1px solid #2d2e3e;
}
.sidebar-logo {
  color: #e0e0e0;
  font-size: 18px;
  font-weight: 600;
  padding: 16px;
  text-align: center;
  border-bottom: 1px solid #2d2e3e;
}
.sidebar-menu {
  border-right: none;
}
.status-header {
  padding: 0 16px;
  display: flex;
  align-items: center;
  background: #fafafa;
  border-bottom: 1px solid #ebeef5;
}
.main-content {
  padding: 16px;
  overflow-y: auto;
  padding-bottom: 72px;
}
/* 底部导航 */
.bottom-nav {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: 56px;
  background: #fff;
  border-top: 1px solid #e0e0e0;
  display: flex;
  z-index: 100;
}
.bottom-nav-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  font-size: 11px;
  color: #909399;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.bottom-nav-item.active {
  color: #409eff;
}
.nav-icon {
  font-size: 18px;
  line-height: 1;
}
.nav-label {
  font-size: 10px;
}
</style>
