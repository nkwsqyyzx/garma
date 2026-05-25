import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      children: [
        { path: '', name: 'dashboard', component: () => import('@/views/Dashboard.vue') },
        { path: 'position/:code', name: 'position', component: () => import('@/views/PositionDetail.vue') },
        { path: 'orders', name: 'orders', component: () => import('@/views/Orders.vue') },
        { path: 'trades', name: 'trades', component: () => import('@/views/Trades.vue') },
        { path: 'trade', name: 'trade', component: () => import('@/views/Trade.vue') },
        { path: 'strategies', name: 'strategies', component: () => import('@/views/StrategyPositions.vue') },
        { path: 'fund-history', name: 'fund-history', component: () => import('@/views/FundHistory.vue') },
      ],
    },
  ],
})

export default router
