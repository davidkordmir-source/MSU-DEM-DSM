import { RasterViewer } from './viewer.js';

const $ = id => document.getElementById(id);
const number = (value, digits = 1) => value == null ? '—' : new Intl.NumberFormat('fa-IR', { maximumFractionDigits: digits }).format(value);
const files = { dem: null, dsm: null };
let aoiFiles = [];
let busy = false;
let completed = false;
let desktopSession = null;
let outputFolder = null;
let activeInput = null;
let lastResult = null;
const inputPreviews = { dem: null, dsm: null };
const viewer = new RasterViewer($('map-canvas'), $('renderer-status'));

function showError(message) {
  $('error-message').textContent = message;
  $('error-message').hidden = false;
}

function markStale() {
  if (completed) $('stale-notice').hidden = false;
}

function setFile(kind, file) {
  if (busy) return;
  if (file && !/\.tiff?$/i.test(file.name)) {
    showError('فایل باید GeoTIFF با پسوند tif یا tiff باشد.');
    return;
  }
  if (file && !file.size) {
    showError('فایل انتخاب‌شده خالی است.');
    return;
  }
  files[kind] = file;
  inputPreviews[kind] = null;
  document.querySelector('[data-input-layer="' + kind + '"]').disabled = true;
  if (file) {
    activeInput = kind;
    $('input-preview-status').textContent = 'آماده‌سازی پیش‌نمایش ' + kind.toUpperCase() + '…';
    file.ready = file.localToken ? Promise.resolve(file.localToken) : stageInput(file);
    file.ready.then(token => requestInputPreview(kind, file, token)).catch(error => {
      if (files[kind] === file && activeInput === kind) $('input-preview-status').textContent = error.message;
    });
  } else if (viewer.layer === 'input_' + kind) {
    const other = kind === 'dem' ? 'dsm' : 'dem';
    if (inputPreviews[other]) activateInput(other);
    else if (lastResult) activateResult('height');
    else {
      viewer.canvas.hidden = true;
      $('empty-state').hidden = false;
      $('map-controls').hidden = true;
      $('input-preview-status').textContent = '';
    }
  }
  $(`${kind}-name`).textContent = file?.name || (kind === 'dem' ? 'فایل زمین را انتخاب کنید' : 'فایل سطح را انتخاب کنید');
  $(`${kind}-name`).dir = file ? 'auto' : 'rtl';
  $(`${kind}-size`).textContent = file ? `${number(file.size / 1024 ** 2, 2)} مگابایت · ${file.localToken ? 'خواندن مستقیم از دیسک' : 'آمادهٔ پردازش'}` : 'یا فایل را اینجا رها کنید';
  $(`${kind}-zone`).classList.toggle('has-file', !!file);
  $(`${kind}-remove`).hidden = !file;
  if (!file) $(`${kind}-file`).value = '';
  $('error-message').hidden = true;
  markStale();
}

async function stageInput(file) {
  const form = new FormData();
  form.append('file', file);
  const info = await responseJSON(await fetch('/api/inputs', { method: 'POST', body: form }));
  file.inputToken = info.token;
  return info.token;
}

async function requestInputPreview(kind, file, token) {
  if (files[kind] !== file) return;
  const form = new FormData();
  form.append('token', token);
  const headers = desktopSession ? { 'X-MSU-Session': desktopSession } : {};
  const entry = await responseJSON(await fetch('/api/previews', { method: 'POST', body: form, headers }));
  while (files[kind] === file) {
    const status = await responseJSON(await fetch('/api/previews/' + entry.id));
    if (status.status === 'error') throw new Error(status.message);
    if (status.status === 'complete') {
      await viewer.loadInput(kind, '/api/previews/' + entry.id + '/image.png', status.result.aspect, () => files[kind] === file);
      if (files[kind] !== file) return;
      inputPreviews[kind] = status.result;
      document.querySelector('[data-input-layer="' + kind + '"]').disabled = false;
      if (activeInput === kind && !busy) activateInput(kind);
      return;
    }
    if (activeInput === kind) $('input-preview-status').textContent = 'پیش‌نمایش ' + kind.toUpperCase() + ' · ' + number(status.progress, 0) + '٪';
    await new Promise(resolve => setTimeout(resolve, 350));
  }
}

function activateInput(kind) {
  const info = inputPreviews[kind];
  if (!info) return;
  activeInput = kind;
  viewer.setLayer('input_' + kind);
  viewer.reset();
  $('empty-state').hidden = true;
  $('map-controls').hidden = false;
  $('mask-toggle').hidden = true;
  $('aoi-legend').hidden = true;
  $('demo-badge').hidden = true;
  $('viewer-status').textContent = 'ورودی ' + kind.toUpperCase() + ' · ' + files[kind].name;
  $('map-resolution').textContent = number(info.width, 0) + ' × ' + number(info.height, 0) + ' پیکسل';
  $('grid-info').textContent = info.crs;
  $('input-preview-status').textContent = info.note + (info.valid_samples ? '' : ' نمونهٔ معتبر در نمای سریع پیدا نشد.');
  document.querySelectorAll('[data-layer]').forEach(button => button.classList.remove('active'));
  document.querySelectorAll('[data-input-layer]').forEach(button => button.classList.toggle('active', button.dataset.inputLayer === kind));
}

function activateResult(layer) {
  activeInput = null;
  viewer.setLayer(layer);
  viewer.reset();
  $('mask-toggle').hidden = layer === 'mask';
  $('input-preview-status').textContent = '';
  document.querySelectorAll('[data-input-layer]').forEach(button => button.classList.remove('active'));
  document.querySelectorAll('[data-layer]').forEach(button => button.classList.toggle('active', button.dataset.layer === layer));
  if (lastResult) {
    const result = lastResult.result;
    $('grid-info').textContent = result.grid.crs;
    $('map-resolution').textContent = number(result.grid.width, 0) + ' × ' + number(result.grid.height, 0) + ' پیکسل · نتیجهٔ استخراج';
    $('viewer-status').textContent = 'ارتفاع از زمین ' + result.comparison + ' ' + number(result.threshold_m) + ' متر';
    $('aoi-legend').hidden = !result.aoi;
    $('demo-badge').hidden = !lastResult.demo;
  }
}

for (const kind of ['dem', 'dsm']) {
  const zone = $(`${kind}-zone`);
  zone.addEventListener('click', async event => {
    if (!desktopSession || busy) return;
    event.preventDefault();
    try {
      const result = await responseJSON(await fetch(`/api/desktop/pick-raster?kind=${kind}`, { method: 'POST', headers: { 'X-MSU-Session': desktopSession } }));
      if (result.file) setFile(kind, result.file);
    } catch (error) { showError(error.message); }
  });
  $(`${kind}-file`).addEventListener('change', event => setFile(kind, event.target.files[0]));
  $(`${kind}-remove`).addEventListener('click', () => setFile(kind, null));
  zone.addEventListener('dragover', event => { event.preventDefault(); if (!busy) zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', event => {
    event.preventDefault();
    zone.classList.remove('dragover');
    setFile(kind, event.dataTransfer.files[0]);
  });
}

function setAoi(inputFiles) {
  if (busy) return;
  const selected = Array.from(inputFiles || []);
  const extensions = selected.map(file => file.name.split('.').pop().toLowerCase());
  if (extensions.some(ext => !['zip', 'shp', 'shx', 'dbf', 'prj', 'cpg'].includes(ext)) || selected.length > 8) {
    showError('یک ZIP یا فایل‌های SHP، SHX، DBF و PRJ یک لایه را انتخاب کنید.'); return;
  }
  if (selected.some(file => !file.size) || selected.reduce((sum, file) => sum + file.size, 0) > 50 * 1024 ** 2) {
    showError('فایل‌های محدوده باید غیرخالی و در مجموع کمتر از ۵۰ مگابایت باشند.'); return;
  }
  if (extensions.includes('zip') && selected.length !== 1) {
    showError('ZIP محدوده را به‌تنهایی انتخاب کنید.'); return;
  }
  aoiFiles = selected;
  $('aoi-name').textContent = selected.length ? (selected.find(file => /\.(shp|zip)$/i.test(file.name)) || selected[0]).name : 'انتخاب مرز محدوده';
  $('aoi-name').dir = selected.length ? 'auto' : 'rtl';
  $('aoi-size').textContent = selected.length ? `${number(selected.length, 0)} فایل · ${number(selected.reduce((sum, file) => sum + file.size, 0) / 1024 ** 2, 2)} مگابایت` : 'ZIP یا اجزای شیپ‌فایل · حداکثر ۵۰ مگابایت';
  $('aoi-remove').hidden = !selected.length;
  $('aoi-zone').classList.toggle('has-file', !!selected.length);
  if (!selected.length) $('aoi-files').value = '';
  $('error-message').hidden = true;
  markStale();
}
$('aoi-files').addEventListener('change', event => setAoi(event.target.files));
$('aoi-remove').addEventListener('click', () => setAoi([]));
$('aoi-zone').addEventListener('dragover', event => { event.preventDefault(); if (!busy) $('aoi-zone').classList.add('dragover'); });
$('aoi-zone').addEventListener('dragleave', () => $('aoi-zone').classList.remove('dragover'));
$('aoi-zone').addEventListener('drop', event => { event.preventDefault(); $('aoi-zone').classList.remove('dragover'); setAoi(event.dataTransfer.files); });

$('choose-output').addEventListener('click', async () => {
  if (busy) return;
  try {
    const result = await responseJSON(await fetch('/api/desktop/pick-output', { method: 'POST', headers: { 'X-MSU-Session': desktopSession } }));
    if (result.folder) {
      outputFolder = result.folder;
      $('output-path').textContent = result.folder.path;
      $('output-space').textContent = number(result.folder.free_bytes / 1024 ** 3, 1) + ' گیگابایت فضای آزاد · نتایج در زیرپوشهٔ جدا ذخیره می‌شوند';
    }
  } catch (error) { showError(error.message); }
});

function syncThreshold(value) {
  if (!Number.isFinite(value)) return;
  $('threshold').value = Math.max(0, value);
  $('threshold-range').value = Math.min(150, Math.max(0, value));
  markStale();
}
$('threshold-range').addEventListener('input', event => syncThreshold(Number(event.target.value)));
$('threshold').addEventListener('input', event => {
  $('threshold-range').value = Math.min(150, Math.max(0, Number(event.target.value)));
  markStale();
});
$('decrease').addEventListener('click', () => syncThreshold(Number($('threshold').value) - 1));
$('increase').addEventListener('click', () => syncThreshold(Number($('threshold').value) + 1));
for (const id of ['inclusive', 'dem-unit', 'dsm-unit', 'resampling', 'same-datum']) $(id).addEventListener('change', markStale);

function setBusy(value) {
  busy = value;
  $('form-fields').disabled = value;
  $('demo-button').disabled = value;
  $('empty-demo').disabled = value;
  $('processing-overlay').hidden = !value;
  $('analysis-form').setAttribute('aria-busy', String(value));
}

function updateProgress(pct, message) {
  $('progress-message').textContent = message;
  $('progress-bar').style.width = `${Math.max(0, Math.min(100, pct))}%`;
  $('progress-label').textContent = `${number(pct, 0)}٪`;
}

async function responseJSON(response) {
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'درخواست معتبر نیست؛ فایل‌ها و تنظیمات را بررسی کنید.');
  return payload;
}

function upload(form) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', '/api/jobs');
    if (desktopSession) request.setRequestHeader('X-MSU-Session', desktopSession);
    request.responseType = 'json';
    request.upload.onprogress = event => {
      if (event.lengthComputable) updateProgress(Math.round(event.loaded / event.total * 100), 'ارسال فایل‌های ارتفاعی');
    };
    request.onerror = () => reject(new Error('ارتباط با سرور قطع شد؛ اتصال را بررسی و دوباره تلاش کنید.'));
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) resolve(request.response);
      else reject(new Error(typeof request.response?.detail === 'string' ? request.response.detail : 'بارگذاری انجام نشد؛ فایل‌ها را بررسی کنید.'));
    };
    request.send(form);
  });
}

async function poll(id) {
  let failures = 0;
  while (true) {
    let job;
    try {
      job = await responseJSON(await fetch(`/api/jobs/${id}`, { cache: 'no-store' }));
      failures = 0;
    } catch (error) {
      if (++failures >= 4) throw error;
      await new Promise(resolve => setTimeout(resolve, 1500));
      continue;
    }
    updateProgress(job.progress, job.message);
    if (job.status === 'error') throw new Error(job.message);
    if (job.status === 'complete') return job;
    await new Promise(resolve => setTimeout(resolve, 600));
  }
}

async function run(demo = false) {
  if (busy) return;
  $('error-message').hidden = true;
  const threshold = Number($('threshold').value);
  if (!$('threshold').value.trim() || !Number.isFinite(threshold) || threshold < 0) {
    showError('یک آستانهٔ نامنفی و معتبر بر حسب متر وارد کنید.');
    $('threshold').focus();
    return;
  }
  if (!demo && (!files.dem || !files.dsm)) {
    showError('هر دو فایل DEM و DSM را انتخاب کنید.');
    (!files.dem ? $('dem-file') : $('dsm-file')).focus();
    return;
  }
  if (!demo && !$('same-datum').checked) {
    showError('برای مقایسهٔ معتبر، یکسان بودن مبنای ارتفاعی دو فایل را تأیید کنید.');
    $('same-datum').focus();
    return;
  }
  setBusy(true);
  updateProgress(0, demo ? 'آماده‌سازی منطقهٔ نمونه' : 'ارسال فایل‌های ارتفاعی');
  if (window.innerWidth < 681) $('viewer-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  try {
    let result;
    if (demo) {
      result = await responseJSON(await fetch(`/api/demo?threshold=${encodeURIComponent(threshold)}&inclusive=${$('inclusive').checked}`, { method: 'POST' }));
    } else {
      await Promise.all([files.dem.ready, files.dsm.ready]);
      const form = new FormData();
      for (const kind of ['dem', 'dsm']) {
        if (files[kind].localToken || files[kind].inputToken) form.append(`${kind}_token`, files[kind].localToken || files[kind].inputToken);
        else form.append(kind, files[kind]);
      }
      form.append('threshold', threshold);
      form.append('inclusive', $('inclusive').checked);
      form.append('dem_unit', $('dem-unit').value);
      form.append('dsm_unit', $('dsm-unit').value);
      form.append('resampling', $('resampling').value);
      form.append('same_datum', $('same-datum').checked);
      aoiFiles.forEach(file => form.append('aoi_files', file));
      if (outputFolder) form.append('output_token', outputFolder.token);
      result = await upload(form);
    }
    sessionStorage.setItem('msu-job', result.id);
    updateProgress(0, 'در صف پردازش');
    const job = await poll(result.id);
    await showResult(job);
  } catch (error) {
    showError(error.message || 'پردازش انجام نشد. دوباره تلاش کنید.');
  } finally {
    setBusy(false);
  }
}

$('analysis-form').addEventListener('submit', event => { event.preventDefault(); run(); });
$('demo-button').addEventListener('click', () => run(true));
$('empty-demo').addEventListener('click', () => run(true));

const methodNames = { none: 'بدون بازنمونه‌برداری', bilinear: 'درون‌یابی دوخطی', average: 'میانگین‌گیری', nearest: 'نزدیک‌ترین همسایه' };
const horizontalUnit = unit => ({ metre: 'متر', meter: 'متر', degree: 'درجه', foot: 'فوت', 'US survey foot': 'فوت پیمایشی' })[unit] || unit;
function resolutionLabel(input) { return `${input.resolution.map(x => number(x, 5)).join(' × ')} ${horizontalUnit(input.horizontal_unit)}`; }

async function showResult(job) {
  const result = job.result;
  lastResult = job;
  $('download-shapefile').href = '/api/jobs/' + job.id + '/files/features.zip';
  $('download-shapefile').hidden = !result.vector;
  $('attribute-preview').hidden = !result.vector;
  $('attribute-count').textContent = result.vector ? number(result.vector.feature_count, 0) + ' عارضه · نمایش حداکثر ۲۰ ردیف' : '';
  $('attribute-rows').replaceChildren(...(result.vector?.sample || []).map(row => {
    const tr = document.createElement('tr');
    for (const name of ['OBJ_ID', 'PIXELS', 'H_MIN_M', 'H_MAX_M', 'H_MEAN_M', 'AREA_M2']) {
      const td = document.createElement('td');
      td.textContent = number(row[name], 3);
      tr.append(td);
    }
    return tr;
  }));
  $('retention-note').textContent = job.permanent_output ? 'نتایج در ' + job.output_directory + ' ذخیره شدند و خودکار پاک نمی‌شوند.' : 'نتایج موقت تا ۲۴ ساعت نگهداری می‌شوند؛ خروجی‌های موردنیاز را دریافت کنید.';
  const url = `/api/jobs/${job.id}/files`;
  $('stat-pixels').textContent = number(result.selected_pixels, 0);
  $('stat-percent').textContent = `${number(result.selected_percent)}٪ از پیکسل‌های معتبر`;
  $('stat-area').textContent = number(result.selected_area_m2);
  $('stat-max').textContent = number(result.max_height_m, 2);
  $('stat-mean').textContent = number(result.mean_height_m, 2);
  $('stat-duration').textContent = `پردازش در ${number(result.elapsed_seconds, 2)} ثانیه`;
  $('result-summary').textContent = `${result.comparison === '>=' ? 'بزرگ‌تر یا مساوی' : 'بزرگ‌تر از'} ${number(result.threshold_m)} متر از زمین${result.selected_pixels === 0 ? ' · هیچ پیکسلی انتخاب نشد' : ''}`;
  for (const [id, name] of Object.entries({ all: 'results.zip', extracted: 'extracted.tif', height: 'height.tif', mask: 'mask.tif', report: 'report.json' })) {
    $(`download-${id}`).href = `${url}/${name}`;
    $(`download-${id}`).download = name;
  }
  const entries = [
    ['تفکیک‌پذیری DEM', resolutionLabel(result.inputs.dem)],
    ['تفکیک‌پذیری DSM', resolutionLabel(result.inputs.dsm)],
    ['روش تبدیل DEM', methodNames[result.resampling]],
    ['CRS خروجی', result.grid.crs],
    ['اندازهٔ خروجی', `${number(result.grid.width, 0)} × ${number(result.grid.height, 0)}`],
    ['هسته‌های پردازشی', number(result.resources.cpu_threads, 0)],
    ['محدودهٔ پردازش', result.aoi ? `${result.aoi.layer} · ${number(result.aoi.feature_count, 0)} عارضه` : 'کل پوشش مشترک'],
  ];
  if (result.aoi) entries.push(['CRS شیپ‌فایل', result.aoi.source_crs]);
  $('alignment-info').replaceChildren(...entries.flatMap(([label, value]) => {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = label; dd.textContent = value;
    return [dt, dd];
  }));
  $('warnings').replaceChildren(...result.warnings.map(warning => {
    const li = document.createElement('li'); li.textContent = warning; return li;
  }));
  const peak = Math.max(...result.histogram.counts, 1);
  $('histogram').replaceChildren(...result.histogram.labels.map((label, index) => {
    const col = document.createElement('div'), bar = document.createElement('div'), text = document.createElement('span');
    col.className = 'bar-column'; bar.className = 'bar'; text.className = 'bar-label';
    bar.style.height = `${result.histogram.counts[index] / peak * 75}px`;
    bar.title = `${label} متر: ${number(result.histogram.counts[index], 0)} پیکسل`;
    bar.setAttribute('aria-label', bar.title);
    text.textContent = label;
    col.append(bar, text); return col;
  }));
  $('demo-badge').hidden = !job.demo;
  $('aoi-legend').hidden = !result.aoi;
  $('grid-info').textContent = result.grid.crs;
  $('map-resolution').textContent = `${number(result.grid.width, 0)} × ${number(result.grid.height, 0)} پیکسل · پیش‌نمایش کاهش‌یافته`;
  $('viewer-status').textContent = `ارتفاع از زمین ${result.comparison === '>=' ? '≥' : '>'} ${number(result.threshold_m)} متر`;
  $('result-details').hidden = false;
  $('stale-notice').hidden = true;
  completed = true;
  // Downloads and numerical results remain usable if image loading fails.
  try {
    await viewer.load(url);
    activateResult('height');
    $('empty-state').hidden = true;
    $('map-controls').hidden = false;
    $('mask-toggle').hidden = viewer.layer === 'mask';
    document.querySelectorAll('[data-layer]').forEach(button => { button.disabled = false; });
  } catch (error) { showError(error.message); }
}

document.querySelectorAll('[data-layer]').forEach(button => button.addEventListener('click', () => {
  activateResult(button.dataset.layer);
}));
document.querySelectorAll('[data-input-layer]').forEach(button => button.addEventListener('click', () => activateInput(button.dataset.inputLayer)));
$('show-mask').addEventListener('change', event => viewer.setOverlay(event.target.checked));
$('zoom-in').addEventListener('click', () => viewer.zoomBy(1.4));
$('zoom-out').addEventListener('click', () => viewer.zoomBy(1 / 1.4));
$('reset-view').addEventListener('click', () => viewer.reset());
$('fullscreen').addEventListener('click', async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else if ($('viewer-card').requestFullscreen) await $('viewer-card').requestFullscreen();
  } catch { showError('نمایش تمام‌صفحه در این مرورگر در دسترس نیست.'); }
});
$('help-button').addEventListener('click', () => $('help-dialog').showModal());
$('close-help').addEventListener('click', () => $('help-dialog').close());
$('help-dialog').addEventListener('click', event => { if (event.target === $('help-dialog')) $('help-dialog').close(); });

fetch('/api/health').then(responseJSON).then(info => {
  $('hardware-status').textContent = `${number(info.resources.cpu_threads, 0)} هسته · ${number(info.resources.gdal_cache_mb / 1024, 1)} گیگابایت کش`;
  desktopSession = info.desktop_session;
  $('desktop-exit').hidden = !desktopSession;
  $('output-settings').hidden = !desktopSession;
}).catch(() => { $('hardware-status').textContent = 'ارتباط با سرور در دسترس نیست'; });

$('desktop-exit').addEventListener('click', async () => {
  if (busy && !window.confirm('پردازش هنوز در حال انجام است. از برنامه خارج می‌شوید؟')) return;
  try {
    await responseJSON(await fetch('/api/desktop/exit', { method: 'POST', headers: { 'X-MSU-Session': desktopSession } }));
    document.body.replaceChildren(Object.assign(document.createElement('p'), { textContent: 'برنامه بسته شد. می‌توانید این پنجره را ببندید.' }));
    window.close();
  } catch { showError('خروج انجام نشد؛ دوباره تلاش کنید.'); }
});

// Resume a long-running job after refresh without uploading the rasters again.
const savedJob = sessionStorage.getItem('msu-job');
if (savedJob && /^[a-f0-9]{32}$/.test(savedJob)) {
  setBusy(true);
  updateProgress(0, 'بازیابی پردازش قبلی');
  poll(savedJob).then(async job => {
    syncThreshold(job.result.threshold_m);
    $('inclusive').checked = job.result.comparison === '>=';
    await showResult(job);
  }).catch(error => { sessionStorage.removeItem('msu-job'); showError(error.message); })
    .finally(() => setBusy(false));
}
