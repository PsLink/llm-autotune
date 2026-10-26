<script setup lang="ts">
import { useI18n } from '../i18n'

defineProps<{ workloadKind: string }>()
const host = defineModel<string>('host', { required: true })
const port = defineModel<number>('port', { required: true })
const { t } = useI18n()
</script>

<template>
  <el-alert v-if="workloadKind !== 'deployment'" type="info" :closable="false"
    :title="t('resources.customEndpointHint')" />
  <template v-else>
    <el-form-item :label="t('resources.endpointHost')">
      <el-input v-model.trim="host" class="mono" placeholder="198.51.100.10" />
      <div class="muted small">{{ t('resources.endpointHostHint') }}</div>
    </el-form-item>
    <el-form-item :label="t('resources.serviceNodePort')">
      <el-input-number v-model="port" :min="0" :max="65535" :precision="0"
        @change="port = $event ?? 0" />
      <div class="muted small">{{ t('resources.serviceNodePortHint') }}</div>
    </el-form-item>
    <el-alert v-if="port > 0" type="warning" :closable="false"
      :title="t('resources.fixedNodePortHint')" />
    <div v-if="host && port > 0" class="mono small">
      {{ t('resources.endpointPreview') }} http://{{ host }}:{{ port }}
    </div>
  </template>
</template>
