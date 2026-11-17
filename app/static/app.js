import { RasterViewer } from './viewer.js';

const $ = id => document.getElementById(id);
const number = (value, digits = 1) => value == null ? '—' : new Intl.NumberFormat('fa-IR', { maximumFractionDigits: digits }).format(value);
const files = { dem: null, dsm: null };
let busy = false;
let completed = false;
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
  if (file && (file.size > 512 * 1024 ** 2 || !file.size)) {
    showError('فایل باید غیرخالی و کوچک‌تر از ۵۱۲ مگابایت باشد.');
    return;
  }
  files[kind] = file;
  $(`${kind}-name`).textContent = file?.name || (kind === 'dem' ? 'فایل زمین را انتخاب کنید' : 'فایل سطح را انتخاب کنید');
  $(`${kind}-name`).dir = file ? 'auto' : 'rtl';
  $(`${kind}-size`).textContent = file ? `${number(file.size / 1024 ** 2, 2)} مگابایت · آمادهٔ ارسال` : 'یا فایل را اینجا رها کنید';
  $(`${kind}-zone`).classList.toggle('has-file', !!file);
  $(`${kind}-remove`).hidden = !file;
  if (!file) $(`${kind}-file`).value = '';
  $('error-message').hidden = true;
  markStale();
}

for (const kind of ['dem', 'dsm']) {
  const zone = $(`${kind}-zone`);
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
      const form = new FormData();
      form.append('dem', files.dem);
      form.append('dsm', files.dsm);
      form.append('threshold', threshold);
      form.append('inclusive', $('inclusive').checked);
      form.append('dem_unit', $('dem-unit').value);
      form.append('dsm_unit', $('dsm-unit').value);
      form.append('resampling', $('resampling').value);
      form.append('same_datum', $('same-datum').checked);
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
  ];
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
  $('grid-info').textContent = result.grid.crs;
  $('map-resolution').textContent = `${number(result.grid.width, 0)} × ${number(result.grid.height, 0)} پیکسل · پیش‌نمایش کاهش‌یافته`;
  $('viewer-status').textContent = `ارتفاع از زمین ${result.comparison === '>=' ? '≥' : '>'} ${number(result.threshold_m)} متر`;
  $('result-details').hidden = false;
  $('stale-notice').hidden = true;
  completed = true;
  // Downloads and numerical results remain usable if image loading fails.
  try {
    await viewer.load(url);
    $('empty-state').hidden = true;
    $('map-controls').hidden = false;
    $('mask-toggle').hidden = viewer.layer === 'mask';
    document.querySelectorAll('[data-layer]').forEach(button => { button.disabled = false; });
  } catch (error) { showError(error.message); }
}

document.querySelectorAll('[data-layer]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-layer]').forEach(item => item.classList.toggle('active', item === button));
  viewer.setLayer(button.dataset.layer);
  $('mask-toggle').hidden = button.dataset.layer === 'mask';
}));
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
}).catch(() => { $('hardware-status').textContent = 'ارتباط با سرور در دسترس نیست'; });

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
