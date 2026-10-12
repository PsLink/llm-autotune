<script setup lang="ts">
/** A list's filters: an optional text search, then one dropdown per field
 *  (status, owner, …) where several values can be ticked. Each option shows how
 *  many rows have it. The choice lives in the page's URL, so a filtered list
 *  can be reloaded or sent to someone as a link. */
import { Search } from '@element-plus/icons-vue'
import { computed } from 'vue'
import { useI18n } from '../i18n'

export interface FilterOption {
  value: string
  label: string
  count?: number
}
export interface FilterField {
  key: string
  label: string
  options: FilterOption[]
  /** Wider for long values, such as campaign names. */
  width?: number
}

const props = defineProps<{
  fields: FilterField[]
  /** Selected values by field key. */
  modelValue: Record<string, string[]>
  /** Shown when set (including ''), as the placeholder of a text search. */
  searchPlaceholder?: string
  search?: string
  /** "12 of 40", when the parent knows both. */
  shown?: number
  total?: number
}>()
const emit = defineEmits<{
  'update:modelValue': [Record<string, string[]>]
  'update:search': [string]
}>()
const { t } = useI18n()

function set(key: string, values: string[]) {
  emit('update:modelValue', { ...props.modelValue, [key]: values })
}

const active = computed(() =>
  !!props.search?.trim() || Object.values(props.modelValue).some((v) => v?.length))

function clear() {
  emit('update:modelValue', Object.fromEntries(props.fields.map((f) => [f.key, []])))
  if (props.search !== undefined) emit('update:search', '')
}
</script>

<template>
  <div class="list-filters">
    <el-input v-if="searchPlaceholder !== undefined" :model-value="search" clearable
      :placeholder="searchPlaceholder" :prefix-icon="Search" class="search"
      @update:model-value="emit('update:search', $event)" />
    <el-select v-for="f in fields" :key="f.key" :model-value="modelValue[f.key] ?? []"
      multiple collapse-tags collapse-tags-tooltip clearable filterable
      :placeholder="f.label" :style="{ width: `${f.width ?? 160}px` }"
      :aria-label="f.label" @update:model-value="set(f.key, $event)">
      <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value">
        <span>{{ o.label }}</span>
        <span v-if="o.count != null" class="count">{{ o.count }}</span>
      </el-option>
    </el-select>
    <el-button v-if="active" link type="primary" @click="clear">{{ t('filters.clear') }}</el-button>
    <span class="spacer" />
    <span v-if="active && shown != null && total != null" class="muted tiny">
      {{ t('filters.shown', { shown, total }) }}
    </span>
  </div>
</template>

<style scoped>
.list-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.search {
  width: 220px;
}
.spacer {
  flex: 1;
}
.count {
  float: right;
  margin-left: 16px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.tiny {
  font-size: 12px;
}
</style>
