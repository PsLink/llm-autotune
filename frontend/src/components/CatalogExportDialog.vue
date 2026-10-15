<script setup lang="ts">
/** Measured runs as model-catalog files (catalog.swiss/v2).
 *
 *  Tick the runs to publish (the campaign's baseline and its winner are
 *  ticked to start) and give each a role. The server looks the model up in the
 *  published catalog: for a model it already has, each run becomes a new
 *  variant or the next version of a published one, and metadata.yaml gains
 *  this tuning result; for a new model, the form asks for what its
 *  metadata.yaml needs (the Hugging Face repo id first) and drafts the rest.
 *  Every file is checked against the catalog's schemas and rules
 *  server-side. Nothing is pushed: download the zip, unpack it at the root of
 *  a model-catalog checkout and open the pull request.
 *
 *  Plugins open it too, with their own runs (`candidates`).
 */
import { computed, ref, watch } from 'vue'
import { api, type CatalogExportPreview } from '../api/client'
import { apiErrorText } from '../utils/apiError'
import { copyText } from '../utils/clipboard'
import { useI18n } from '../i18n'

export interface CatalogCandidate {
  run_id: number
  /** What the row is called here, and in the provenance lines. */
  label: string
  /** A short note next to the label: a score, a rank. */
  detail?: string
}
interface Row extends CatalogCandidate {
  picked: boolean
  role: string
  /** Variant id override; empty = derived (<engine>-<parallelism>-<card>-<role>). */
  id: string
  /** The image tag to publish when the run's image was named by digest alone. */
  imageTag: string
}

const props = defineProps<{
  modelValue: boolean
  candidates: CatalogCandidate[]
  /** What the dialog opens with ticked: typically [baseline, optimized]. */
  initial: { run_id: number; role: string }[]
  modelName?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', open: boolean): void }>()

const { t } = useI18n()
const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const rows = ref<Row[]>([])
const name = ref('')
const hf = ref('')
const displayName = ref('')
const description = ref('')
const family = ref('')
const tags = ref('')
const license = ref('')
const homepage = ref('')
const defaultVariant = ref('')
const includeEnv = ref(true)
const includeVolumes = ref(false)
/** null = automatic: 90 minutes, raised to 3x the measured load when longer. */
const startupMinutes = ref<number | null>(null)
/** Empty = the node label of the card each config was measured on. */
const gpuProduct = ref('')
/** A saved report comparing exactly these runs; a tuning result needs one. */
const reportId = ref<number | null>(null)
const advanced = ref<string[]>([])

const loading = ref(false)
const downloading = ref(false)
const error = ref('')
const preview = ref<CatalogExportPreview | null>(null)
const tab = ref('')

watch(
  () => props.modelValue,
  (isOpen) => {
    if (!isOpen) return
    const initial = new Map(props.initial.map((p) => [p.run_id, p.role]))
    rows.value = props.candidates.map((c) => ({
      ...c,
      picked: initial.has(c.run_id),
      role: initial.get(c.run_id) ?? '',
      id: '',
      imageTag: '',
    }))
    for (const field of [name, hf, displayName, description, family, tags, license, homepage]) {
      field.value = ''
    }
    gpuProduct.value = ''
    reportId.value = null
    startupMinutes.value = null
    defaultVariant.value = ''
    advanced.value = []
    preview.value = null
    error.value = ''
    tab.value = ''
    void load()
  },
)

const picked = computed(() => rows.value.filter((r) => r.picked))
const ready = computed(() => picked.value.length > 0)
/** The form's metadata fields matter only for a model new to the catalog. */
const isNew = computed(() => !preview.value?.existing)

/** A freshly ticked row gets a role: the baseline if none is picked yet,
 *  otherwise another optimized config. */
function onTick(row: Row) {
  if (row.picked && !row.role) {
    row.role = picked.value.some((r) => r !== row && r.role === 'baseline') ? 'optimized' : 'baseline'
  }
}

function body() {
  // The baseline first, then the rest in list order.
  const order = [...picked.value].sort(
    (a, b) => Number(b.role === 'baseline') - Number(a.role === 'baseline'),
  )
  const list = (text: string) => text.split(',').map((s) => s.trim()).filter(Boolean)
  return {
    variants: order.map((r) => ({
      run_id: r.run_id, role: r.role, label: r.label, id: r.id.trim(), image_tag: r.imageTag.trim(),
    })),
    name: name.value,
    hf: hf.value,
    display_name: displayName.value,
    description: description.value,
    family: family.value,
    tags: list(tags.value),
    license: license.value,
    homepage: homepage.value,
    default_variant: defaultVariant.value,
    include_env: includeEnv.value,
    include_volumes: includeVolumes.value,
    startup_minutes: startupMinutes.value || null,
    gpu_product: list(gpuProduct.value),
    report_id: reportId.value || null,
  }
}

async function load() {
  if (!ready.value) {
    preview.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.post<CatalogExportPreview>('/catalog-export/preview', body())
    preview.value = data
    // Adopt what the server derived, so the form shows what the files say and
    // every drafted field can be edited. source.hf is never adopted: it is
    // only suggested, and confirmed by the person.
    if (!name.value) name.value = data.name
    if (!displayName.value) displayName.value = data.display_name
    if (!description.value) description.value = data.description
    if (!family.value) family.value = data.family
    if (!tags.value) tags.value = (data.tags ?? []).join(', ')
    if (defaultVariant.value && !variantIds.value.includes(defaultVariant.value)) {
      defaultVariant.value = ''
    }
    if (!data.files.some((f) => f.path === tab.value)) tab.value = data.files[0]?.path ?? ''
  } catch (e) {
    error.value = apiErrorText(e, t('catalogExport.failed'))
    preview.value = null
  } finally {
    loading.value = false
  }
}

const variantIds = computed(() => (preview.value?.variants ?? []).map((v) => v.id))

function useSuggestion() {
  hf.value = preview.value?.hf_suggestion ?? ''
  void load()
}

function statusText(v: CatalogExportPreview['variants'][number]) {
  return t(`catalogExport.status.${v.status}`, { version: v.version })
}

async function download() {
  downloading.value = true
  error.value = ''
  try {
    const { data } = await api.post('/catalog-export/archive', body(), { responseType: 'blob' })
    const url = URL.createObjectURL(data as Blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${preview.value?.name || 'model'}-catalog.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = apiErrorText(e, t('catalogExport.failed'))
  } finally {
    downloading.value = false
  }
}
</script>

<template>
  <el-dialog v-model="open" :title="t('catalogExport.title')" width="860px" top="6vh">
    <p class="muted subtitle">{{ t('catalogExport.subtitle') }}</p>

    <h4>{{ t('catalogExport.configs') }}
      <span class="muted tiny">{{ t('catalogExport.pickedN', { n: picked.length }) }}</span></h4>
    <el-table :data="rows" size="small" max-height="260" class="configs"
      :empty-text="t('catalogExport.noRuns')">
      <el-table-column width="44">
        <template #default="{ row }">
          <el-checkbox v-model="row.picked" @change="onTick(row)" />
        </template>
      </el-table-column>
      <el-table-column :label="t('catalogExport.runCol')" min-width="220">
        <template #default="{ row }">{{ row.label }}
          <span v-if="row.detail" class="muted">· {{ row.detail }}</span></template>
      </el-table-column>
      <el-table-column :label="t('catalogExport.role')" width="140">
        <template #default="{ row }">
          <el-select v-model="row.role" size="small" clearable :disabled="!row.picked"
            placeholder="—">
            <el-option value="baseline" :label="t('catalogExport.roles.baseline')" />
            <el-option value="optimized" :label="t('catalogExport.roles.optimized')" />
          </el-select>
        </template>
      </el-table-column>
    </el-table>

    <h4>{{ t('catalogExport.model') }}</h4>
    <el-alert v-if="preview?.existing" type="info" :closable="false" class="note"
      :title="t('catalogExport.existing', { name: preview.name })" />
    <el-form label-width="150px" size="small" class="form">
      <el-form-item :label="t('catalogExport.name')">
        <el-input v-model="name" :placeholder="modelName" class="mono" :disabled="!isNew" />
      </el-form-item>
      <el-form-item v-if="isNew" label="source.hf" required>
        <el-input v-model="hf" class="mono" :placeholder="preview?.hf_suggestion || 'org/name'" />
        <el-button v-if="preview?.hf_suggestion && !hf" size="small" link class="hint"
          @click="useSuggestion">{{ t('catalogExport.useSuggestion') }}</el-button>
        <div class="muted tiny">{{ t('catalogExport.hfHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('catalogExport.reportId')">
        <el-input-number v-model="reportId" :min="1" size="small" :value-on-clear="null" />
        <span class="muted tiny hint">{{ t('catalogExport.reportIdHint') }}</span>
      </el-form-item>
      <template v-if="isNew">
        <el-form-item label="displayName">
          <el-input v-model="displayName" maxlength="128" />
        </el-form-item>
        <el-form-item label="description">
          <el-input v-model="description" type="textarea" :rows="3" maxlength="2048" show-word-limit />
        </el-form-item>
        <el-form-item label="family · tags">
          <el-input v-model="family" placeholder="qwen" class="short" />
          <el-input v-model="tags" placeholder="chat, moe" class="tags" />
        </el-form-item>
        <el-form-item label="license · homepage">
          <el-input v-model="license" placeholder="apache-2.0" class="short" />
          <el-input v-model="homepage" placeholder="https://huggingface.co/org/name" class="tags" />
        </el-form-item>
      </template>
    </el-form>

    <!-- What most exports leave alone. -->
    <el-collapse v-model="advanced" class="advanced">
      <el-collapse-item :title="t('catalogExport.advanced')" name="advanced">
        <el-form label-width="150px" size="small">
          <el-form-item :label="t('catalogExport.variantIds')">
            <div class="ids">
              <div v-for="row in picked" :key="row.run_id" class="id-row">
                <span class="muted tiny id-label">{{ row.label }}</span>
                <el-input v-model="row.id" size="small" class="mono"
                  :placeholder="t('catalogExport.auto')" />
                <el-input v-model="row.imageTag" size="small" class="mono tag-input"
                  :placeholder="t('catalogExport.imageTag')" />
              </div>
            </div>
          </el-form-item>
          <el-form-item :label="t('catalogExport.defaultVariant')">
            <el-select v-model="defaultVariant" clearable :placeholder="t('catalogExport.defaultAuto')">
              <el-option v-for="id in variantIds" :key="id" :value="id" :label="id" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('catalogExport.options')">
            <el-checkbox v-model="includeEnv">{{ t('catalogExport.includeEnv') }}</el-checkbox>
            <el-checkbox v-model="includeVolumes">{{ t('catalogExport.includeVolumes') }}</el-checkbox>
          </el-form-item>
          <el-form-item :label="t('catalogExport.startup')">
            <el-input-number v-model="startupMinutes" :min="1" :max="240" size="small"
              :placeholder="t('catalogExport.auto')" :value-on-clear="null" />
          </el-form-item>
          <el-form-item label="gpuProduct">
            <el-input v-model="gpuProduct" class="mono" :placeholder="t('catalogExport.gpuProductHint')" />
          </el-form-item>
        </el-form>
      </el-collapse-item>
    </el-collapse>

    <el-button size="small" :loading="loading" :disabled="!ready" @click="load">
      {{ t('catalogExport.refresh') }}</el-button>

    <el-alert v-if="error" type="error" :closable="false" :title="error" class="note" />
    <template v-if="preview">
      <el-alert v-if="preview.errors.length" type="error" :closable="false" class="note"
        :title="t('catalogExport.invalid')">
        <ul class="caveats"><li v-for="(e, i) in preview.errors" :key="i">{{ e }}</li></ul>
      </el-alert>
      <el-alert v-else type="success" :closable="false" class="note"
        :title="t('catalogExport.valid')" />

      <ul class="caveats variants">
        <li v-for="v in preview.variants" :key="v.id">
          <span class="mono">{{ v.id }}</span> · {{ statusText(v) }}</li>
      </ul>
      <template v-if="preview.warnings.length">
        <h4>{{ t('catalogExport.warnings') }}</h4>
        <ul class="caveats"><li v-for="(w, i) in preview.warnings" :key="i">{{ w }}</li></ul>
      </template>

      <el-tabs v-model="tab" class="files">
        <el-tab-pane v-for="f in preview.files" :key="f.path" :name="f.path"
          :label="f.path.split('/').pop()">
          <div class="file-actions">
            <span class="muted tiny mono">{{ f.path }}</span>
            <el-button size="small" link @click="copyText(f.text, t('catalogExport.copied'))">
              {{ t('common.copy') }}</el-button>
          </div>
          <pre v-if="f.kind !== 'report'" class="yaml">{{ f.text }}</pre>
          <p v-else class="muted tiny">{{ t('catalogExport.reportFile') }}</p>
        </el-tab-pane>
      </el-tabs>
      <template v-if="preview.provenance?.length">
        <h4>{{ t('catalogExport.provenance') }}
          <el-button size="small" link
            @click="copyText(preview!.provenance.map((l) => `- ${l}`).join('\n'), t('catalogExport.copied'))">
            {{ t('common.copy') }}</el-button></h4>
        <ul class="caveats"><li v-for="(l, i) in preview.provenance" :key="i" class="mono tiny">{{ l }}</li></ul>
      </template>
      <p class="muted tiny">{{ t('catalogExport.next') }}</p>
    </template>

    <template #footer>
      <el-button @click="open = false">{{ t('common.close') }}</el-button>
      <el-button type="primary" :loading="downloading"
        :disabled="!preview || loading || !preview.valid" @click="download">
        {{ t('catalogExport.download') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.subtitle { margin: 0 0 10px; }
h4 { margin: 14px 0 6px; font-size: 13px; }
.configs { margin-bottom: 4px; }
.form { margin-top: 4px; }
.short { max-width: 140px; }
.tags { max-width: 280px; margin-left: 8px; }
.hint { margin-left: 10px; }
.advanced { margin: 4px 0 10px; }
.ids { display: flex; flex-direction: column; gap: 4px; width: 100%; }
.id-row { display: flex; align-items: center; gap: 8px; }
.id-label { min-width: 160px; }
.tag-input { max-width: 160px; }
.note { margin: 10px 0 8px; }
.caveats { margin: 0; padding-left: 18px; font-size: 12.5px; }
.caveats li { margin: 2px 0; }
.variants { margin-bottom: 6px; }
.files { margin-top: 10px; }
.file-actions { display: flex; justify-content: space-between; align-items: center; }
.yaml {
  max-height: 380px; overflow: auto; font-size: 12px; line-height: 1.45;
  background: var(--el-fill-color-light); padding: 10px; border-radius: 6px; margin: 0;
  white-space: pre;
}
.tiny { font-size: 11.5px; }
</style>
