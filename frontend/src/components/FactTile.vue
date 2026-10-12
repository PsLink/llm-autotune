<script setup lang="ts">
/** One fact about a campaign at a glance: an icon, a small label, the value,
 *  and a muted line under it. What does not fit stays in the tooltip, so the
 *  summary is one row of tiles rather than paragraphs. */
import type { Component } from 'vue'

defineProps<{
  icon: Component
  label: string
}>()
</script>

<template>
  <div class="fact">
    <el-icon class="icon" :size="18"><component :is="icon" /></el-icon>
    <div class="body">
      <div class="label">
        {{ label }}
        <slot name="action" />
      </div>
      <el-tooltip :disabled="!$slots.tip" placement="bottom-start" :show-after="250">
        <template #content><div class="tip"><slot name="tip" /></div></template>
        <div class="value"><slot /></div>
      </el-tooltip>
      <div v-if="$slots.sub" class="sub"><slot name="sub" /></div>
    </div>
  </div>
</template>

<style scoped>
.fact {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  min-width: 0;
}
.icon {
  flex: none;
  margin-top: 2px;
  color: var(--el-color-primary);
}
.body {
  min-width: 0;
  flex: 1;
}
.label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  line-height: 18px;
}
.value {
  font-weight: 600;
  font-size: 14px;
  line-height: 20px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 18px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tip {
  max-width: 360px;
  line-height: 1.5;
  white-space: pre-line;
}
</style>
