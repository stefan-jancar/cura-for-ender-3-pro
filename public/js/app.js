// Kompilátor Cura → Ender-3 Pro – rozhranie pre PC (lokálny server), web a mobil (Vercel).
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const store = {
  get(k, d) { try { const v = localStorage.getItem('kc_' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('kc_' + k, JSON.stringify(v)); } catch { /* súkromné okno */ } },
};

const NATIVE = !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
let info = null;
let desktop = false;
let jobs = [];
let srcMode = 'url';
let pickedFiles = [];
let pollTimer = null;
const busy = new Set();

const STATUS = {
  nahrava: ['Nahráva sa', 'info'],
  caka: ['Čaká na PC', 'warn'],
  spracuva: ['Spracúva sa', 'info'],
  hotovo: ['Hotovo', 'ok'],
  chyba: ['Chyba', 'err'],
};

// ---------------------------------------------------------------- API
async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const token = store.get('token', '');
  if (token) headers.Authorization = 'Bearer ' + token;
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) { showLogin(); throw new Error('Neprihlásený'); }
  if (!r.ok) {
    let msg = `Chyba ${r.status}`;
    try { const j = await r.json(); msg = j.detail || msg; } catch { /* nie JSON */ }
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('application/json') ? r.json() : r;
}

function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { t.hidden = true; }, ms);
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function fmtTime(s) {
  if (!s) return '–';
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}
function fmtSize(b) {
  if (b > 1048576) return (b / 1048576).toFixed(1).replace('.', ',') + ' MB';
  return Math.max(1, Math.round(b / 1024)) + ' kB';
}
function ago(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return 'práve teraz';
  if (d < 3600) return `pred ${Math.round(d / 60)} min`;
  if (d < 86400) return `pred ${Math.round(d / 3600)} h`;
  return new Date(ts * 1000).toLocaleDateString('sk-SK');
}

// ---------------------------------------------------------------- views
function show(view) {
  for (const v of ['login', 'main', 'settings']) $('#view-' + v).hidden = v !== view;
  window.scrollTo({ top: 0 });
}
function showLogin() { show('login'); setTimeout(() => $('#login-pw').focus(), 50); }

function setStatus() {
  const el = $('#status');
  if (desktop) {
    const cs = settingsCache?.cloud_status;
    if (settingsCache && !settingsCache.cura_found) { el.className = 'pill err'; el.textContent = 'Cura nenájdená'; return; }
    if (cs?.state === 'online') { el.className = 'pill ok'; el.textContent = 'PC + mobil'; return; }
    if (cs?.state === 'chyba') { el.className = 'pill warn'; el.textContent = 'Mobil: chyba'; return; }
    el.className = 'pill ok'; el.textContent = 'PC pripravené';
    return;
  }
  if (info?.worker_online) { el.className = 'pill ok'; el.textContent = 'PC online'; }
  else { el.className = 'pill warn'; el.textContent = 'PC offline'; el.title = 'Úlohy počkajú, kým zapneš aplikáciu na PC'; }
}

// ---------------------------------------------------------------- form
const OPT_DEFAULT = { material: 'auto', quality: 'standard', supports: 'auto', adhesion: 'auto', strong: false };
let opts = { ...OPT_DEFAULT, ...store.get('opts', {}) };

function buildForm() {
  const mat = $('[data-opt=material]');
  mat.innerHTML = info.materials.map(([v, l]) => `<button type="button" data-v="${v}">${esc(l)}</button>`).join('');
  const q = $('[data-opt=quality]');
  q.innerHTML = info.qualities.map(([v, l]) => `<button type="button" data-v="${v}">${esc(l)}</button>`).join('');
  for (const group of $$('.chips[data-opt]')) {
    const key = group.dataset.opt;
    const paint = () => $$('button', group).forEach((b) => b.classList.toggle('on', b.dataset.v === opts[key]));
    group.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      opts[key] = b.dataset.v;
      store.set('opts', opts);
      paint();
    });
    paint();
  }
  $('#strong').checked = !!opts.strong;
  $('#strong').addEventListener('change', (e) => { opts.strong = e.target.checked; store.set('opts', opts); });

  $$('.seg [data-src]').forEach((b) => b.addEventListener('click', () => setSrc(b.dataset.src)));
  $('#btn-paste').addEventListener('click', async () => {
    try { $('#url').value = (await navigator.clipboard.readText()).trim(); } catch { toast('Schránka nie je dostupná – vlož odkaz ručne'); }
  });
  const drop = $('#drop');
  $('#files').addEventListener('change', (e) => pickFiles(e.target.files));
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (e) => pickFiles(e.dataTransfer.files));
  // súbor pretiahnutý kamkoľvek do okna
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => {
    if (e.target.closest('#drop')) return;
    e.preventDefault();
    if (e.dataTransfer?.files?.length) { setSrc('file'); pickFiles(e.dataTransfer.files); }
  });
  $('#new-job').addEventListener('submit', submitJob);

  // zdieľanie odkazu do PWA (share_target) alebo ?url=
  const p = new URLSearchParams(location.search);
  const shared = p.get('url') || (p.get('text') || '').match(/https?:\/\/\S+/)?.[0];
  if (shared) { $('#url').value = shared; if (p.get('title')) $('#title').value = p.get('title'); }
}

function setSrc(mode) {
  srcMode = mode;
  $$('.seg [data-src]').forEach((b) => b.classList.toggle('on', b.dataset.src === mode));
  $$('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== mode; });
}

function pickFiles(list) {
  const ok = [...list].filter((f) => /\.(stl|3mf|obj|zip)$/i.test(f.name));
  if (!ok.length) { toast('Podporované sú súbory STL, 3MF, OBJ a ZIP'); return; }
  pickedFiles = ok;
  const total = ok.reduce((a, f) => a + f.size, 0);
  $('#drop-text').innerHTML = `<b>${ok.map((f) => esc(f.name)).join('<br>')}</b><br><small>${fmtSize(total)} · klikni pre zmenu</small>`;
}

async function submitJob(e) {
  e.preventDefault();
  const err = $('#form-err');
  err.hidden = true;
  const btn = $('#btn-go');
  const url = $('#url').value.trim();
  if (srcMode === 'url' && !url) { err.textContent = 'Vlož odkaz na model.'; err.hidden = false; return; }
  if (srcMode === 'file' && !pickedFiles.length) { err.textContent = 'Vyber súbor s modelom.'; err.hidden = false; return; }
  btn.disabled = true;
  const label = $('span', btn);
  const old = label.textContent;
  try {
    const body = { title: $('#title').value.trim(), options: opts };
    if (srcMode === 'url') body.url = url;
    else body.files = pickedFiles.map((f) => ({ name: f.name, size: f.size }));
    const job = await api('/api/jobs', { method: 'POST', json: body });
    if (srcMode === 'file') {
      const chunk = info.chunk;
      let sent = 0;
      const total = pickedFiles.reduce((a, f) => a + f.size, 0);
      for (const f of pickedFiles) {
        for (let i = 0, n = 0; i < f.size; i += chunk, n++) {
          label.textContent = `Nahrávam ${Math.round((sent / total) * 100)} %`;
          await api(`/api/jobs/${job.id}/src/${encodeURIComponent(f.name)}/${n}`, {
            method: 'PUT', body: f.slice(i, i + chunk), headers: { 'Content-Type': 'application/octet-stream' },
          });
          sent += Math.min(chunk, f.size - i);
        }
      }
      await api(`/api/jobs/${job.id}/ready`, { method: 'POST' });
    }
    $('#url').value = '';
    $('#title').value = '';
    pickedFiles = [];
    $('#files').value = '';
    $('#drop-text').innerHTML = 'Pretiahni sem súbory alebo klikni<br><small>STL · 3MF · OBJ · ZIP</small>';
    toast(desktop ? 'Spracúvam…' : info.worker_online ? 'Odoslané – PC to spracuje' : 'Odoslané – spracuje sa, keď zapneš PC');
    await loadJobs();
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
    label.textContent = old;
  }
}

// ---------------------------------------------------------------- jobs
async function loadJobs() {
  try {
    jobs = await api('/api/jobs');
    renderJobs();
  } catch (ex) {
    if (ex.message !== 'Neprihlásený') toast(ex.message);
  }
  schedulePoll();
}

function schedulePoll() {
  clearTimeout(pollTimer);
  const active = jobs.some((j) => ['nahrava', 'caka', 'spracuva'].includes(j.status));
  const ms = active ? (desktop ? 1200 : 4000) : (desktop ? 6000 : 20000);
  pollTimer = setTimeout(async () => {
    if (document.hidden) { schedulePoll(); return; }
    if (!desktop) { try { info = await api('/api/info'); setStatus(); } catch { /* offline */ } }
    else if (Date.now() - (loadSettings.at || 0) > 15000) loadSettings();
    loadJobs();
  }, ms);
}

function renderJobs() {
  const box = $('#jobs');
  if (!jobs.length) {
    box.innerHTML = `<div class="empty">Zatiaľ žiadne úlohy. Vlož odkaz na model alebo nahraj súbor.</div>`;
    return;
  }
  box.innerHTML = jobs.map(jobHtml).join('');
}

function jobHtml(j) {
  const [stLabel, stClass] = STATUS[j.status] || [j.status, ''];
  const o = j.options || {};
  const meta = [
    j.origin === 'mobil' ? '📱 z mobilu' : '💻 z PC',
    ago(j.created),
    `${o.material === 'auto' ? 'materiál auto' : o.material} · ${({ rychla: '0,28', standard: '0,2', jemna: '0,12' })[o.quality] || ''} mm${o.strong ? ' · pevný' : ''}`,
  ];
  if (j.url) meta.push(`<a href="${esc(j.url)}" target="_blank" rel="noopener" class="muted">zdroj</a>`);
  let body = '';
  if (['spracuva', 'caka', 'nahrava'].includes(j.status)) {
    const frac = j.progress?.frac || 0;
    const msg = j.progress?.msg || (j.status === 'caka' ? (desktop ? 'V poradí' : 'Čaká, kým PC prevezme úlohu') : 'Nahráva sa');
    body = `<div class="bar ${frac ? '' : 'indet'}"><i style="width:${Math.round(frac * 100)}%"></i></div><div class="progress-msg">${esc(msg)}</div>`;
  } else if (j.status === 'chyba') {
    body = `<p class="err" style="margin-top:10px">${esc(j.error)}</p>`;
  } else if (j.status === 'hotovo' && j.result) {
    body = resultHtml(j);
  }
  return `<article class="job" data-id="${esc(j.id)}">
    <div class="job-head">
      <div><div class="job-title">${esc(j.title)}</div><div class="job-meta">${meta.map((m) => `<span>${m}</span>`).join('')}</div></div>
      <div class="job-actions">
        <span class="pill ${stClass}">${stLabel}</span>
        ${j.status === 'chyba' || j.status === 'hotovo' ? `<button class="btn small ghost" data-act="retry">Znova</button>` : ''}
        ${desktop && j.status === 'hotovo' ? `<button class="btn small ghost" data-act="folder">Priečinok</button>` : ''}
        <button class="btn small ghost danger" data-act="delete" title="Zmazať">✕</button>
      </div>
    </div>
    ${body}
  </article>`;
}

function resultHtml(j) {
  const r = j.result;
  const files = r.files || [];
  const plates = r.plates || [];
  const stats = [
    [plates.length, plates.length === 1 ? 'podložka' : plates.length < 5 ? 'podložky' : 'podložiek'],
    [fmtTime(r.total_time_s), 'čas tlače'],
    [r.total_filament_g ? `${String(r.total_filament_g).replace('.', ',')} g` : '–', 'filament'],
  ];
  const notes = (r.notes || []).map((n) => `<li>${esc(n)}</li>`).join('');
  const plateCards = plates.map((p) => {
    const g = files.find((f) => f.plate === p.index && f.kind === 'gcode');
    const m = files.find((f) => f.plate === p.index && f.kind === '3mf');
    const parts = p.parts.map((x) => esc(x.name)).join(', ');
    return `<div class="plate">
      <div class="plate-head"><span>Podložka ${p.index}</span><span class="tag on">${esc(p.material)}</span></div>
      ${p.svg || ''}
      <div class="tags">
        <span class="tag">${p.parts.length} ${p.parts.length === 1 ? 'diel' : p.parts.length < 5 ? 'diely' : 'dielov'}</span>
        <span class="tag">výška ${String(p.height_mm).replace('.', ',')} mm</span>
        <span class="tag ${p.supports ? 'on' : ''}">${p.supports ? 'podpery' : 'bez podpier'}</span>
        <span class="tag">${p.adhesion}</span>
        ${p.time_s ? `<span class="tag">${fmtTime(p.time_s)}</span>` : ''}
        ${p.filament_g ? `<span class="tag">${String(p.filament_g).replace('.', ',')} g</span>` : ''}
      </div>
      <div class="muted" style="font-size:12px" title="${parts}">${parts.length > 120 ? parts.slice(0, 120) + '…' : parts}</div>
      <div class="plate-btns">${fileButtons(j, g, true)}${fileButtons(j, m, false)}</div>
    </div>`;
  }).join('');
  const profiles = files.filter((f) => f.kind === 'curaprofile');
  return `<div class="summary">${stats.map(([b, s]) => `<div class="stat"><b>${b}</b><span>${s}</span></div>`).join('')}</div>
    ${notes ? `<ul class="notes">${notes}</ul>` : ''}
    <div class="plates">${plateCards}</div>
    ${profiles.length ? `<div class="extra-files"><span class="muted">Profil pre Cura:</span>${profiles.map((f) => `<button class="btn small" data-act="${desktop ? 'save' : 'get'}" data-name="${esc(f.name)}">${esc(f.name.replace(/^.*?_(?=[A-Z]{3,4})/, ''))}</button>`).join('')}</div>` : ''}`;
}

function fileButtons(j, f, primary) {
  if (!f) return '';
  const n = esc(f.name);
  if (desktop) {
    if (f.kind === 'gcode') {
      return `<button class="btn small primary" data-act="sd" data-name="${n}">Na SD kartu</button>
        <button class="btn small" data-act="save" data-name="${n}">Uložiť G-code</button>`;
    }
    return `<button class="btn small" data-act="cura" data-name="${n}">Otvoriť v Cure</button>`;
  }
  return `<button class="btn small ${primary ? 'primary' : ''}" data-act="get" data-name="${n}">${f.kind === 'gcode' ? 'G-code' : '3MF'} <small>${fmtSize(f.size)}</small></button>`;
}

$('#jobs').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const id = b.closest('.job').dataset.id;
  const job = jobs.find((j) => j.id === id);
  const act = b.dataset.act;
  const key = id + act + (b.dataset.name || '');
  if (busy.has(key)) return;
  busy.add(key);
  b.disabled = true;
  try {
    if (act === 'delete') {
      if (!confirm(`Zmazať úlohu „${job.title}“?`)) return;
      await api(`/api/jobs/${id}`, { method: 'DELETE' });
      await loadJobs();
    } else if (act === 'retry') {
      await api(`/api/jobs/${id}/retry`, { method: 'POST', json: { options: job.options } });
      await loadJobs();
    } else if (act === 'folder') {
      await api('/api/desktop/open', { method: 'POST', json: { job: id, how: 'folder' } });
    } else if (act === 'cura') {
      await api('/api/desktop/open', { method: 'POST', json: { job: id, how: 'cura', name: b.dataset.name } });
      toast('Otváram v Cure…');
    } else if (act === 'sd') {
      const r = await api('/api/desktop/to-sd', { method: 'POST', json: { job: id, name: b.dataset.name } });
      toast('Skopírované na ' + r.path);
    } else if (act === 'save') {
      const r = await api('/api/desktop/save-as', { method: 'POST', json: { job: id, name: b.dataset.name } });
      if (r.ok) toast('Uložené: ' + r.path);
    } else if (act === 'get') {
      await download(job, b.dataset.name, b);
    }
  } catch (ex) {
    toast(ex.message, 4000);
  } finally {
    busy.delete(key);
    b.disabled = false;
  }
});

// ---------------------------------------------------------------- sťahovanie (web / mobil)
async function download(job, name, btn) {
  if (desktop) {
    const r = await api(`/api/desktop/file/${job.id}/${encodeURIComponent(name)}`);
    return saveBlob(await r.blob(), name);
  }
  const f = job.result.files.find((x) => x.name === name);
  const parts = [];
  const old = btn.innerHTML;
  for (let i = 0; i < f.chunks; i++) {
    btn.textContent = `${Math.round((i / f.chunks) * 100)} %`;
    const r = await api(`/api/jobs/${job.id}/file/out/${encodeURIComponent(name)}/${i}`);
    parts.push(await r.arrayBuffer());
  }
  btn.innerHTML = old;
  let blob = new Blob(parts);
  if (f.gzip) blob = await new Response(blob.stream().pipeThrough(new DecompressionStream('gzip'))).blob();
  await saveBlob(blob, name);
}

async function saveBlob(blob, name) {
  if (NATIVE) {
    // APK: uloží do cache a otvorí zdieľanie (Quick Share, Gmail, Disk… → do PC)
    const { Filesystem, Share } = window.Capacitor.Plugins;
    const b64 = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.readAsDataURL(blob); });
    const w = await Filesystem.writeFile({ path: name, data: b64, directory: 'CACHE' });
    await Share.share({ title: name, files: [w.uri], dialogTitle: 'Poslať súbor' });
    return;
  }
  const file = new File([blob], name, { type: 'application/octet-stream' });
  if (navigator.canShare && /Android|iPhone|iPad/i.test(navigator.userAgent) && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: name }); return; } catch { /* zrušené – stiahne sa */ }
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}

// ---------------------------------------------------------------- nastavenia (PC)
let settingsCache = null;
async function loadSettings() {
  loadSettings.at = Date.now();
  try { settingsCache = await api('/api/desktop/settings'); } catch { return; }
  setStatus();
  if (!$('#view-settings').hidden) fillSettings();
}

function fillSettings() {
  const s = settingsCache;
  $('#set-cura').innerHTML = s.cura_found ? `✔ ${esc(s.cura_found)}` : `<span class="err">${esc(s.cura_error)}</span>`;
  $('#set-output').value = s.output_dir;
  $('#set-copy').checked = !!s.copy_to_output;
  $('#set-sd').textContent = s.drives.length ? s.drives.map((d) => `${d.root} ${d.label} (voľné ${fmtSize(d.free)})`).join(', ') : 'Žiadna SD karta – vlož kartu do čítačky, tlačidlo „Na SD kartu“ ju nájde samo.';
  if (document.activeElement !== $('#set-url')) $('#set-url').value = s.cloud_url || '';
  $('#set-cloud').checked = !!s.cloud_enabled;
  $('#set-cloud').disabled = !s.cloud_logged_in;
  const cs = s.cloud_status || {};
  $('#set-cloud-status').textContent = !s.cloud_logged_in ? 'Neprihlásené.' : `Stav: ${cs.state || '–'}${cs.detail ? ' – ' + cs.detail : ''}`;
}

function bindSettings() {
  $('#btn-settings').hidden = false;
  $('#btn-settings').addEventListener('click', async () => { show('settings'); await loadSettings(); fillSettings(); });
  $('#btn-back').addEventListener('click', () => show('main'));
  $('#btn-pick').addEventListener('click', async () => {
    const r = await api('/api/desktop/pick-folder', { method: 'POST' });
    if (r.path) { settingsCache = await api('/api/desktop/settings', { method: 'POST', json: { output_dir: r.path } }); fillSettings(); }
  });
  $('#set-copy').addEventListener('change', async (e) => {
    settingsCache = await api('/api/desktop/settings', { method: 'POST', json: { copy_to_output: e.target.checked } });
  });
  $('#set-cloud').addEventListener('change', async (e) => {
    settingsCache = await api('/api/desktop/settings', { method: 'POST', json: { cloud_enabled: e.target.checked } });
    fillSettings(); setStatus();
  });
  $('#btn-cloud-login').addEventListener('click', async () => {
    const err = $('#set-err');
    err.hidden = true;
    try {
      settingsCache = await api('/api/desktop/cloud-login', { method: 'POST', json: { url: $('#set-url').value, password: $('#set-pw').value } });
      $('#set-pw').value = '';
      toast('Prihlásené – PC teraz spracúva aj úlohy z mobilu');
      fillSettings(); setStatus();
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  });
}

// ---------------------------------------------------------------- štart
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = $('#login-err');
  err.hidden = true;
  try {
    const r = await api('/api/login', { method: 'POST', json: { password: $('#login-pw').value } });
    store.set('token', r.token);
    $('#login-pw').value = '';
    show('main');
    loadJobs();
  } catch (ex) { err.textContent = ex.message; err.hidden = false; }
});
$('#btn-refresh').addEventListener('click', loadJobs);
document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('#view-main').hidden) loadJobs(); });

async function start() {
  try {
    info = await api('/api/info');
  } catch (ex) {
    $('#status').textContent = 'offline';
    $('#status').className = 'pill err';
    $('#jobs').innerHTML = `<div class="empty">Server je nedostupný: ${esc(ex.message)}</div>`;
    show('main');
    return;
  }
  desktop = info.mode === 'desktop';
  document.body.classList.toggle('desktop', desktop);
  buildForm();
  if (desktop) { bindSettings(); await loadSettings(); }
  setStatus();
  if (info.auth_required && !store.get('token', '')) { showLogin(); return; }
  show('main');
  loadJobs();
}

if ('serviceWorker' in navigator && !location.search.includes('desktop') && location.protocol === 'https:') {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
start();
