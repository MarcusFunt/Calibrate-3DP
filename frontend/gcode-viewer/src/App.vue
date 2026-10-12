<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue';

const GcodePreview = defineAsyncComponent(() =>
  import('@chestnutlabs/gcode-preview-vue').then((module) => module.GcodePreview));

const source = shallowRef(null);
const fileName = ref('');
const renderer = ref('3d');
const webglAvailable = ref(false);
const transfer = ref({ status: 'waiting', bytes: 0, total: 0, message: 'Preparing local G-code…' });
const preview = ref({ status: 'waiting', summary: null, message: 'Waiting for the local artifact.' });
const layerRange = ref(null);
const showTravel = ref(false);
const visibleLayer = ref(0);
const chunks = [];
let expectedBytes = 0;
let receivedBytes = 0;

const transferPercent = computed(() => transfer.value.total
  ? Math.min(100, Math.round((transfer.value.bytes / transfer.value.total) * 100))
  : 0);
const layers = computed(() => preview.value.summary?.layers ?? 0);

function beginLoad(name, totalBytes) {
  fileName.value = name;
  expectedBytes = totalBytes;
  receivedBytes = 0;
  chunks.length = 0;
  layerRange.value = null;
  visibleLayer.value = 0;
  preview.value = { status: 'waiting', summary: null, message: 'Waiting for the local artifact.' };
  transfer.value = { status: 'loading', bytes: 0, total: totalBytes, message: 'Loading verified local artifact…' };
  return true;
}

function pushChunk(encoded) {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  chunks.push(bytes);
  receivedBytes += bytes.byteLength;
  transfer.value = {
    status: 'loading',
    bytes: receivedBytes,
    total: expectedBytes,
    message: 'Loading verified local artifact…',
  };
  return true;
}

function finishLoad() {
  const file = new File(chunks, fileName.value, { type: 'application/octet-stream' });
  chunks.length = 0;
  source.value = file;
  transfer.value = {
    status: 'complete', bytes: expectedBytes, total: expectedBytes,
    message: 'Local file ready · parsing in the viewer worker…',
  };
  preview.value = { status: 'parsing', summary: null, message: 'Parsing the toolpath…' };
  return true;
}

function failLoad(message) {
  chunks.length = 0;
  transfer.value = { status: 'failed', bytes: 0, total: expectedBytes, message };
  preview.value = { status: 'failed', summary: null, message };
  return true;
}

function parseProgress(progress) {
  const percent = progress.totalBytes
    ? Math.min(100, Math.round((progress.bytesProcessed / progress.totalBytes) * 100))
    : 0;
  preview.value = { ...preview.value, status: 'parsing', message: `Parsing toolpath · ${percent}%` };
}

function preparationStage(event) {
  const percent = Number.isFinite(event.progress) ? ` · ${Math.round(event.progress * 100)}%` : '';
  preview.value = { ...preview.value, status: 'building', message: `Preparing preview · ${event.stage}${percent}` };
}

function onReady(summary) {
  visibleLayer.value = summary.layers;
  preview.value = { status: 'ready', summary, message: 'Preview ready' };
}

function onError(error) {
  const message = error?.message || 'The G-code viewer could not parse this file.';
  preview.value = { ...preview.value, status: 'failed', message };
}

function onDisclosure(message) {
  preview.value = { ...preview.value, disclosure: message };
}

function detectWebGL() {
  try {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('webgl2', { failIfMajorPerformanceCaveat: true })
      || canvas.getContext('webgl', { failIfMajorPerformanceCaveat: true });
    const supported = Boolean(context);
    context?.getExtension('WEBGL_lose_context')?.loseContext();
    return supported;
  } catch {
    return false;
  }
}

function setRenderer(event) {
  const requested = event.target.value;
  if (requested === '3d' && !webglAvailable.value) return;
  renderer.value = requested;
  if (source.value) {
    preview.value = { ...preview.value, status: 'parsing', message: 'Preparing selected view…' };
  }
}

function setVisibleLayer(value) {
  visibleLayer.value = Number(value);
  layerRange.value = visibleLayer.value >= layers.value ? null : [0, visibleLayer.value - 1];
}

onMounted(() => {
  webglAvailable.value = detectWebGL();
  renderer.value = webglAvailable.value ? '3d' : '2d';
  window.calibrate3dpPreview = { beginLoad, pushChunk, finishLoad, failLoad };
});

onBeforeUnmount(() => {
  delete window.calibrate3dpPreview;
});
</script>

<template>
  <main class="viewer-shell">
    <header class="viewer-header">
      <div class="brand-mark" aria-hidden="true">C3</div>
      <div class="heading">
        <p class="eyebrow">PLATE INSPECTION</p>
        <h1>G-code preview</h1>
        <p class="filename" :title="fileName">{{ fileName || 'Waiting for a saved G-code artifact' }}</p>
      </div>
      <label class="view-mode">
        <span>View mode</span>
        <select :value="renderer" aria-label="View mode" @change="setRenderer">
          <option value="3d" :disabled="!webglAvailable">3D toolpath</option>
          <option value="2d">2D layers</option>
        </select>
      </label>
      <span class="status-pill" :class="`status-${preview.status}`" role="status">
        <span class="status-dot" aria-hidden="true"></span>{{ preview.message }}
      </span>
    </header>

    <section v-if="transfer.status === 'loading'" class="load-panel" aria-live="polite">
      <div class="load-heading"><strong>Opening saved file</strong><span>{{ transferPercent }}%</span></div>
      <progress :value="transfer.bytes" :max="transfer.total || 1"></progress>
      <p>{{ transfer.message }}</p>
    </section>

    <section v-if="transfer.status === 'failed' || preview.status === 'failed'" class="error-panel" role="alert">
      <strong>Preview unavailable</strong>
      <p>{{ transfer.status === 'failed' ? transfer.message : preview.message }}</p>
    </section>

    <section v-if="preview.disclosure" class="disclosure" role="status">{{ preview.disclosure }}</section>

    <section v-if="preview.status === 'ready'" class="summary-row" aria-label="Parsed file summary">
      <div><span class="summary-label">Layers</span><strong>{{ preview.summary.layers.toLocaleString() }}</strong></div>
      <div><span class="summary-label">Toolpath segments</span><strong>{{ preview.summary.segments.toLocaleString() }}</strong></div>
      <div><span class="summary-label">Parse coverage</span><strong>{{ preview.summary.complete ? 'Complete' : 'Partial' }}</strong></div>
      <div v-if="preview.summary.warnings?.length" class="warning-count">
        <span class="summary-label">Viewer notes</span><strong>{{ preview.summary.warnings.length }}</strong>
      </div>
    </section>

    <section class="canvas-frame" aria-label="Interactive G-code toolpath">
      <GcodePreview
        v-if="source"
        :source="source"
        :key="renderer"
        :renderer="renderer"
        quality="auto"
        :layer-range="layerRange"
        :show-travel="showTravel"
        progressive-preview="hold"
        @ready="onReady"
        @parse-progress="parseProgress"
        @stage="preparationStage"
        @error="onError"
        @parse-error="onError"
        @disclosure="onDisclosure"
      />
      <div v-else class="canvas-placeholder" aria-live="polite">
        <div class="placeholder-icon" aria-hidden="true">⌁</div>
        <strong>Preparing the toolpath view</strong>
        <span>The saved artifact is read locally and stays on this computer.</span>
      </div>
    </section>

    <footer class="viewer-controls">
      <label class="layer-control" :class="{ disabled: preview.status !== 'ready' }">
        <span>Show through layer</span>
        <input
          type="range"
          min="1"
          :max="Math.max(layers, 1)"
          :value="visibleLayer || Math.max(layers, 1)"
          :disabled="preview.status !== 'ready' || layers < 2"
          :aria-label="`Show toolpath through layer ${visibleLayer || layers}`"
          @input="setVisibleLayer($event.target.value)"
        />
        <strong>{{ visibleLayer || layers || '—' }}<small v-if="layers"> / {{ layers }}</small></strong>
      </label>
      <label class="toggle-control">
        <input v-model="showTravel" type="checkbox" :disabled="preview.status !== 'ready'" />
        <span>Show travel moves</span>
      </label>
      <p class="inspection-note">
        <template v-if="!webglAvailable">3D graphics are unavailable here; showing 2D layers. · </template>
        Visual inspection only · This preview does not validate print safety.
      </p>
    </footer>
  </main>
</template>
