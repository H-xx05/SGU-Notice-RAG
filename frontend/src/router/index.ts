import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    {
      path: '/chat',
      name: 'chat',
      component: () => import('@/views/ChatView.vue'),
      meta: { title: '问答' },
    },
    {
      path: '/notices',
      name: 'notices',
      component: () => import('@/views/NoticesView.vue'),
      meta: { title: '通知清单' },
    },
    {
      path: '/eval',
      name: 'eval',
      component: () => import('@/views/EvalView.vue'),
      meta: { title: '评估看板' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

router.afterEach((to) => {
  const title = (to.meta.title as string | undefined) ?? ''
  document.title = title ? `${title} · SGU 教学通知问答` : 'SGU 教学通知问答'
})

export default router
