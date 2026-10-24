<script setup lang="ts">
/** Replay datasets: samples of one model's recorded production traffic that a
 *  campaign's Replay workload sends again. LLMBench collects each sample from
 *  the gateway's request logs in VictoriaLogs; this page explains how, sets
 *  datasets up, shows what each sample holds, and takes new ones. */
import { Coin, DataAnalysis, Files, Loading, Lock, Plus, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  api,
  type DatasetBuildState,
  type DatasetList,
  type FrozenDataset,
  type ReplayDataset,
} from '../api/client'
import CampaignChips from '../components/CampaignChips.vue'
import DatasetCard from '../components/DatasetCard.vue'
import DatasetDialog from '../components/DatasetDialog.vue'
import DatasetPipeline from '../components/DatasetPipeline.vue'
import InfoHint from '../components/InfoHint.vue'
import PageLoading from '../components/PageLoading.vue'
import { apiErrorText } from '../utils/apiError'
import { useFirstLoad } from '../utils/load'
import { usePoll } from '../utils/poll'
import { exactTime, relativeTime } from '../utils/time'

const DOCS = 'https://github.com/modelsphere/llm-autotune/blob/main/docs/guide/datasets.md'
const PIPELINE_KEY = 'autotune.datasets.pipelineHidden'

const route = useRoute()
const list = ref<DatasetList | null>(null)
const loadError = ref('')
const dialogOpen = ref(false)
const editing = ref<ReplayDataset | null>(null)
/** Each dataset's recent collections, and the last build they were read at. */
const builds = ref<Record<string, DatasetBuildState[] | null>>({})
const buildsAt: Record<string, string> = {}

const datasets = computed(() => list.value?.datasets ?? [])
const collecting = computed(() => datasets.value.filter(inFlight))

/** Frozen datasets: files on LLMBench that never change, frozen there from
 *  a sample or imported from another installation. Read separately, so an
 *  LLMBench that does not share them still shows the rolling ones. */
const frozen = ref<FrozenDataset[]>([])
const frozenError = ref('')

/** The explainer folds away once someone has read it; a browser without
 *  storage simply shows it every time. */
function readHidden(): boolean {
  try { return localStorage.getItem(PIPELINE_KEY) === '1' } catch { return false }
}
const pipelineHidden = ref(readHidden())
function togglePipeline() {
  pipelineHidden.value = !pipelineHidden.value
  try { localStorage.setItem(PIPELINE_KEY, pipelineHidden.value ? '1' : '0') } catch { /* not kept */ }
}

async function load() {
  await Promise.all([loadList(), loadFrozen()])
}
async function loadList() {
  try {
    list.value = (await api.get('/datasets')).data
    loadError.value = ''
    void loadBuilds()
  } catch (error) {
    loadError.value = apiErrorText(error, 'Could not reach LLMBench')
  }
}
/** Recent collections for every card, re-read only when a dataset's last
 *  build has moved on since. */
async function loadBuilds() {
  await Promise.all(datasets.value.map(async (d) => {
    const mark = `${d.last_build?.id ?? ''}:${d.last_build?.status ?? ''}`
    if (buildsAt[d.name] === mark && builds.value[d.name]) return
    try {
      builds.value[d.name] = (await api.get(`/datasets/${d.name}/builds`)).data
      buildsAt[d.name] = mark
    } catch {
      builds.value[d.name] = []
    }
  }))
}
async function loadFrozen() {
  try {
    frozen.value = (await api.get('/datasets/frozen')).data.frozen
    frozenError.value = ''
  } catch (error) {
    frozen.value = []
    frozenError.value = apiErrorText(error, 'Could not read the frozen datasets')
  }
}
const first = useFirstLoad(load)

function openOnLlmbench() {
  if (list.value?.llmbench_url) {
    window.open(`${list.value.llmbench_url}/admin/replay-datasets`, '_blank', 'noopener')
  }
}

function size(bytes: number | null): string {
  if (bytes == null) return '—'
  const mb = bytes / (1 << 20)
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(mb < 10 ? 1 : 0)} MB`
}

// Every few seconds while a sample is being collected, otherwise every minute.
const fast = usePoll(() => (collecting.value.length ? load() : undefined), 5_000)
const slow = usePoll(load, 60_000)

onMounted(async () => {
  await first.run()
  fast.start()
  slow.start()
  if (route.query.new) openNew()
})

function inFlight(d: ReplayDataset): boolean {
  return d.last_build?.status === 'pending' || d.last_build?.status === 'running'
}

const fmt = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString())

/** The page at a glance: how many datasets, what is happening to them, and
 *  how much of the tuning leans on them. */
const tiles = computed(() => {
  const ds = datasets.value
  const replaying = new Set(ds.flatMap((d) => [...d.used_by, ...d.held_by].map((c) => c.id)))
  const held = ds.filter((d) => d.held_by.length).length
  return [
    { icon: Coin, label: 'Datasets', value: String(ds.length), sub: `${ds.filter((d) => d.current).length} with a sample` },
    { icon: DataAnalysis, label: 'Requests in samples', value: fmt(ds.reduce((n, d) => n + (d.current?.records ?? 0), 0)), sub: 'across every dataset' },
    { icon: collecting.value.length ? Loading : Lock, spin: collecting.value.length > 0, label: collecting.value.length ? 'Collecting now' : 'Held steady',
      value: String(collecting.value.length || held), sub: collecting.value.length ? 'samples on their way' : 'kept for campaigns measuring on them' },
    { icon: VideoPlay, label: 'Campaigns replaying', value: String(replaying.size), sub: `${frozen.value.length} frozen dataset${frozen.value.length === 1 ? '' : 's'} too` },
  ]
})

function openNew() {
  editing.value = null
  dialogOpen.value = true
}

function openEdit(d: ReplayDataset) {
  editing.value = d
  dialogOpen.value = true
}

async function collect(d: ReplayDataset) {
  let force = false
  if (d.held_by.length) {
    const names = d.held_by.map((c) => c.name).join(', ')
    try {
      await ElMessageBox.confirm(
        `${names} is measuring on the current sample. Its later replay runs would use the new ` +
          'sample and no longer compare with the earlier ones. Collect a new sample anyway?',
        'Collect a new sample',
        { confirmButtonText: 'Collect', cancelButtonText: 'Cancel', type: 'warning' },
      )
    } catch {
      return
    }
    force = true
  }
  try {
    await api.post(`/datasets/${d.name}/collect`, null, { params: { force } })
    ElMessage.success(`Collecting a new sample of ${d.display_name || d.name}`)
    await load()
  } catch (error) {
    ElMessage.error(apiErrorText(error, 'Could not start collecting'))
  }
}

async function remove(d: ReplayDataset) {
  try {
    await ElMessageBox.confirm(
      `Delete ${d.display_name || d.name}? Finished campaigns keep their results; a campaign ` +
        'created later can no longer replay it.',
      'Delete dataset',
      { confirmButtonText: 'Delete', cancelButtonText: 'Cancel', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await api.delete(`/datasets/${d.name}`)
    ElMessage.success('Dataset deleted')
    await load()
  } catch (error) {
    ElMessage.error(apiErrorText(error, 'Delete failed'))
  }
}
</script>

<template>
  <div class="page">
    <div class="header-row">
      <div>
        <h1 class="page-title">Datasets
          <InfoHint :width="400">
            A dataset is a sample of one model's recorded production requests, which a campaign's
            <b>Replay</b> workload sends again. LLMBench collects it from the gateway's request
            logs in VictoriaLogs, and replaces it with a fresh sample on the interval you choose,
            but not while a campaign is measuring on it.
            <a :href="DOCS" target="_blank" rel="noopener">Datasets guide</a>
          </InfoHint>
        </h1>
        <p class="lede">Real production traffic, sampled from the gateway's logs and kept fresh, so
          campaigns tune against the requests your users actually send.</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openNew">New dataset</el-button>
    </div>

    <el-alert v-if="loadError" type="error" :closable="false" show-icon class="banner"
      :title="loadError" />
    <el-alert v-else-if="list && !list.collector" type="warning" :closable="false" show-icon
      class="banner" title="LLMBench has no dataset collector, so no sample can be collected">
      An admin turns it on in LLMBench's values (<span class="mono">datasets.enabled</span> and
      <span class="mono">datasetBuilder.enabled</span>);
      <a :href="DOCS" target="_blank" rel="noopener">see the guide</a>.
    </el-alert>

    <PageLoading v-if="first.loading && !list" />
    <template v-else>
      <div v-if="datasets.length" class="tiles">
        <div v-for="tile in tiles" :key="tile.label" class="tile">
          <span class="tile-icon"><el-icon :size="18" :class="{ 'is-loading': tile.spin }"><component :is="tile.icon" /></el-icon></span>
          <div>
            <div class="tile-label">{{ tile.label }}</div>
            <div class="tile-value">{{ tile.value }}</div>
            <div class="tile-sub">{{ tile.sub }}</div>
          </div>
        </div>
      </div>

      <DatasetPipeline :collapsed="pipelineHidden && datasets.length > 0" @toggle="togglePipeline" />

      <div v-if="datasets.length" class="cards">
        <DatasetCard v-for="d in datasets" :key="d.name" :dataset="d" :builds="builds[d.name] ?? null"
          :collector="!!list?.collector" :llmbench-url="list?.llmbench_url ?? ''"
          @collect="collect(d)" @edit="openEdit(d)" @remove="remove(d)" @llmbench="openOnLlmbench" />
      </div>
      <div v-else-if="!loadError" class="empty-card">
        <span class="empty-icon"><el-icon :size="26"><Coin /></el-icon></span>
        <div class="empty-title">No datasets yet</div>
        <div class="empty-text">Pick a model the gateway serves, and LLMBench collects a sample of its
          requests for campaigns to replay.</div>
        <el-button type="primary" :icon="Plus" @click="openNew">New dataset</el-button>
      </div>
    </template>

    <div class="header-row frozen-head">
      <h2 class="section-title"><el-icon class="section-icon"><Files /></el-icon> Frozen datasets
        <InfoHint :width="400">
          Files on LLMBench that never change: a sample frozen there, or a dataset imported
          from another installation (an import is always frozen). Every campaign that replays
          one sees exactly the same requests, so their results compare across nights. Choose
          one under <b>Replay</b> in a campaign's workload. Import and download them on
          LLMBench's datasets page.
        </InfoHint>
      </h2>
      <el-button v-if="list?.llmbench_url" link type="primary"
        @click="openOnLlmbench">Import or download on LLMBench</el-button>
    </div>
    <el-alert v-if="frozenError" type="warning" :closable="false" show-icon class="banner"
      :title="frozenError" />
    <div v-else-if="frozen.length" class="frozen-grid">
      <article v-for="f in frozen" :key="f.name" class="frozen">
        <div class="frozen-top">
          <span class="mono frozen-name" :title="f.name">{{ f.name }}</span>
          <el-tag v-if="f.origin === 'upload'" size="small" effect="plain">imported</el-tag>
        </div>
        <div class="frozen-facts">
          <span><b>{{ fmt(f.records) }}</b> requests</span>
          <span><b>{{ size(f.bytes) }}</b></span>
          <span :title="exactTime(f.frozen_at)">frozen {{ relativeTime(f.frozen_at) }}</span>
        </div>
        <div v-if="f.source_profile || f.source_build_id" class="frozen-from tiny">
          from <span class="mono">{{ f.source_profile }}<template v-if="f.source_build_id"> / {{ f.source_build_id }}</template></span>
        </div>
        <CampaignChips v-if="f.campaigns.length" :campaigns="f.campaigns" :max="3" class="frozen-chips" />
        <div v-else class="tiny muted frozen-chips">No campaign replays it</div>
      </article>
    </div>
    <div v-else-if="!first.loading" class="empty muted">
      No frozen datasets. Freeze a sample, or import a dataset downloaded from another
      installation, on LLMBench's datasets page.
    </div>

    <DatasetDialog v-model="dialogOpen" :dataset="editing" @saved="load" />
  </div>
</template>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 16px;
}
.lede { margin: 4px 0 0; font-size: 13.5px; color: var(--autotune-muted); max-width: 680px; }
.banner { margin-bottom: 12px; }
.tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile {
  display: flex; gap: 12px; align-items: flex-start; padding: 12px 14px;
  background: var(--autotune-surface); border: 1px solid var(--autotune-border); border-radius: 10px;
}
.tile-icon {
  flex: none; width: 34px; height: 34px; border-radius: 8px; display: inline-flex; align-items: center;
  justify-content: center; color: var(--el-color-primary); background: var(--el-color-primary-light-9);
}
.tile-label { font-size: 12px; color: var(--autotune-muted); }
.tile-value { font-size: 20px; font-weight: 600; color: var(--autotune-text); line-height: 1.25; font-variant-numeric: tabular-nums; }
.tile-sub { font-size: 11.5px; color: var(--el-text-color-placeholder); }
.cards { display: flex; flex-direction: column; gap: 16px; }
.empty-card {
  display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 36px 24px; text-align: center;
  background: var(--autotune-surface); border: 1px dashed var(--el-border-color); border-radius: 10px;
}
.empty-icon {
  width: 52px; height: 52px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
  color: var(--el-color-primary); background: var(--el-color-primary-light-9);
}
.empty-title { font-size: 15px; font-weight: 600; }
.empty-text { font-size: 13px; color: var(--autotune-muted); max-width: 440px; line-height: 1.5; margin-bottom: 6px; }
.frozen-head { margin-top: 36px; align-items: center; margin-bottom: 12px; }
.section-title { font-size: 16px; font-weight: 600; margin: 0; display: flex; align-items: center; gap: 6px; }
.section-icon { color: var(--el-color-primary); }
.frozen-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.frozen {
  display: flex; flex-direction: column; gap: 6px; padding: 12px 14px;
  background: var(--autotune-surface); border: 1px solid var(--autotune-border); border-radius: 10px;
}
.frozen-top { display: flex; align-items: center; gap: 8px; min-width: 0; }
.frozen-name { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.frozen-facts { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12.5px; color: var(--autotune-muted); }
.frozen-facts b { color: var(--autotune-text); font-weight: 600; }
.frozen-from { color: var(--el-text-color-placeholder); }
.frozen-chips { margin-top: 4px; }
.empty { padding: 16px 4px; line-height: 1.6; }
@media (max-width: 900px) {
  .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
