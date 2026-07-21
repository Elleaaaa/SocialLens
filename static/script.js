const PLATFORM_COLORS = { youtube: '#FF0000', facebook: '#1877F2', instagram: '#E1306C', tiktok: '#00f2ea', x: '#1DA1F2' };
const $ = id => document.getElementById(id);
const toastEl = $('toast');
let scanning = false;
let currentPage = 1;
let totalPages = 1;
let totalPosts = 0;
let sortBy = 'published_at';
let sortDir = 'desc';
let allProfiles = [];
let searchTimer = null;
let postsLoaded = false;
let scanStartedAt = null;
let progressTimer = null;

function toast(msg) { toastEl.textContent = msg; toastEl.classList.remove('hidden'); setTimeout(() => toastEl.classList.add('hidden'), 3000); }

function timeAgo(iso) {
  if (!iso) return 'never';
  let s = iso;
  if (s.indexOf('T') < 0 && s.indexOf(' ') >= 0) s = s.replace(' ', 'T');
  if (!s.endsWith('Z')) s += 'Z';
  const d = new Date(s);
  if (isNaN(d)) return iso;
  const diff = Math.floor((Date.now() - d) / 1000);
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function fmtNum(n) {
  n = Number(n) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}

function fmtDate(iso) {
  if (!iso) return 'unknown';
  const d = new Date(iso);
  if (isNaN(d)) return iso.split('T')[0];
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function postQueryParams() {
  const p = new URLSearchParams();
  const f = $('fromDate').value, t = $('toDate').value;
  const platform = $('fPlatform').value, profile = $('fProfile').value;
  const search = $('fSearch').value.trim();
  if (f) p.set('from_date', f);
  if (t) p.set('to_date', t);
  if (platform) p.set('platform', platform);
  if (profile) p.set('profile_id', profile);
  if (search) p.set('search', search);
  p.set('sort_by', sortBy);
  p.set('sort_dir', sortDir);
  p.set('page', currentPage);
  p.set('per_page', 20);
  return p.toString();
}

function statsQueryParams() {
  const p = new URLSearchParams();
  const f = $('fromDate').value, t = $('toDate').value;
  if (f) p.set('from_date', f);
  if (t) p.set('to_date', t);
  return p.toString();
}

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  const r = await fetch(path, { ...opts, headers });
  if (!r.ok) {
    let detail = '';
    try { detail = (await r.json()).detail || await r.text(); } catch (e) { detail = await r.text(); }
    throw new Error('HTTP ' + r.status + (detail ? ': ' + detail : ''));
  }
  return r.json();
}

async function loadStats() {
  const q = statsQueryParams();
  const s = await api('/api/stats' + (q ? '?' + q : ''));
  $('kTotal').textContent = fmtNum(s.total_posts);
  $('kFollowers').textContent = fmtNum(s.total_followers);
  $('lastRun').textContent = s.last_run ? ('Last sync: ' + timeAgo(s.last_run)) : 'Last sync: never';
  const f = $('fromDate').value, t = $('toDate').value;
  let label = 'all time';
  if (f && t) label = `${f} to ${t}`;
  else if (f) label = `from ${f}`;
  else if (t) label = `up to ${t}`;
  $('kTotalRange').textContent = label;
}

async function loadProfiles() {
  const q = statsQueryParams();
  const profiles = await api('/api/profiles' + (q ? '?' + q : ''));
  allProfiles = profiles;
  const platforms = new Set(profiles.map(p => p.platform));
  $('kAccounts').textContent = profiles.length;
  $('kPlatforms').textContent = platforms.size;
  $('profileCount').textContent = profiles.length + ' profiles';
  const wrap = $('profileList'); wrap.innerHTML = '';
  if (!profiles.length) { wrap.innerHTML = '<p class="text-[var(--muted)] text-sm col-span-full">No profiles yet. Click + Add Profile.</p>'; return; }
  const now = Date.now();
  profiles.forEach(p => {
    const c = PLATFORM_COLORS[p.platform] || '#5b8cff';
    const card = document.createElement('article');
    card.className = 'card rounded-xl p-4 flex gap-3 items-start';
    const lastRunMs = p.last_run ? new Date(p.last_run.replace(' ', 'T') + 'Z').getTime() : 0;
    const isNew = (now - lastRunMs) < 86400000 && p.total_posts > 0;
    card.innerHTML = `
      <div class="grid place-items-center w-10 h-10 rounded-lg font-bold flex-shrink-0" style="background:${c};color:#0b0f1a">${p.platform[0].toUpperCase()}</div>
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2">
          <span class="font-semibold text-sm">${p.name}</span>
          ${isNew ? '<span class="new-badge">NEW</span>' : ''}
        </div>
        <a href="${p.url}" target="_blank" class="text-xs text-[var(--muted)] truncate block hover:text-[var(--accent)]">${p.url}</a>
        <div class="flex gap-4 mt-2 text-xs">
          <span><b class="text-sm">${fmtNum(p.total_posts)}</b> <span class="text-[var(--muted)]">posts</span></span>
          <span><b class="text-sm">${fmtNum(p.followers)}</b> <span class="text-[var(--muted)]">${p.metric_label}</span></span>
        </div>
        <p class="text-xs text-[var(--muted)] mt-1">Last run: ${timeAgo(p.last_run)}</p>
      </div>
      <button class="del text-xs text-[var(--red)] hover:underline" data-id="${p.id}">✕</button>
    `;
    wrap.appendChild(card);
  });
  wrap.querySelectorAll('.del').forEach(b => b.onclick = async () => {
    try { await api('/api/profiles/' + b.dataset.id, { method: 'DELETE' }); toast('Profile removed'); loadAll(); }
    catch (e) { toast('Error: ' + e.message); }
  });
  updateProfileFilter();
}

function updateProfileFilter() {
  const sel = $('fProfile');
  const current = sel.value;
  sel.innerHTML = '<option value="">All Accounts</option>';
  allProfiles.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.name;
    sel.appendChild(opt);
  });
  if (current) sel.value = current;
}

async function loadPlatformFilter() {
  try {
    const platforms = await api('/api/platforms');
    const sel = $('fPlatform');
    const current = sel.value;
    sel.innerHTML = '<option value="">All Platforms</option>';
    platforms.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p;
      opt.textContent = p.charAt(0).toUpperCase() + p.slice(1);
      sel.appendChild(opt);
    });
    if (current) sel.value = current;
  } catch (e) { console.error(e); }
}

async function loadPosts() {
  postsLoaded = true;
  const tb = $('postsBody');
  tb.innerHTML = '<tr><td colspan="4" class="text-center text-[var(--muted)] py-6">Loading…</td></tr>';
  $('pageInfo').textContent = '';
  $('pagination').innerHTML = '';
  const q = postQueryParams();
  try {
    const data = await api('/api/posts?' + q);
    totalPosts = data.total;
    totalPages = data.total_pages;
    const posts = data.posts;
    const tb = $('postsBody');
    if (!posts.length) { tb.innerHTML = '<tr><td colspan="4" class="text-center text-[var(--muted)] py-6">No posts found.</td></tr>'; }
    else {
      tb.innerHTML = posts.map(p => {
        const c = PLATFORM_COLORS[p.platform] || '#5b8cff';
        const title = p.title || '(no title)';
        return `<tr>
        <td><span class="inline-flex items-center gap-1.5 font-semibold"><span class="w-2 h-2 rounded-full" style="background:${c}"></span>${p.platform}</span></td>
        <td>${p.profile_name || p.profile_id}</td>
        <td><a href="${p.url || ('https://www.instagram.com/p/' + p.post_id + '/')}" target="_blank" class="text-[var(--accent)] hover:underline" style="max-width:250px;display:inline-block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom">${title}</a></td>
        <td class="text-[var(--muted)]">${fmtDate(p.published_at)}</td>
      </tr>`;
      }).join('');
    }
    const start = totalPosts > 0 ? (currentPage - 1) * 20 + 1 : 0;
    const end = Math.min(currentPage * 20, totalPosts);
    $('pageInfo').textContent = `Showing ${start}-${end} of ${totalPosts}`;
    renderPagination();
  } catch (e) {
    console.error(e);
    tb.innerHTML = '<tr><td colspan="4" class="text-center text-[var(--red)] py-6">Error loading posts: ' + e.message + '</td></tr>';
  }
}

function renderPagination() {
  const wrap = $('pagination');
  wrap.innerHTML = '';
  if (totalPages <= 1) return;
  const prev = document.createElement('button');
  prev.className = 'page-btn'; prev.textContent = '‹'; prev.disabled = currentPage <= 1;
  prev.onclick = () => { if (currentPage > 1) { currentPage--; loadPosts(); } };
  wrap.appendChild(prev);

  const maxPages = 7;
  let startP = Math.max(1, currentPage - 3);
  let endP = Math.min(totalPages, startP + maxPages - 1);
  if (endP - startP < maxPages - 1) startP = Math.max(1, endP - maxPages + 1);

  if (startP > 1) {
    const b = document.createElement('button');
    b.className = 'page-btn'; b.textContent = '1'; b.onclick = () => { currentPage = 1; loadPosts(); };
    wrap.appendChild(b);
    if (startP > 2) { const d = document.createElement('span'); d.className = 'text-[var(--muted)] px-1'; d.textContent = '…'; wrap.appendChild(d); }
  }
  for (let i = startP; i <= endP; i++) {
    const b = document.createElement('button');
    b.className = 'page-btn' + (i === currentPage ? ' active' : '');
    b.textContent = i; b.onclick = () => { currentPage = i; loadPosts(); };
    wrap.appendChild(b);
  }
  if (endP < totalPages) {
    if (endP < totalPages - 1) { const d = document.createElement('span'); d.className = 'text-[var(--muted)] px-1'; d.textContent = '…'; wrap.appendChild(d); }
    const b = document.createElement('button');
    b.className = 'page-btn'; b.textContent = totalPages; b.onclick = () => { currentPage = totalPages; loadPosts(); };
    wrap.appendChild(b);
  }
  const next = document.createElement('button');
  next.className = 'page-btn'; next.textContent = '›'; next.disabled = currentPage >= totalPages;
  next.onclick = () => { if (currentPage < totalPages) { currentPage++; loadPosts(); } };
  wrap.appendChild(next);
}

async function loadAll() {
  try {
    await Promise.all([loadStats(), loadProfiles(), loadPlatformFilter()]);
    if (postsLoaded) loadPosts();
  }
  catch (e) { console.error(e); toast('Load error: ' + e.message); }
}

// Filter event listeners - only reload if posts already loaded
$('fPlatform').addEventListener('change', () => { if (postsLoaded) { currentPage = 1; loadPosts(); } });
$('fProfile').addEventListener('change', () => { if (postsLoaded) { currentPage = 1; loadPosts(); } });
$('fSearch').addEventListener('input', () => {
  if (!postsLoaded) return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentPage = 1; loadPosts(); }, 400);
});
$('fromDate').addEventListener('change', () => { if (postsLoaded) { currentPage = 1; loadPosts(); } });
$('toDate').addEventListener('change', () => { if (postsLoaded) { currentPage = 1; loadPosts(); } });
$('clearFilters').onclick = () => {
  $('fPlatform').value = ''; $('fProfile').value = '';
  $('fSearch').value = ''; $('fromDate').value = ''; $('toDate').value = '';
  currentPage = 1;
  if (postsLoaded) loadPosts();
};
$('loadPostsBtn').onclick = () => { currentPage = 1; loadPosts(); };

// Sortable columns
document.querySelectorAll('th[data-sort]').forEach(th => {
  th.onclick = () => {
    const col = th.dataset.sort;
    if (sortBy === col) {
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      sortBy = col;
      sortDir = th.dataset.dir || 'asc';
    }
    document.querySelectorAll('th .sort-arrow').forEach(s => s.textContent = '');
    th.querySelector('.sort-arrow').textContent = sortDir === 'asc' ? '▲' : '▼';
    loadPosts();
  };
});

// Add Profile Modal
const modal = $('modal');
function openModal() { modal.classList.remove('hidden'); modal.classList.add('flex'); }
function closeModal() { modal.classList.add('hidden'); modal.classList.remove('flex'); }
$('addBtn').onclick = openModal;
$('mCancel').onclick = closeModal;
modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });

$('mSave').onclick = async () => {
  const platform = $('mPlatform').value;
  const name = $('mName').value.trim();
  const url = $('mUrl').value.trim();
  if (!name || !url) { toast('Name and URL required'); return; }
  const btn = $('mSave');
  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const res = await api('/api/profiles', { method: 'POST', body: JSON.stringify({ platform, name, url }) });
    $('mName').value = ''; $('mUrl').value = '';
    closeModal();
    toast('Profile connected' + (res.id ? ' (' + res.id + ')' : ''));
    loadAll();
  } catch (e) {
    console.error(e);
    toast('Error: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Connect';
  }
};

// CSV Export (server-side, full filtered set)
$('exportBtn').onclick = () => {
  const q = postQueryParams().replace('page=' + currentPage, 'page=1').replace('per_page=20', 'per_page=10000');
  window.open('/api/posts/export?' + q, '_blank');
};

// Refresh button
$('refreshBtn').onclick = () => { loadAll(); toast('Data refreshed'); };

/* ============================================================
 * Scan Results summary modal
 * ============================================================ */
const scanModal = $('scanModal');
let scanBaseline = null;
let scanResults = [];

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function postKey(p) {
  return p.post_id || p.url || p.title || '';
}

function toPostArray(data) {
  if (Array.isArray(data)) return data;
  return (data && data.posts) ? data.posts : [];
}

async function captureBaseline() {
  const data = await api('/api/posts?per_page=10000');
  scanBaseline = new Set(toPostArray(data).map(postKey));
}

async function showScanResults() {
  try {
    const data = await api('/api/posts?per_page=10000');
    const fresh = toPostArray(data);
    const base = scanBaseline || new Set();
    const newPosts = fresh.filter(p => !base.has(postKey(p)));
    openScanResults(newPosts, scanStartedAt);
  } catch (e) {
    console.error('scan results error', e);
    toast('Could not load scan results: ' + e.message);
  }
}

function openScanResults(newPosts, runTime) {
  scanResults = newPosts;
  const count = newPosts.length;
  $('scanNewBadge').textContent = count + ' new';
  $('scanTime').textContent = runTime ? new Date(runTime).toLocaleString() : '—';

  const exportBtn = $('scanExport');
  if (!count) {
    $('scanEmpty').classList.remove('hidden');
    $('scanTableWrap').classList.add('hidden');
    exportBtn.disabled = true;
    exportBtn.classList.add('btn-disabled');
  } else {
    $('scanEmpty').classList.add('hidden');
    $('scanTableWrap').classList.remove('hidden');
    exportBtn.disabled = false;
    exportBtn.classList.remove('btn-disabled');
    $('scanBody').innerHTML = newPosts.map(p => `
      <tr>
        <td>${escapeHtml(p.title || '(untitled)')}</td>
        <td>${escapeHtml(p.profile_name || p.platform || '—')}</td>
        <td>${fmtDateTime(p.detected_at)}</td>
        <td><a href="${escapeHtml(p.url || '#')}" target="_blank"
               class="text-[var(--accent)] hover:underline">Open</a></td>
        <td><span class="badge live">New</span></td>
      </tr>`).join('');
  }
  scanModal.classList.remove('hidden');
  scanModal.classList.add('flex');
}

function closeScanResults() {
  scanModal.classList.add('hidden');
  scanModal.classList.remove('flex');
}

function csvCell(v) {
  const s = String(v == null ? '' : v);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function exportScanCSV() {
  if (!scanResults.length) return;
  const headers = ['Post Title', 'Author/Source', 'Date Detected', 'URL/Link', 'Status'];
  const rows = scanResults.map(p => [
    p.title || '(untitled)',
    p.profile_name || p.platform || '',
    p.detected_at || '',
    p.url || '',
    'New'
  ]);
  const csv = [headers, ...rows].map(r => r.map(csvCell).join(',')).join('\r\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
  const d = new Date(), pad = n => String(n).padStart(2, '0');
  const fname = `scan_results_${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}.csv`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = fname;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('Exported ' + scanResults.length + ' new post(s)');
}

$('scanClose').onclick = closeScanResults;
$('scanDone').onclick = closeScanResults;
$('scanExport').onclick = exportScanCSV;
scanModal.addEventListener('click', e => { if (e.target === scanModal) closeScanResults(); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !scanModal.classList.contains('hidden')) closeScanResults();
});

/* ============================================================
 * Scan progress dashboard (polls /api/scan/progress)
 * ============================================================ */
const scanProgressModal = $('scanProgressModal');

function openScanProgress() {
  renderScanProgress({ running: true, profiles: [] });
  scanProgressModal.classList.remove('hidden');
  scanProgressModal.classList.add('flex');
  startProgressPoll();
}

function closeScanProgress() {
  scanProgressModal.classList.add('hidden');
  scanProgressModal.classList.remove('flex');
  stopProgressPoll();
}

function startProgressPoll() {
  stopProgressPoll();
  fetchProgress();
  progressTimer = setInterval(fetchProgress, 1500);
}

function stopProgressPoll() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
}

async function fetchProgress() {
  try {
    const p = await api('/api/scan/progress');
    renderScanProgress(p);
    // Scan finished: close dashboard, show results.
    if (!p.running && p.profiles.length > 0 && scanning) {
      stopProgressPoll();
      scanning = false;
      $('runBtn').innerHTML = '▶ Run Scan';
      $('runBtn').disabled = false;
      $('statusBadge').className = 'badge live';
      $('statusBadge').textContent = '● Idle';
      setTimeout(async () => {
        closeScanProgress();
        await loadAll();
        await showScanResults();
      }, 900);
    }
  } catch (e) { /* ignore transient errors */ }
}

function renderScanProgress(p) {
  const profiles = p.profiles || [];
  const total = profiles.length;
  const done = profiles.filter(x => ['completed', 'failed', 'cancelled'].includes(x.status)).length;
  const scanningNow = profiles.filter(x => x.status === 'scanning').length;

  $('scanProgressTitle').textContent = p.running ? 'Scan in progress…' : 'Scan complete';
  $('scanProgressSpinner').style.display = p.running ? 'inline-block' : 'none';

  if (!total) {
    $('scanProgressCount').textContent = 'Starting…';
    $('scanProgressPct').textContent = '0%';
    $('scanProgressBar').style.width = '0%';
    $('scanProfileList').innerHTML = '';
    return;
  }

  $('scanProgressCount').textContent = `${done} of ${total} complete${scanningNow ? ' · scanning ' + scanningNow : ''}`;
  const pct = Math.round((done / total) * 100);
  $('scanProgressPct').textContent = pct + '%';
  $('scanProgressBar').style.width = pct + '%';

  $('scanProfileList').innerHTML = profiles.map(renderProfileRow).join('');
}

function renderProfileRow(p) {
  const c = PLATFORM_COLORS[p.platform] || '#5b8cff';
  let badge = '';
  if (p.status === 'scanning') {
    badge = '<span class="ps-badge ps-scanning"><span class="spinner"></span> Scanning</span>';
  } else if (p.status === 'completed') {
    badge = `<span class="ps-badge ps-completed">✓ ${p.new_posts} new</span>`;
  } else if (p.status === 'failed') {
    badge = '<span class="ps-badge ps-failed">✕ Failed</span>';
  } else if (p.status === 'cancelled') {
    badge = '<span class="ps-badge ps-cancelled">Cancelled</span>';
  } else {
    badge = '<span class="ps-badge ps-pending">Queued</span>';
  }
  const err = (p.status === 'failed' && p.error)
    ? `<span class="ps-error">${escapeHtml(p.error)}</span>` : '';
  return `
    <div class="ps-row ps-${p.status}">
      <span class="ps-dot" style="background:${c}"></span>
      <div class="ps-info">
        <span class="ps-name">${escapeHtml(p.name)}</span>
        <span class="ps-platform">${escapeHtml(p.platform)}</span>
        ${err}
      </div>
      ${badge}
    </div>`;
}

function cancelScan() {
  api('/api/scan/cancel', { method: 'POST' })
    .then(() => toast('Cancelling… (stops after current profile)'))
    .catch(() => toast('Cancel failed'));
}

$('scanProgressCancel').onclick = cancelScan;

/* ============================================================
 * Run Scan — opens the dashboard, captures baseline, triggers
 * the scan, and lets the progress poll drive completion.
 * ============================================================ */
$('runBtn').onclick = async () => {
  if (scanning) return;
  scanning = true;
  scanStartedAt = Date.now();
  try { await captureBaseline(); } catch (e) { scanBaseline = null; }
  $('runBtn').innerHTML = '<span class="spinner"></span> Running…';
  $('runBtn').disabled = true;
  $('statusBadge').className = 'badge busy';
  $('statusBadge').textContent = '● Running';
  openScanProgress();
  try { await api('/api/scan/run', { method: 'POST' }); }
  catch (e) { toast('Failed: ' + e.message); closeScanProgress(); scanning = false; }
};

loadAll();
setInterval(loadAll, 30000);