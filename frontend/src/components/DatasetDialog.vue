<script setup lang="ts">
/** Set up or change a replay dataset: a sample of one model's recorded traffic
 *  that LLMBench collects from the gateway's logs in VictoriaLogs.
 *
 *  The form asks for what a person knows: the model and, if it matters, the
 *  nodes, both offered from what the log store has seen; how many requests;
 *  and how often to take a new sample. The rest of a collection profile has
 *  defaults that suit most traffic and waits under Advanced. While the model
 *  and filters change, the form counts what they match, so a typo shows up
 *  here as "no request matches" rather than later as an empty dataset.
 */
import { CircleCheckFilled, CircleCloseFilled, Loading, WarningFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { api, type ReplayDataset, type ReplayDatasetForm } from '../api/client'
import { apiErrorText } from '../utils/apiError'
import InfoHint from './InfoHint.vue'

const props = defineProps<{
  /** The dataset to change; none = a new one. */
  dataset?: ReplayDataset | null
}>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ saved: [name: string] }>()

interface Seen { value: string; count: number }
interface Check { matched: number; hours: number; error?: string | null }

const REFRESH = [
  { value: 6, label: 'Every 6 hours' },
  { value: 12, label: 'Every 12 hours' },
  { value: 24, label: 'Every day' },
  { value: 168, label: 'Every week' },
  { value: 0, label: 'Only when asked' },
]

function blank(): ReplayDatasetForm {
  return {
    model: '', forwarded_to: [], sample_size: 2000, window_hours: 24, refresh_hours: 24,
    display_name: '', name: '', source_url: '', statuses: ['200'],
    uris: ['/v1/chat/completions'], extra_logsql: '', max_model_len: 262144,
    min_records: 100, keep_builds: 7, subwindow_minutes: 60, max_carry_multiple: 4,
    window_timezone: 'UTC',
  }
}

const form = ref<ReplayDatasetForm>(blank())
const advanced = ref<string[]>([])
const busy = ref(false)
/** The install's VictoriaLogs address; empty = this form asks for one. */
const defaultStore = ref<string | null>(null)
const models = ref<Seen[]>([])
const nodes = ref<Seen[]>([])
const loadingModels = ref(false)
const loadingNodes = ref(false)
const storeError = ref('')
const check = ref<Check | null>(null)
const checking = ref(false)

const editing = computed(() => Boolean(props.dataset))
const refreshOptions = computed(() =>
  REFRESH.some((o) => o.value === form.value.refresh_hours)
    ? REFRESH
    : [...REFRESH, { value: form.value.refresh_hours, label: `Every ${form.value.refresh_hours} hours` }])
const storeUrl = computed(() => form.value.source_url.trim() || defaultStore.value || '')
const canSave = computed(() => Boolean(form.value.model.trim() && storeUrl.value))

/** The ID LLMBench will know it by, as the server derives it. */
const slug = computed(() => {
  const s = (form.value.display_name || form.value.model).toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-').replace(/^[-_]+|[-_]+$/g, '').replace(/-{2,}/g, '-')
    .slice(0, 64).replace(/[-_]+$/, '')
  return s || 'dataset'
})

/** Requests a full window would offer at the rate the check saw. */
const expected = computed(() => {
  const c = check.value
  if (!c || c.error || !c.matched) return null
  return Math.round(c.matched * (form.value.window_hours / c.hours))
})

const fmt = (n: number) => n.toLocaleString()

watch(open, (isOpen) => {
  if (!isOpen) return
  const d = props.dataset
  form.value = d ? pick(d) : blank()
  advanced.value = []
  check.value = null
  nodes.value = []
  void loadModels()
  if (d?.model) void loadNodes()
})

function pick(d: ReplayDataset): ReplayDatasetForm {
  const out = blank()
  for (const key of Object.keys(out) as (keyof ReplayDatasetForm)[]) {
    const value = d[key]
    ;(out as any)[key] = Array.isArray(value) ? [...value] : value
  }
  return out
}

async function values(field: 'model' | 'forwarded_to') {
  const { data } = await api.post('/datasets/values', {
    field, model: form.value.model, source_url: form.value.source_url,
  })
  defaultStore.value = data.victorialogs_url || ''
  return data as { values: Seen[]; error: string | null }
}

async function loadModels() {
  loadingModels.value = true
  try {
    const data = await values('model')
    models.value = data.values
    storeError.value = data.error && storeUrl.value ? data.error : ''
  } catch (error) {
    storeError.value = apiErrorText(error, 'Could not read the log store')
  } finally {
    loadingModels.value = false
  }
}

async function loadNodes() {
  if (!form.value.model) {
    nodes.value = []
    return
  }
  loadingNodes.value = true
  try {
    nodes.value = (await values('forwarded_to')).values
  } catch {
    nodes.value = []
  } finally {
    loadingNodes.value = false
  }
}

function onModel() {
  form.value.forwarded_to = []
  void loadNodes()
}

// Count what the filters match, a moment after they stop changing.
let timer = 0
let asked = 0
watch(
  () => JSON.stringify([form.value.model, form.value.forwarded_to, form.value.window_hours,
    form.value.statuses, form.value.uris, form.value.extra_logsql, storeUrl.value]),
  () => {
    window.clearTimeout(timer)
    if (!open.value || !form.value.model.trim() || !storeUrl.value) {
      check.value = null
      return
    }
    timer = window.setTimeout(runCheck, 600)
  },
)

async function runCheck() {
  const mine = ++asked
  checking.value = true
  try {
    const { data } = await api.post('/datasets/check', form.value)
    if (mine === asked) check.value = data
  } catch (error) {
    if (mine === asked) check.value = { matched: 0, hours: 24, error: apiErrorText(error, 'Check failed') }
  } finally {
    if (mine === asked) checking.value = false
  }
}

async function save() {
  busy.value = true
  try {
    if (props.dataset) {
      await api.put(`/datasets/${props.dataset.name}`, form.value)
      ElMessage.success('Saved. The next sample uses the new settings.')
      emit('saved', props.dataset.name)
    } else {
      const { data } = await api.post('/datasets', form.value)
      if (data.collect_error) {
        ElMessage.warning(`${data.display_name} is set up, but its first sample could not start: ${data.collect_error}`)
      } else {
        ElMessage.success(`Collecting the first sample of ${data.display_name}`)
      }
      emit('saved', data.name)
    }
    open.value = false
  } catch (error) {
    ElMessage.error(apiErrorText(error, 'Save failed'))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-dialog v-model="open" :title="dataset ? `Edit ${dataset.display_name || dataset.name}` : 'New dataset'"
    width="580px" append-to-body>
    <el-form label-position="top" @submit.prevent>
      <el-form-item v-if="defaultStore === ''" required>
        <template #label>
          VictoriaLogs address
          <InfoHint :width="340">The store of the gateway's request logs, as LLMBench reaches
            it. An admin can set it once for every dataset with
            <span class="mono">llmbench.victoriaLogsUrl</span>.</InfoHint>
        </template>
        <el-input v-model="form.source_url" class="mono" placeholder="http://victorialogs:9428"
          @change="loadModels" />
      </el-form-item>

      <el-form-item required>
        <template #label>
          Model
          <InfoHint :width="320">The model name clients send, as the gateway logged it. The
            list holds the models in the last day of traffic.</InfoHint>
        </template>
        <el-select v-model="form.model" filterable allow-create default-first-option
          :loading="loadingModels" placeholder="Choose or type a model" @change="onModel">
          <el-option v-for="m in models" :key="m.value" :value="m.value" :label="m.value">
            <span>{{ m.value }}</span>
            <span class="muted opt-help">{{ fmt(m.count) }} requests / day</span>
          </el-option>
        </el-select>
        <div v-if="storeError" class="tiny error-text">Could not read the log store: {{ storeError }}</div>
      </el-form-item>

      <el-form-item>
        <template #label>
          Nodes
          <InfoHint :width="320">Only the requests these backends served: an IP (any port) or
            IP:port. Empty takes the model's traffic from every node.</InfoHint>
        </template>
        <el-select v-model="form.forwarded_to" multiple filterable allow-create default-first-option
          :loading="loadingNodes" :disabled="!form.model" placeholder="All nodes serving this model">
          <el-option v-for="n in nodes" :key="n.value" :value="n.value" :label="n.value">
            <span class="mono">{{ n.value }}</span>
            <span class="muted opt-help">{{ fmt(n.count) }} requests / day</span>
          </el-option>
        </el-select>
      </el-form-item>

      <el-form-item label="Sample">
        <div class="inline">
          <el-input-number v-model="form.sample_size" :min="1" :max="1000000" :step="500"
            controls-position="right" class="num" />
          <span>requests from the last</span>
          <el-input-number v-model="form.window_hours" :min="1" :max="720"
            controls-position="right" class="num-sm" />
          <span>hours</span>
        </div>
      </el-form-item>

      <div class="grid-2">
        <el-form-item>
          <template #label>
            New sample
            <InfoHint :width="340">How often to replace the sample with a fresh one. Not while a
              campaign is measuring on the current sample: the new one waits until the campaign
              finishes, so its results stay comparable.</InfoHint>
          </template>
          <el-select v-model="form.refresh_hours">
            <el-option v-for="o in refreshOptions" :key="o.value" :value="o.value" :label="o.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="Name">
          <el-input v-model="form.display_name" :placeholder="form.model || 'Qwen3-8B production'" />
        </el-form-item>
      </div>

      <div v-if="form.model && storeUrl" class="check" :class="{
        ok: check && !check.error && check.matched > 0,
        warn: check && !check.error && !check.matched,
        bad: check?.error }">
        <template v-if="checking || !check">
          <el-icon class="is-loading"><Loading /></el-icon> Checking the log store…
        </template>
        <template v-else-if="check.error">
          <el-icon><CircleCloseFilled /></el-icon> {{ check.error }}
        </template>
        <template v-else-if="check.matched > 0">
          <el-icon><CircleCheckFilled /></el-icon>
          {{ fmt(check.matched) }} requests in the last {{ check.hours }} h match.
          <span v-if="expected !== null && expected < form.sample_size" class="muted">
            A {{ form.window_hours }} h window holds about {{ fmt(expected) }}, so the sample
            takes what there is.
          </span>
        </template>
        <template v-else>
          <el-icon><WarningFilled /></el-icon>
          No request in the last {{ check.hours }} h matches. Check the model name and the nodes.
        </template>
      </div>

      <el-collapse v-model="advanced" class="advanced">
        <el-collapse-item name="advanced" title="Advanced">
          <div class="grid-2">
            <el-form-item v-if="defaultStore" class="span-2">
              <template #label>
                VictoriaLogs address
                <InfoHint>Empty uses the install's, shown here.</InfoHint>
              </template>
              <el-input v-model="form.source_url" class="mono" :placeholder="defaultStore"
                @change="loadModels" />
            </el-form-item>
            <el-form-item>
              <template #label>
                Status codes
                <InfoHint>Only requests that ended with these statuses.</InfoHint>
              </template>
              <el-select v-model="form.statuses" multiple filterable allow-create
                default-first-option placeholder="Any" />
            </el-form-item>
            <el-form-item>
              <template #label>
                Paths
                <InfoHint>Only requests to these API paths.</InfoHint>
              </template>
              <el-select v-model="form.uris" multiple filterable allow-create
                default-first-option placeholder="Any" />
            </el-form-item>
            <el-form-item class="span-2">
              <template #label>
                Extra LogsQL filter
                <InfoHint :width="320">Any further VictoriaLogs filter, combined with the ones
                  above, e.g. <span class="mono">req_headers.x-app:="chat"</span>.</InfoHint>
              </template>
              <el-input v-model="form.extra_logsql" class="mono" placeholder="none" />
            </el-form-item>
            <el-form-item>
              <template #label>
                Longest prompt, tokens
                <InfoHint :width="300">Longer requests are left out. Set it to the context
                  length of the engines that will replay the sample.</InfoHint>
              </template>
              <el-input-number v-model="form.max_model_len" :min="2048" :step="8192"
                controls-position="right" />
            </el-form-item>
            <el-form-item>
              <template #label>
                Fewest requests
                <InfoHint :width="300">A sample with fewer requests is not published; the
                  previous one stays.</InfoHint>
              </template>
              <el-input-number v-model="form.min_records" :min="1" controls-position="right" />
            </el-form-item>
            <el-form-item>
              <template #label>
                Slice, minutes
                <InfoHint :width="340">The window is cut into slices, and each keeps an even
                  share of the sample, so every hour of the day is represented.</InfoHint>
              </template>
              <el-input-number v-model="form.subwindow_minutes" :min="1" :max="1440"
                controls-position="right" />
            </el-form-item>
            <el-form-item>
              <template #label>
                Busy-slice cap
                <InfoHint :width="340">A quiet slice's unused share carries to the next, up to
                  this many times the even share. For traffic that comes in bursts, raise it or
                  widen the slices.</InfoHint>
              </template>
              <el-input-number v-model="form.max_carry_multiple" :min="1" controls-position="right" />
            </el-form-item>
            <el-form-item>
              <template #label>
                Samples kept
                <InfoHint>Older samples are deleted from LLMBench's volume.</InfoHint>
              </template>
              <el-input-number v-model="form.keep_builds" :min="1" :max="100"
                controls-position="right" />
            </el-form-item>
            <el-form-item>
              <template #label>
                Timezone
                <InfoHint>The clock slices line up with, for slices longer than an hour.</InfoHint>
              </template>
              <el-input v-model="form.window_timezone" />
            </el-form-item>
            <el-form-item v-if="!editing" class="span-2">
              <template #label>
                ID
                <InfoHint>Its name on LLMBench, which cannot change later.</InfoHint>
              </template>
              <el-input v-model="form.name" class="mono" :placeholder="slug" />
            </el-form-item>
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-form>
    <template #footer>
      <el-button @click="open = false">Cancel</el-button>
      <el-button type="primary" :loading="busy" :disabled="!canSave" @click="save">
        {{ editing ? 'Save' : 'Create and collect' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.inline { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.num { width: 130px; }
.num-sm { width: 100px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.grid-2 :deep(.el-input-number), .grid-2 :deep(.el-select) { width: 100%; }
.span-2 { grid-column: span 2; }
.opt-help { margin-left: 8px; font-size: 12px; float: right; }
.check {
  display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
  font-size: 13px; padding: 8px 10px; margin-bottom: 12px; border-radius: 4px;
  background: var(--el-fill-color-light); color: var(--el-text-color-regular);
}
.check .el-icon { position: relative; top: 2px; }
.check.ok .el-icon { color: var(--el-color-success); }
.check.warn .el-icon { color: var(--el-color-warning); }
.check.bad { color: var(--el-color-danger); }
.error-text { color: var(--el-color-danger); margin-top: 4px; line-height: 1.4; }
.advanced { border-top: none; }
</style>
