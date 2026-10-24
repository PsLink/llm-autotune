<script setup lang="ts">
/** How a replay dataset comes to be, as five steps in a row: the gateway's
 *  logs, the filter, the sample, the file on LLMBench, the replay. Read once,
 *  it explains every column of the cards below it, so it can fold away. */
import { ArrowRight, DataAnalysis, Files, Filter, Lock, Tickets, VideoPlay } from '@element-plus/icons-vue'

defineProps<{ collapsed: boolean }>()
defineEmits<{ toggle: [] }>()

const STEPS = [
  { icon: Tickets, title: 'Gateway logs', text: 'Every request the gateway serves is logged to VictoriaLogs.' },
  { icon: Filter, title: 'Filter', text: 'One model’s successful requests, from every node or the ones you pick.' },
  { icon: DataAnalysis, title: 'Sample', text: 'N requests spread evenly over the last hours, so every hour of the day is in it.' },
  { icon: Files, title: 'Dataset', text: 'LLMBench keeps the sample, and takes a fresh one on the schedule you set.' },
  { icon: VideoPlay, title: 'Replay', text: 'A campaign sends those requests again to every config it tries.' },
]
</script>

<template>
  <section class="pipeline" :class="{ collapsed }">
    <div class="head">
      <span class="title">How collection works</span>
      <button type="button" class="toggle" @click="$emit('toggle')">{{ collapsed ? 'Show' : 'Hide' }}</button>
    </div>
    <template v-if="!collapsed">
      <ol class="steps">
        <template v-for="(s, i) in STEPS" :key="s.title">
          <li class="step">
            <span class="badge"><el-icon :size="20"><component :is="s.icon" /></el-icon></span>
            <span class="step-title"><span class="num">{{ i + 1 }}</span>{{ s.title }}</span>
            <span class="step-text">{{ s.text }}</span>
          </li>
          <li v-if="i < STEPS.length - 1" class="arrow" aria-hidden="true">
            <el-icon><ArrowRight /></el-icon>
          </li>
        </template>
      </ol>
      <div class="rule">
        <el-icon class="rule-icon"><Lock /></el-icon>
        <span>A fresh sample never replaces one that a campaign is still measuring on. It waits
          until that campaign finishes, so every run of the campaign replays the same requests and
          stays comparable.</span>
      </div>
    </template>
  </section>
</template>

<style scoped>
.pipeline {
  background: var(--autotune-surface); border: 1px solid var(--autotune-border);
  border-radius: 10px; padding: 14px 18px 16px; margin-bottom: 20px;
}
.pipeline.collapsed { padding-bottom: 14px; }
.head { display: flex; justify-content: space-between; align-items: center; }
.title { font-size: 13px; font-weight: 600; color: var(--autotune-text); }
.toggle {
  border: none; background: none; font: inherit; font-size: 12px; cursor: pointer;
  color: var(--autotune-muted); padding: 2px 4px;
}
.toggle:hover { color: var(--el-color-primary); }
.steps {
  list-style: none; margin: 14px 0 0; padding: 0;
  display: grid; grid-template-columns: 1fr 20px 1fr 20px 1fr 20px 1fr 20px 1fr; align-items: start;
}
.step { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 6px; padding: 0 4px; }
.badge {
  width: 40px; height: 40px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
  color: var(--el-color-primary); background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
}
.step-title { font-size: 13px; font-weight: 600; color: var(--autotune-text); display: inline-flex; align-items: center; gap: 5px; }
.num {
  font-size: 10px; font-weight: 600; width: 15px; height: 15px; border-radius: 50%; line-height: 15px;
  color: #fff; background: var(--el-color-primary-light-3);
}
.step-text { font-size: 12px; line-height: 1.45; color: var(--autotune-muted); max-width: 190px; }
.arrow { display: flex; justify-content: center; padding-top: 12px; color: var(--el-text-color-placeholder); }
.rule {
  display: flex; gap: 8px; align-items: flex-start; margin-top: 14px; padding: 8px 12px;
  font-size: 12.5px; line-height: 1.5; color: var(--el-text-color-regular);
  background: var(--el-color-warning-light-9); border-radius: 6px;
}
.rule-icon { flex: none; margin-top: 3px; color: var(--el-color-warning-dark-2); }
@media (max-width: 900px) {
  .steps { grid-template-columns: 1fr; gap: 10px; }
  .step { flex-direction: row; text-align: left; align-items: center; }
  .step-text { max-width: none; }
  .arrow { display: none; }
}
</style>
