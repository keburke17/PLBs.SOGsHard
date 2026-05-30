/* ===== DATA & STATE ===== */
let APP = null; // loaded from app_data.json
let careerShowAll = false;
let careerSortKey = 'career_pts';
let careerSortDir = -1;
let seasonSortKey = null;
let seasonSortDir = -1;
let selectedSeasonIdx = 0;
let expandedPlayers = new Set();

/* ===== HELPERS ===== */
const extLink = (url, label) =>
  `<a class="source-link" href="${url}" target="_blank" rel="noopener noreferrer">
    ${label}
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M5 1H1v10h10V7M7 1h4v4M11 1L5 7"/>
    </svg>
  </a>`;

const pct = (w, gp) => gp > 0 ? ((w / gp) * 100).toFixed(0) + '%' : '—';
const plusMinus = (gf, ga) => {
  const d = (gf || 0) - (ga || 0);
  return d >= 0 ? `+${d}` : `${d}`;
};
const safeInt = v => parseInt(v) || 0;
const fmt = v => v != null && v !== '' ? v : '—';

function recordStr(s) {
  const r = s.record;
  const parts = [r.w, r.l];
  if (r.otl > 0 || r.sol > 0) parts.push(r.otl, r.sol);
  return parts.join('-');
}

function gameResultClass(game) {
  if (!game.result) return 'game-pending';
  if (game.result === 'W') return 'game-win';
  if (game.result === 'L') return 'game-loss';
  return 'game-ot';
}

function resultBadge(game) {
  if (!game.result || game.result_type === 'pending') return '<span style="color:#aaa">—</span>';
  const type = game.result_type === 'REG' ? '' : ` <small>${game.result_type}</small>`;
  const cls = game.result === 'W' ? 'badge-w' : game.result === 'L' ? 'badge-l' : 'badge-ot';
  return `<span class="badge ${cls}">${game.result}${type}</span>${game.is_playoff ? '<span class="badge badge-playoff">PO</span>' : ''}`;
}

function sortBy(arr, key, dir) {
  return [...arr].sort((a, b) => {
    let av = a[key], bv = b[key];
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    if (av == null) return 1;
    if (bv == null) return -1;
    return (av < bv ? -1 : av > bv ? 1 : 0) * dir;
  });
}

/* ===== SECTION: CHART ===== */
let chartInstance = null;

function renderChart() {
  const seasons = APP.seasons;
  const labels = seasons.map(s => s.season.replace('Winter ', 'W').replace('Summer ', 'Su'));
  const pts = seasons.map(s => s.record.pts);
  const wins = seasons.map(s => s.record.w);

  const ctx = document.getElementById('season-chart').getContext('2d');
  if (chartInstance) chartInstance.destroy();

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Points',
          data: pts,
          borderColor: '#c8a84b',
          backgroundColor: 'rgba(200,168,75,0.15)',
          borderWidth: 2.5,
          pointBackgroundColor: '#c8a84b',
          pointRadius: 5,
          tension: 0.3,
          fill: true,
          yAxisID: 'y',
        },
        {
          label: 'Wins',
          data: wins,
          borderColor: '#2a6090',
          backgroundColor: 'transparent',
          borderWidth: 2,
          borderDash: [4, 3],
          pointBackgroundColor: '#2a6090',
          pointRadius: 4,
          tension: 0.3,
          yAxisID: 'y',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', labels: { color: '#2c3e50', font: { size: 12 } } },
        tooltip: {
          callbacks: {
            afterBody(items) {
              const i = items[0].dataIndex;
              const s = seasons[i];
              return [`Record: ${recordStr(s)}`, `GF: ${s.record.gf}  GA: ${s.record.ga}`, `Standing: ${s.our_rank || '?'}/${s.total_teams || '?'}`];
            },
          },
        },
      },
      scales: {
        x: { ticks: { color: '#5d6d7e', font: { size: 11 }, maxRotation: 45 } },
        y: { beginAtZero: true, ticks: { color: '#5d6d7e', stepSize: 5 }, grid: { color: 'rgba(0,0,0,0.06)' } },
      },
    },
  });
}

/* ===== SECTION: ALL-TIME RECORD TABLE ===== */
function renderSeasonTable() {
  const COLS = [
    { key: 'season',      label: 'Season' },
    { key: 'team_name',   label: 'Team Name' },
    { key: 'record.gp',  label: 'GP',  num: true },
    { key: 'record.w',   label: 'W',   num: true },
    { key: 'record.l',   label: 'L',   num: true },
    { key: 'record.otl', label: 'OTL', num: true },
    { key: 'record.sol', label: 'SOL', num: true },
    { key: 'record.pts', label: 'PTS', num: true },
    { key: 'record.gf',  label: 'GF',  num: true },
    { key: 'record.ga',  label: 'GA',  num: true },
    { key: '_pm',        label: '+/-', num: true },
    { key: '_standing',  label: 'Standing' },
    { key: '_source',    label: 'Source' },
  ];

  const seasons = APP.seasons.map(s => ({
    ...s,
    _pm: (s.record.gf || 0) - (s.record.ga || 0),
    _standing: s.our_rank ? `${s.our_rank}/${s.total_teams}` : '—',
  }));

  let sorted = seasonSortKey
    ? sortBy(seasons, seasonSortKey, seasonSortDir)
    : seasons;

  const thead = `<thead><tr>${COLS.map(c =>
    `<th class="${seasonSortKey === c.key ? 'sorted' : ''}" data-col="${c.key}" ${c.num ? 'style="text-align:right"' : ''}>${c.label}</th>`
  ).join('')}</tr></thead>`;

  const tbody = sorted.map(s => {
    const wPct = s.record.gp > 0 ? s.record.w / s.record.gp : 0;
    const rowCls = wPct >= 0.6 ? 'season-win' : wPct <= 0.4 ? 'season-loss' : 'season-mid';
    const pm = (s.record.gf || 0) - (s.record.ga || 0);
    const pmStr = pm >= 0 ? `+${pm}` : `${pm}`;
    const src = s.source_urls?.team_home
      ? extLink(s.source_urls.team_home, '🔗')
      : '—';
    return `<tr class="${rowCls}">
      <td>${s.season}</td>
      <td>${s.team_name || '—'}</td>
      <td class="num">${fmt(s.record.gp)}</td>
      <td class="num">${fmt(s.record.w)}</td>
      <td class="num">${fmt(s.record.l)}</td>
      <td class="num">${fmt(s.record.otl)}</td>
      <td class="num">${fmt(s.record.sol)}</td>
      <td class="num" style="font-weight:700">${fmt(s.record.pts)}</td>
      <td class="num">${fmt(s.record.gf)}</td>
      <td class="num">${fmt(s.record.ga)}</td>
      <td class="num" style="color:${pm>=0?'#27ae60':'#e74c3c'};font-weight:600">${pmStr}</td>
      <td>${s.our_rank ? `${s.our_rank}/${s.total_teams}` : '—'}</td>
      <td>${src}</td>
    </tr>`;
  }).join('');

  document.getElementById('season-table').innerHTML = `${thead}<tbody>${tbody}</tbody>`;

  // Sort click handlers
  document.querySelectorAll('#season-table thead th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (seasonSortKey === col) seasonSortDir *= -1;
      else { seasonSortKey = col; seasonSortDir = -1; }
      renderSeasonTable();
    });
  });
}

/* ===== SECTION: CAREER LEADERS ===== */
function renderCareerTable() {
  const COLS = [
    { key: 'name',         label: 'Player' },
    { key: 'seasons_count',label: 'Seasons', num: true },
    { key: 'career_gp',   label: 'GP',  num: true },
    { key: 'career_g',    label: 'G',   num: true },
    { key: 'career_a',    label: 'A',   num: true },
    { key: 'career_pts',  label: 'PTS', num: true },
    { key: 'career_pim',  label: 'PIM', num: true },
  ];

  let players = sortBy(APP.career_stats, careerSortKey, careerSortDir);
  const total = players.length;
  if (!careerShowAll) players = players.slice(0, 25);

  const thead = `<thead><tr>
    <th style="width:30px"></th>
    ${COLS.map(c =>
      `<th class="${careerSortKey === c.key ? 'sorted' : ''}" data-col="${c.key}" ${c.num ? 'style="text-align:right"' : ''}>${c.label}</th>`
    ).join('')}
  </tr></thead>`;

  const rows = players.map((p, i) => {
    const rank = i + 1;
    const expanded = expandedPlayers.has(p.name);
    const seasonRows = p.seasons.map(s =>
      `<tr>
        <td>${s.season}</td>
        <td style="color:#5d6d7e;font-size:0.8rem">${s.team_name || ''}</td>
        <td class="num">${s.gp}</td>
        <td class="num">${s.g}</td>
        <td class="num">${s.a}</td>
        <td class="num" style="font-weight:700">${s.pts}</td>
        <td class="num">${s.pim}</td>
      </tr>`
    ).join('');
    return `
    <tr class="player-row" data-player="${encodeURIComponent(p.name)}">
      <td style="color:#aaa;font-size:0.8rem;text-align:center">${rank}</td>
      <td class="name-cell">▶ ${p.name}</td>
      <td class="num">${p.seasons_count}</td>
      <td class="num">${p.career_gp}</td>
      <td class="num">${p.career_g}</td>
      <td class="num">${p.career_a}</td>
      <td class="num" style="font-weight:700;color:var(--blue-dark)">${p.career_pts}</td>
      <td class="num">${p.career_pim}</td>
    </tr>
    <tr class="player-expand-row ${expanded ? 'open' : ''}" id="expand-${encodeURIComponent(p.name)}">
      <td class="player-expand-cell" colspan="8">
        <div class="player-expand-inner">
          <table class="player-season-table">
            <thead><tr>
              <th>Season</th><th>Team</th><th class="num">GP</th>
              <th class="num">G</th><th class="num">A</th>
              <th class="num">PTS</th><th class="num">PIM</th>
            </tr></thead>
            <tbody>${seasonRows}</tbody>
          </table>
        </div>
      </td>
    </tr>`;
  }).join('');

  const showBtn = total > 25
    ? `<button class="show-more-btn" id="career-show-more">
        ${careerShowAll ? '▲ Show top 25' : `▼ Show all ${total} players`}
       </button>`
    : '';

  document.getElementById('career-table-wrap').innerHTML =
    `<div class="table-scroll"><table id="career-table">${thead}<tbody>${rows}</tbody></table></div>${showBtn}`;

  // Sort handlers
  document.querySelectorAll('#career-table thead th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (careerSortKey === col) careerSortDir *= -1;
      else { careerSortKey = col; careerSortDir = -1; }
      renderCareerTable();
    });
  });

  // Expand handlers
  document.querySelectorAll('.player-row').forEach(row => {
    row.addEventListener('click', () => {
      const name = decodeURIComponent(row.dataset.player);
      if (expandedPlayers.has(name)) expandedPlayers.delete(name);
      else expandedPlayers.add(name);
      renderCareerTable();
    });
  });

  // Show more
  const smBtn = document.getElementById('career-show-more');
  if (smBtn) smBtn.addEventListener('click', () => { careerShowAll = !careerShowAll; renderCareerTable(); });
}

function gsIframe(url, height = 500) {
  return `<iframe src="${url}&configuration[secondary-colour]=0077cc&configuration[logo]=false&configuration[navigation]=false&configuration[filters]=false"
    width="100%" height="${height}" frameborder="0" style="display:block;border:none"></iframe>`;
}

/* ===== SECTION: SEASON BROWSER ===== */
function renderSeasonBrowser() {
  const s = APP.seasons[selectedSeasonIdx];
  if (!s) return;

  const r = s.record;
  const gp = r.gp || (r.w + r.l + r.otl + r.sol);

  // Season card
  const recordFull = [r.w, r.l, r.otl, r.sol].join('-');
  const standingTxt = s.our_rank ? `${s.our_rank}${ordinal(s.our_rank)} of ${s.total_teams}` : '—';

  document.getElementById('season-card').innerHTML = `
    <div>
      <div style="font-size:1.5rem;font-weight:800;color:var(--blue-dark)">${s.team_name || '—'}</div>
      <div style="color:var(--text-muted);font-size:0.88rem;margin-top:2px">${s.season} · ICAHL Wednesday B2</div>
      <div class="season-stat-grid" style="margin-top:12px">
        <div class="stat-pill"><div class="val">${recordFull}</div><div class="lbl">Record</div></div>
        <div class="stat-pill"><div class="val">${r.pts}</div><div class="lbl">Points</div></div>
        <div class="stat-pill"><div class="val">${r.gf || '—'}</div><div class="lbl">GF</div></div>
        <div class="stat-pill"><div class="val">${r.ga || '—'}</div><div class="lbl">GA</div></div>
        <div class="stat-pill"><div class="val" style="color:${(r.gf-r.ga)>=0?'#27ae60':'#e74c3c'}">${plusMinus(r.gf, r.ga)}</div><div class="lbl">+/-</div></div>
        <div class="stat-pill"><div class="val">${pct(r.w, gp)}</div><div class="lbl">Win%</div></div>
      </div>
    </div>
    <div>
      <div class="standing-badge">
        <span class="lbl">Division Standing</span>
        ${s.our_rank ? `${s.our_rank}<span style="font-size:0.7em">/${s.total_teams}</span>` : '—'}
      </div>
    </div>`;

  // Update source links header label
  const srcHeader = document.getElementById('source-links-header');
  if (srcHeader) srcHeader.textContent = s.live ? '🔗 GameSheet Source Links' : '🔗 PointStreak Source Links';

  // Source links
  const urls = s.source_urls || {};
  const linkItems = [
    ['team_home', '🏒 Team Home'],
    ['schedule', '📅 Schedule'],
    ['roster', '👥 Roster'],
    ['standings', '📊 Standings'],
  ].filter(([k]) => urls[k]);

  document.getElementById('season-source-links').innerHTML = linkItems.length
    ? linkItems.map(([k, label]) => extLink(urls[k], label)).join('')
    : '<span style="color:#aaa;font-size:0.85rem">No source links available</span>';

  // Schedule table
  const games = s.schedule || [];
  const schedRows = games.map(g => {
    const cls = gameResultClass(g);
    const loc = g.is_home ? '<span class="badge badge-home">H</span>' : '<span class="badge badge-away">A</span>';
    const opp = g.opponent || '—';
    const score = g.our_score != null ? `${g.our_score} – ${g.opp_score}` : '—';
    const date = g.date || g.date_raw || '—';
    return `<tr class="${cls}">
      <td>${date}</td>
      <td>${loc} ${opp}</td>
      <td class="num" style="font-weight:600">${score}</td>
      <td>${resultBadge(g)}</td>
      ${g.gameid ? `<td>${extLink(`http://stats.pointstreak.com/players/players-boxscore.html?gameid=${g.gameid}`, '🔗')}</td>` : '<td></td>'}
    </tr>`;
  }).join('');

  document.getElementById('season-schedule').innerHTML = `
    <div class="table-scroll">
      <table>
        <thead><tr>
          <th>Date</th><th>Opponent</th><th class="num">Score</th><th>Result</th><th>Box</th>
        </tr></thead>
        <tbody>${schedRows || '<tr><td colspan="5" style="text-align:center;color:#aaa;padding:20px">No games found</td></tr>'}</tbody>
      </table>
    </div>`;

  // Points leaders top 10 — live iframe for GameSheet seasons, table for archived
  const iurls = s.iframe_urls || {};
  if (s.live && iurls.players) {
    document.getElementById('season-leaders').innerHTML =
      gsIframe(iurls.players, 480);
  } else {
    const top10 = [...(s.skaters || [])].sort((a, b) => b.pts - a.pts).slice(0, 10);
    const leaderRows = top10.map((p, i) =>
      `<tr>
        <td style="color:#aaa;font-size:0.8rem;text-align:center">${i + 1}</td>
        <td class="name-cell">${p.name}</td>
        <td class="num">${p.gp}</td>
        <td class="num">${p.g}</td>
        <td class="num">${p.a}</td>
        <td class="num" style="font-weight:700">${p.pts}</td>
        <td class="num">${p.pim}</td>
      </tr>`
    ).join('');
    document.getElementById('season-leaders').innerHTML = `
      <div class="table-scroll">
        <table>
          <thead><tr>
            <th style="width:30px"></th>
            <th>Player</th><th class="num">GP</th>
            <th class="num">G</th><th class="num">A</th>
            <th class="num">PTS</th><th class="num">PIM</th>
          </tr></thead>
          <tbody>${leaderRows || '<tr><td colspan="7" style="text-align:center;color:#aaa;padding:20px">No data</td></tr>'}</tbody>
        </table>
      </div>`;
  }

  // Standings — live iframe for GameSheet seasons
  if (s.live && iurls.standings) {
    document.getElementById('season-standings').innerHTML = gsIframe(iurls.standings, 380);
  } else {
    const ourId = String(s.teamid);
    const standRows = (s.standings || []).map(t => {
      const isUs = String(t.teamid) === ourId || t.team?.toLowerCase() === 'parking lot beers';
      return `<tr class="${isUs ? 'our-team' : ''}">
        <td style="color:#aaa;text-align:center">${t.rank || ''}</td>
        <td class="name-cell">${t.team || '—'}${isUs ? ' ⬅' : ''}</td>
        <td class="num">${fmt(t.gp)}</td>
        <td class="num">${fmt(t.w)}</td>
        <td class="num">${fmt(t.l)}</td>
        <td class="num">${fmt(t.otl)}</td>
        <td class="num">${fmt(t.sol)}</td>
        <td class="num" style="font-weight:700">${fmt(t.pts)}</td>
        <td class="num">${fmt(t.gf)}</td>
        <td class="num">${fmt(t.ga)}</td>
      </tr>`;
    }).join('');
    document.getElementById('season-standings').innerHTML = `
      <div class="table-scroll">
        <table>
          <thead><tr>
            <th style="width:30px"></th>
            <th>Team</th>
            <th class="num">GP</th><th class="num">W</th><th class="num">L</th>
            <th class="num">OTL</th><th class="num">SOL</th>
            <th class="num">PTS</th><th class="num">GF</th><th class="num">GA</th>
          </tr></thead>
          <tbody>${standRows || '<tr><td colspan="10" style="text-align:center;color:#aaa;padding:20px">No standings data</td></tr>'}</tbody>
        </table>
      </div>`;
  }

  // Goalies — live iframe for GameSheet seasons
  if (s.live && iurls.goalies) {
    document.getElementById('season-goalies').innerHTML = gsIframe(iurls.goalies, 380);
  } else {
    const goalieRows = (s.goalies || []).map(g =>
      `<tr>
        <td class="name-cell">${g.name}</td>
        <td class="num">${g.gp}</td>
        <td class="num">${g.w}-${g.l}</td>
        <td class="num">${g.gaa?.toFixed(2) || '—'}</td>
        <td class="num">${g.sv_pct ? (g.sv_pct * 100).toFixed(1) + '%' : '—'}</td>
        <td class="num">${g.so || 0}</td>
      </tr>`
    ).join('');
    document.getElementById('season-goalies').innerHTML = goalieRows
      ? `<div class="table-scroll"><table>
          <thead><tr>
            <th>Goalie</th>
            <th class="num">GP</th><th class="num">W-L</th>
            <th class="num">GAA</th><th class="num">SV%</th><th class="num">SO</th>
          </tr></thead>
          <tbody>${goalieRows}</tbody>
         </table></div>`
      : '<div style="padding:12px 24px;color:#aaa;font-size:0.85rem">No goalie data</div>';
  }
}

function ordinal(n) {
  const s = ['th','st','nd','rd'];
  const v = n % 100;
  return n + (s[(v-20)%10]||s[v]||s[0]);
}

/* ===== NAV SCROLL ===== */
function initNav() {
  document.querySelectorAll('.sticky-nav button[data-target]').forEach(btn => {
    btn.addEventListener('click', () => {
      const el = document.getElementById(btn.dataset.target);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });

  // Highlight active nav
  const sections = ['section-upcoming', 'section-chart', 'section-seasons', 'section-career', 'section-browser'];
  const observer = new IntersectionObserver(entries => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        const id = e.target.id;
        document.querySelectorAll('.sticky-nav button').forEach(b => b.classList.remove('active'));
        const btn = document.querySelector(`.sticky-nav button[data-target="${id}"]`);
        if (btn) btn.classList.add('active');
      }
    });
  }, { threshold: 0.3 });

  sections.forEach(id => {
    const el = document.getElementById(id);
    if (el) observer.observe(el);
  });
}

/* ===== SEASON SELECTOR ===== */
function initSeasonSelector() {
  const select = document.getElementById('season-select');
  APP.seasons.forEach((s, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = `${s.season} — ${s.team_name || '?'} (${s.record.w}-${s.record.l})`;
    select.appendChild(opt);
  });
  // Default to most recent season
  selectedSeasonIdx = APP.seasons.length - 1;
  select.value = selectedSeasonIdx;
  select.addEventListener('change', () => {
    selectedSeasonIdx = parseInt(select.value);
    renderSeasonBrowser();
  });
}

/* ===== HEADER ===== */
function populateHeader() {
  // The team first used "Parking Lot Beers" in Summer 2022, reverted to "Vinegar Strokes"
  // for Winter 22/23, then permanently switched starting Summer 2023.
  const transNote = `First used <strong>Parking Lot Beers</strong> in <strong>Summer 2022</strong>; permanent from <strong>Summer 2023</strong>`;

  document.getElementById('name-transition-text').innerHTML = transNote;
  document.getElementById('total-games-count').textContent = APP.total_games;
  document.getElementById('total-seasons-count').textContent = APP.seasons.length;
}

/* ===== SECTION: UPCOMING PLB GAMES ===== */
function renderUpcomingPLB() {
  const s26 = APP.seasons.find(s => s.slug === 'summer_2026');
  const el = document.getElementById('upcoming-plb-games');
  if (!el) return;

  if (!s26) {
    el.innerHTML = '<div style="padding:12px 24px;color:#aaa">No Summer 2026 data found.</div>';
    return;
  }

  const today = new Date().toISOString().slice(0, 10);
  const upcoming = (s26.schedule || []).filter(g => g.date >= today);
  const played   = (s26.schedule || []).filter(g => g.date < today && g.result);

  if (!upcoming.length && !played.length) {
    el.innerHTML = '<div style="padding:12px 24px;color:#aaa;font-size:0.88rem">No games scheduled yet.</div>';
    return;
  }

  const gameRow = (g, isPast) => {
    const loc  = g.is_home ? '<span class="badge badge-home">H</span>' : '<span class="badge badge-away">A</span>';
    const score = g.our_score != null ? `${g.our_score} – ${g.opp_score}` : g.time;
    const res   = isPast ? resultBadge(g) : `<span style="color:#aaa;font-size:0.85rem">${g.time}</span>`;
    const cls   = isPast ? gameResultClass(g) : '';
    return `<tr class="${cls}">
      <td>${g.date}</td>
      <td>${loc} ${g.opponent}</td>
      <td class="num" style="font-weight:600">${isPast ? score : '—'}</td>
      <td>${res}</td>
      <td style="font-size:0.75rem;color:#aaa">${g.rink || ''}</td>
    </tr>`;
  };

  const allRows = [
    ...played.map(g => gameRow(g, true)),
    ...upcoming.map(g => gameRow(g, false)),
  ].join('');

  const lastUpdated = s26.last_updated ? `<div style="padding:6px 16px;font-size:0.75rem;color:#aaa">Last synced: ${s26.last_updated} · Run <code>python3 update.py</code> to refresh</div>` : '';

  el.innerHTML = `
    <div class="table-scroll">
      <table>
        <thead><tr>
          <th>Date</th><th>Opponent</th><th class="num">Score</th><th>Result</th><th>Rink</th>
        </tr></thead>
        <tbody>${allRows}</tbody>
      </table>
    </div>
    ${lastUpdated}`;
}

/* ===== INIT ===== */
async function init() {
  try {
    const res = await fetch('data/app_data.json');
    APP = await res.json();
  } catch (e) {
    document.body.innerHTML = `<div style="color:red;padding:40px;font-size:1.2rem">
      Failed to load data/app_data.json — run process.py first.<br><small>${e}</small>
    </div>`;
    return;
  }

  populateHeader();
  renderUpcomingPLB();
  renderChart();
  renderSeasonTable();
  renderCareerTable();
  initSeasonSelector();
  renderSeasonBrowser();
  initNav();
}

document.addEventListener('DOMContentLoaded', init);
