import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useConnectionStore = defineStore('connection', () => {
  const connected = ref(false)

  function setConnected(value: boolean) {
    connected.value = value
  }

  return { connected, setConnected }
})
