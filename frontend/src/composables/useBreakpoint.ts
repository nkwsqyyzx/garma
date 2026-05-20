import { ref, onMounted, onUnmounted } from 'vue'

export function useBreakpoint(breakpoint: number = 768) {
  const isMobile = ref(window.innerWidth < breakpoint)

  function onResize() {
    isMobile.value = window.innerWidth < breakpoint
  }

  onMounted(() => window.addEventListener('resize', onResize))
  onUnmounted(() => window.removeEventListener('resize', onResize))

  return { isMobile }
}
