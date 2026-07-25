<template>
  <div class="settings-layout">
    <ConfigNav :active="active" @navigate="$emit('navigate', $event)" />
    <div class="settings-main">
      <component :is="current" :active="active" @navigate="$emit('navigate', $event)" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ConfigNav from '../components/ConfigNav.vue'
import SystemSettingsPage from './admin/SystemSettingsPage.vue'
import ModelSettingsPage from './admin/ModelSettingsPage.vue'
import EmbeddingSettingsPage from './admin/EmbeddingSettingsPage.vue'
import RagSettingsPage from './admin/RagSettingsPage.vue'
import AiCsSettings from './settings/AiCsSettings.vue'
import ProductOpSettings from './settings/ProductOpSettings.vue'
import NotifySettings from './settings/NotifySettings.vue'
import AboutSettings from './settings/AboutSettings.vue'

const props = defineProps({ active: String })
defineEmits(['navigate'])

const map = {
  'settings-system': SystemSettingsPage,
  'settings-ai-cs': AiCsSettings,
  'settings-model': ModelSettingsPage,
  'settings-embedding': EmbeddingSettingsPage,
  'settings-rag': RagSettingsPage,
  'settings-product': ProductOpSettings,
  'settings-about': AboutSettings,
  'settings-notify': NotifySettings
}

const current = computed(() => map[props.active] || SystemSettingsPage)
</script>

<style scoped>
.settings-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.settings-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

@media (max-width: 1260px) {
  .settings-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 900px) {
  .settings-layout {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }

  .settings-main {
    gap: 12px;
    min-width: 0;
  }
}
</style>
