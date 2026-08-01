// script.js
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
let profilePage = 0;
const PROFILE_PAGE_SIZE = 50;
let profileFilter = { platform: '', profile: '', search: '' };
let postsLoaded = false;

function toast(msg) { toastEl.textContent = msg; toastEl.classList.remove('hidden'); setTimeout(() => toastEl.classList.add('hidden'), 3000); }

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

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
  profilePage = 0;
  renderProfiles();
  updateProfileFilter();
  populateCompanyFilter();
}

function filteredProfiles() {
  const { platform, profile, search } = profileFilter;
  const s = search.trim().toLowerCase();
  return allProfiles.filter(p => {
    if (platform && p.platform !== platform) return false;
    if (profile && String(p.id) !== String(profile)) return false;
    if (s && !((p.name + ' ' + p.url + ' ' + p.platform + ' ' + (p.company || '')).toLowerCase().includes(s))) return false;
    return true;
  });
}

function renderProfiles() {
  const wrap = $('profileList'); wrap.innerHTML = '';
  const list = filteredProfiles();
  $('profileCount').textContent = list.length + ' of ' + allProfiles.length + ' profiles';
  if (!list.length) {
    wrap.innerHTML = '<p class="text-[var(--muted)] text-sm col-span-full">No profiles match your filters.</p>';
    return;
  }
  const now = Date.now();
  const shown = list.slice(0, (profilePage + 1) * PROFILE_PAGE_SIZE);
  shown.forEach(p => {
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
        ${p.company ? '<p class="text-xs text-[var(--muted)] mt-0.5">Company: ' + escapeHtml(p.company) + '</p>' : ''}
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
  const remaining = list.length - shown.length;
  if (remaining > 0) {
    const btn = document.createElement('button');
    btn.className = 'text-sm px-4 py-2 rounded-lg border border-[var(--border)] hover:border-[var(--accent)]';
    btn.textContent = `Show ${Math.min(PROFILE_PAGE_SIZE, remaining)} more (${remaining} left)`;
    btn.onclick = () => { profilePage++; renderProfiles(); };
    wrap.appendChild(btn);
  }
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
  tb.innerHTML = '<tr><td colspan="5" class="text-center text-[var(--muted)] py-6">Loading…</td></tr>';
  $('pageInfo').textContent = '';
  $('pagination').innerHTML = '';
  const q = postQueryParams();
  try {
    const data = await api('/api/posts?' + q);
    totalPosts = data.total;
    totalPages = data.total_pages;
    const posts = data.posts;
    const tb = $('postsBody');
    if (!posts.length) { tb.innerHTML = '<tr><td colspan="5" class="text-center text-[var(--muted)] py-6">No posts found.</td></tr>'; }
    else {
      tb.innerHTML = posts.map(p => {
        const c = PLATFORM_COLORS[p.platform] || '#5b8cff';
        const title = p.title || '(no title)';
        return `<tr>
        <td><span class="inline-flex items-center gap-1.5 font-semibold"><span class="w-2 h-2 rounded-full" style="background:${c}"></span>${p.platform}</span></td>
        <td>${p.profile_name || p.profile_id}</td>
        <td>${escapeHtml(p.profile_company || '—')}</td>
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
    tb.innerHTML = '<tr><td colspan="5" class="text-center text-[var(--red)] py-6">Error loading posts: ' + e.message + '</td></tr>';
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

// Filter event listeners - reload immediately on any change
$('fPlatform').addEventListener('change', () => { currentPage = 1; loadPosts(); });
$('fProfile').addEventListener('change', () => { currentPage = 1; loadPosts(); });
$('fSearch').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentPage = 1; loadPosts(); }, 400);
});
$('fromDate').addEventListener('change', () => { currentPage = 1; loadPosts(); });
$('toDate').addEventListener('change', () => { currentPage = 1; loadPosts(); });

$('clearFilters').onclick = () => {
  $('fPlatform').value = ''; $('fProfile').value = '';
  $('fSearch').value = ''; $('fromDate').value = ''; $('toDate').value = '';
  currentPage = 1;
  loadPosts();
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
    const company = $('mCompany').value.trim();
    const res = await api('/api/profiles', { method: 'POST', body: JSON.stringify({ platform, name, url, company }) });
    $('mName').value = ''; $('mUrl').value = ''; $('mCompany').value = '';
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
 * Scan Progress dashboard modal
 *
 * Server-driven model: POST /api/scan/run triggers the backend
 * sequential loop. The client mirrors the backend via polling
 * /api/scan/progress and renders per-account status badges.
 *
 * scanState mirrors the backend snapshot. Helper functions:
 *   startScan    — trigger scan + open modal + begin polling
 *   scanNext     — one poll cycle (fetch progress, render, check done)
 *   cancelScan   — request graceful cancel, wait, close, toast
 *   resumeScan   — re-trigger scan (resumes remaining accounts)
 *   persistState — sync scanState from server payload
 *   renderModalState — render scanState into the modal DOM
 * ============================================================ */

const scanProgressModal = $('scanProgressModal');
let scanProgressPoll = null;

// Client-side mirror of the backend scan_state snapshot.
const scanState = {
  running: false,
  session_id: null,
  created_at: null,
  profiles: [],   // [{profile_id, name, platform, company, status, new_posts, followers, error}]
};

// ── Pre-scan company filter ────────────────────────────────────
// Populate the company dropdown from loaded profiles (All + one per company).
function populateCompanyFilter() {
  const sel = $('scanCompanyFilter');
  if (!sel) return;
  const current = sel.value;
  const companies = [...new Set(allProfiles.map(p => p.company).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">All Companies</option>';
  companies.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    sel.appendChild(opt);
  });
  if (current) sel.value = current;
}

// Return profile IDs matching the selected company filter.
function getFilteredProfileIds() {
  const company = $('scanCompanyFilter').value;
  if (!company) return null;  // null = all profiles
  return allProfiles.filter(p => p.company === company).map(p => p.id);
}

// ── persistState: sync scanState from server payload ───────────
function persistState(data) {
  scanState.running = !!data.running;
  scanState.session_id = data.session_id || null;
  scanState.created_at = data.created_at || null;
  scanState.profiles = (data.profiles || []).map(p => ({
    profile_id: p.profile_id,
    name: p.name,
    platform: p.platform,
    company: p.company || '',
    status: p.status,
    new_posts: p.new_posts || 0,
    followers: p.followers,
    error: p.error,
  }));
}

// ── renderModalState: render scanState into modal DOM ───────────
const STATUS_META = {
  pending: { label: 'Queue', cls: 'ps-badge-queue' },
  scanning: { label: 'Ongoing', cls: 'ps-badge-ongoing' },
  completed: { label: 'Done', cls: 'ps-badge-done' },
  failed: { label: 'Failed', cls: 'ps-badge-failed' },
  cancelled: { label: 'Cancelled', cls: 'ps-badge-cancelled' },
};

function renderModalState() {
  const list = $('scanProfileList');
  const profs = scanState.profiles;
  const total = profs.length;

  // Count completed for progress bar + count text.
  const done = profs.filter(p => p.status === 'completed').length;
  const pct = total ? Math.round((done / total) * 100) : 0;

  $('scanProgressBar').style.width = pct + '%';
  $('scanProgressPct').textContent = pct + '%';
  $('scanProgressCount').textContent = total ? `Scanning ${done}/${total}` : 'Starting…';

  // Render per-account rows with badges.
  list.innerHTML = profs.map(p => {
    const meta = STATUS_META[p.status] || STATUS_META.pending;
    const platformColor = PLATFORM_COLORS[p.platform] || '#5b8cff';
    const sub = p.status === 'completed'
      ? `${p.new_posts} new post(s)`
      : p.status === 'failed'
        ? escapeHtml(p.error || 'error')
        : p.status === 'cancelled'
          ? 'skipped'
          : '';
    return `<div class="ps-item">
      <span class="inline-grid place-items-center w-7 h-7 rounded font-bold text-xs flex-shrink-0"
            style="background:${platformColor};color:#0b0f1a">${p.platform[0].toUpperCase()}</span>
      <span class="ps-name">
        ${escapeHtml(p.name)}
        ${p.company ? '<span class="text-xs text-[var(--muted)] ml-1">(' + escapeHtml(p.company) + ')</span>' : ''}
        ${sub ? '<span class="ps-meta ml-2">' + sub + '</span>' : ''}
      </span>
      <span class="ps-badge ${meta.cls}">${meta.label}</span>
    </div>`;
  }).join('');

  // Auto-scroll to the currently-scanning profile.
  const ongoingIdx = profs.findIndex(p => p.status === 'scanning');
  if (ongoingIdx >= 0) {
    const items = list.querySelectorAll('.ps-item');
    if (items[ongoingIdx]) items[ongoingIdx].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

// ── openScanProgress / closeScanProgress ────────────────────────
function openScanProgress() {
  $('scanProgressTitle').textContent = 'Scan in progress…';
  const spin = $('scanProgressSpinner'); if (spin) spin.classList.remove('hidden');
  $('scanProgressFooter').classList.add('hidden');
  $('scanProgressModal').classList.remove('ps-cancelling');
  $('scanProfileList').innerHTML = '<div class="text-[var(--muted)] text-sm p-4">Initializing scan…</div>';
  scanProgressModal.classList.remove('hidden');
  scanProgressModal.classList.add('flex');
}

function closeScanProgress() {
  scanProgressModal.classList.add('hidden');
  scanProgressModal.classList.remove('flex');
  scanProgressModal.classList.remove('ps-cancelling');
}

// ── Modal lock: during scan, block backdrop / Escape / X ────────
// While scanning, the modal must NOT close via backdrop click or
// Escape. Only the explicit Cancel button can dismiss it mid-scan.
scanProgressModal.addEventListener('click', e => {
  if (scanState.running && e.target === scanProgressModal) return;  // locked
  if (!scanState.running && e.target === scanProgressModal) closeScanProgress();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !scanProgressModal.classList.contains('hidden')) {
    if (scanState.running) return;  // locked during scan
    closeScanProgress();
  }
});

// ── beforeunload: warn user during active scan ─────────────────
window.addEventListener('beforeunload', e => {
  if (scanState.running) {
    e.preventDefault();
    e.returnValue = 'A scan is in progress. Leaving will interrupt it. You can resume after returning.';
    return e.returnValue;
  }
});

// ── scanNext: one poll cycle ───────────────────────────────────
function scanNext(scanStartedAt, onComplete) {
  return async () => {
    try {
      const data = await api('/api/scan/progress');
      persistState(data);          // mirror backend into scanState
      renderModalState();          // re-render badges + bar
    } catch (e) {
      console.error('progress fetch error', e);
    }

    // Check if the scan finished (running flipped to false).
    try {
      const st = await api('/api/scan/status');
      if (!st.running) {
        clearInterval(scanProgressPoll); scanProgressPoll = null;
        if (onComplete) onComplete();
      }
    } catch (e) {
      console.error('status fetch error', e);
    }
  };
}

// ── startScan: trigger scan + open modal + begin polling ───────
async function startScan(profileIds, fresh = false) {
  if (scanState.running) return;
  scanState.running = true;

  openScanProgress();

  const scanStartedAt = Date.now();
  $('runBtn').innerHTML = '<span class="spinner"></span> Running…';
  $('runBtn').disabled = true;
  $('statusBadge').className = 'badge busy'; $('statusBadge').textContent = '● Running';

  try {
    await api('/api/scan/run', {
      method: 'POST',
      body: JSON.stringify({ profile_ids: profileIds, fresh }),
    });
    toast('Scan started');
  } catch (e) {
    toast('Failed: ' + e.message);
    closeScanProgress();
    scanState.running = false;
    resetRunButton();
    return;
  }

  // Poll every 1.5s for live status updates.
  scanProgressPoll = setInterval(
    scanNext(scanStartedAt, () => onScanComplete(scanStartedAt)),
    1500
  );
}

// ── onScanComplete: show summary, unlock modal ─────────────────
async function onScanComplete(scanStartedAt) {
  // Final render with completed state.
  try {
    const data = await api('/api/scan/progress');
    persistState(data);
    renderModalState();
  } catch (e) { /* ignore */ }

  scanState.running = false;
  resetRunButton();
  $('scanProgressSpinner').classList.add('hidden');

  // Show summary footer.
  const profs = scanState.profiles;
  const done = profs.filter(p => p.status === 'completed').length;
  const failed = profs.filter(p => p.status === 'failed').length;
  const cancelled = profs.filter(p => p.status === 'cancelled').length;
  const newPosts = profs.reduce((s, p) => s + (p.new_posts || 0), 0);

  let summary = `${done}/${profs.length} done`;
  if (failed) summary += `, ${failed} failed`;
  if (cancelled) summary += `, ${cancelled} cancelled`;
  summary += ` — ${newPosts} new post(s)`;
  $('scanProgressSummary').textContent = summary;

  const allDone = done === profs.length;
  $('scanProgressTitle').textContent = allDone ? 'Scan complete' : 'Scan stopped';
  $('scanProgressFooter').classList.remove('hidden');

  // Refresh dashboard data so new posts appear in the table.
  await loadAll();
}

function resetRunButton() {
  $('runBtn').innerHTML = '▶ Run Scan';
  $('runBtn').disabled = false;
  $('statusBadge').className = 'badge live'; $('statusBadge').textContent = '● Idle';
}

// ── cancelScan: graceful cancel, wait, close, toast ────────────
async function cancelScan() {
  if (!scanState.running) return;

  // Request graceful cancel (stops after current profile finishes).
  try { await api('/api/scan/cancel', { method: 'POST' }); } catch (e) { /* ignore */ }

  // Show cancelling state — modal stays locked until backend confirms.
  $('scanProgressTitle').textContent = 'Cancelling after current account…';
  scanProgressModal.classList.add('ps-cancelling');
  $('scanProgressCancel').disabled = true;

  // Keep polling until running=false (current profile finishes).
  const waitCancel = setInterval(async () => {
    try {
      const st = await api('/api/scan/status');
      if (!st.running) {
        clearInterval(waitCancel);
        scanState.running = false;
        resetRunButton();
        closeScanProgress();
        toast('Scan paused — click Run Scan to resume remaining accounts');
        await loadAll();
      } else {
        // Still scanning the current profile; update badges.
        const data = await api('/api/scan/progress');
        persistState(data);
        renderModalState();
      }
    } catch (e) { /* keep waiting */ }
  }, 1500);
}

// ── resumeScan: re-trigger scan, resumes remaining accounts ─────
async function resumeScan() {
  // Passing the same profile_ids + fresh=false lets init_progress
  // match the prior session by profile_id and skip completed ones.
  await startScan(getFilteredProfileIds(), false);
}

// ── Run Scan button ────────────────────────────────────────────
$('runBtn').onclick = async () => {
  if (scanState.running) return;
  await startScan(getFilteredProfileIds(), false);
};

// ── Cancel button ──────────────────────────────────────────────
$('scanProgressCancel').onclick = cancelScan;

// ── Close button (only enabled when scan is done) ──────────────
$('scanProgressClose').onclick = closeScanProgress;

// ── Re-scan All button (forces fresh full scan) ─────────────────
$('scanProgressRescan').onclick = async () => {
  closeScanProgress();
  await startScan(null, true);  // null = all profiles, fresh=true
};

// ── On app load: check for incomplete session, offer resume ────
async function checkResumeOnLoad() {
  try {
    const sess = await api('/api/scan/session');
    if (!sess || !sess.session_id) return;

    if (sess.running) {
      // Scan is in progress (e.g. user refreshed the page mid-scan).
      // Reattach: open modal and resume polling.
      scanState.running = true;
      openScanProgress();
      $('runBtn').innerHTML = '<span class="spinner"></span> Running…';
      $('runBtn').disabled = true;
      $('statusBadge').className = 'badge busy'; $('statusBadge').textContent = '● Running';
      scanProgressPoll = setInterval(
        scanNext(Date.now(), () => onScanComplete(Date.now())),
        1500
      );
      return;
    }

    if (sess.has_incomplete) {
      // Previous scan was interrupted (cancel/crash). Offer resume.
      const remaining = sess.total - sess.completed;
      toast(`Previous scan incomplete (${remaining} remaining). Click Run Scan to resume.`);
      // Change button label to indicate resume is available.
      $('runBtn').innerHTML = '▶ Run Scan (Resume)';
    }
  } catch (e) {
    console.error('resume check error', e);
  }
}

loadAll().then(checkResumeOnLoad);