// Claude Panel - lokalny serwer danych dla ekranu naściennego.
// Bez zaleznosci. Uruchom: node server.js
const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFile } = require('child_process');

const HOME = os.homedir();
const PROJECTS = path.join(HOME, '.claude', 'projects');
const CLAUDE_JSON = path.join(HOME, '.claude.json');
const CREDS = path.join(HOME, '.claude', '.credentials.json');

// Ten sam endpoint, ktory wola Claude Code. Bez tego limity odswiezaly sie
// dopiero, gdy klient sam po nie siegnal - bywalo, ze co dwie godziny.
const USAGE_URL = 'https://api.anthropic.com/api/oauth/usage';
const LIVE_POLL_MS = Number(process.env.PANEL_USAGE_POLL_MS) || 2 * 60 * 1000;
const LIVE_ENABLED = process.env.PANEL_NO_API !== '1';

// Ostatni udany odczyt z API trzymamy takze na dysku. Bez tego restart
// serwera kasowal swiezsza wartosc i panel wracal do starego pliku - czyli
// procent POTRAFIL SIE COFNAC, sugerujac spadek zuzycia, ktorego nie bylo.
const USAGE_CACHE = path.join(__dirname, 'usage-cache.json');
const PUBLIC = path.join(__dirname, 'public');
const PORT = Number(process.env.PANEL_PORT) || 4747;

const KEEP_DAYS = 8;              // ile dni historii trzymamy w pamieci
const ACTIVE_MS = 30 * 60 * 1000; // jak dlugo sesja wisi na liscie po ostatnim ruchu
const WORKING_MS = 45 * 1000;     // ponizej tego uznajemy, ze sesja PRACUJE
const ALERT_TTL = 30 * 60 * 1000; // jak dlugo wazny jest meldunek z hooka

// ---------------------------------------------------------------- stan
let records = [];                 // {ts, model, in, out, cw, cr}
const offsets = new Map();        // sciezka -> ile bajtow juz przeczytane
let sessions = new Map();         // sessionId -> {title, project, idleMs, agents}
const titles = new Map();         // sessionId -> {custom, ai, prompt}
let procCount = 0;
let lastGoodLimits = null;
const alerts = new Map();         // sessionId -> {state, cwd, message, at}

// ---------------------------------------------------------------- limity z ~/.claude.json
let livePayload = null;   // {fetchedAtMs, utilization} - prosto z API
let liveError = null;
let lastAccount = null;

// Endpoint ma wlasny limit czestotliwosci i potrafi odpowiedziec 429.
// Po odmowie odczekujemy coraz dluzej, zamiast dobijac sie w kolko.
let backoffUntil = 0;
let backoffMs = 0;
const BACKOFF_MAX = 30 * 60 * 1000;

// Odpytuje API o zuzycie. Token czytamy z pliku PRZY KAZDYM wywolaniu, zeby
// samo podchwycic odswiezenie, gdy Claude Code go wymieni. Nigdzie go nie
// kopiujemy, nie logujemy i nie zwracamy w /api/state.
async function pollLiveUsage() {
  if (!LIVE_ENABLED) return;
  if (Date.now() < backoffUntil) return;
  let token;
  try {
    const cred = JSON.parse(fs.readFileSync(CREDS, 'utf8'));
    token = cred.claudeAiOauth && cred.claudeAiOauth.accessToken;
  } catch (e) {
    liveError = 'brak pliku poswiadczen';
    return;
  }
  if (!token) { liveError = 'brak tokenu'; return; }

  try {
    const ctl = AbortSignal.timeout(8000);
    const r = await fetch(USAGE_URL, {
      headers: {
        Authorization: 'Bearer ' + token,
        Accept: 'application/json',
        'User-Agent': 'claude-panel/1.0',
      },
      signal: ctl,
    });
    if (r.status === 429) {
      backoffMs = Math.min(backoffMs ? backoffMs * 2 : LIVE_POLL_MS, BACKOFF_MAX);
      backoffUntil = Date.now() + backoffMs;
      liveError = 'HTTP 429 - pauza ' + Math.round(backoffMs / 60000) + ' min';
      return;
    }
    if (!r.ok) {
      liveError = 'HTTP ' + r.status + (r.status === 401 ? ' (token wygasl?)' : '');
      return;
    }
    const j = await r.json();
    if (!j || !j.limits) { liveError = 'nieoczekiwany ksztalt odpowiedzi'; return; }
    livePayload = { fetchedAtMs: Date.now(), utilization: j };
    liveError = null;
    backoffMs = 0;
    backoffUntil = 0;
    try {
      fs.writeFileSync(USAGE_CACHE, JSON.stringify(livePayload), 'utf8');
    } catch (e) {
      // brak zapisu nie jest powodem, zeby zepsuc odczyt
    }
  } catch (e) {
    liveError = e.name === 'TimeoutError' ? 'przekroczony czas' : e.message;
  }
}

function mapUtilization(u, fetchedAtMs, source) {
  const byKind = {};
  const scopedList = [];
  for (const l of u.limits || []) {
    if (l.kind === 'weekly_scoped') {
      // Limit tygodniowy per-model. Nazwa modelu zalezy od konta
      // (u jednych "Fable", u innych inny model albo brak) - bierzemy
      // dynamicznie z API zamiast zaszywac konkretna nazwe.
      const name = (l.scope && l.scope.model && l.scope.model.display_name) || 'model';
      scopedList.push({ name, ...pick(l, null) });
    } else {
      byKind[l.kind] = l;
    }
  }
  return {
    fetchedAtMs,
    ageMs: fetchedAtMs ? Date.now() - fetchedAtMs : null,
    source,
    liveError,
    session: pick(byKind.session, u.five_hour),
    weekly: pick(byKind.weekly_all, u.seven_day),
    scoped: scopedList[0] || null,
    plan: (lastAccount && lastAccount.organizationRateLimitTier) || null,
    who: (lastAccount && lastAccount.displayName) || null,
  };
}

function readLimits() {
  let fromFile = null;
  try {
    const cj = JSON.parse(fs.readFileSync(CLAUDE_JSON, 'utf8'));
    if (cj.oauthAccount) lastAccount = cj.oauthAccount;
    const cu = cj.cachedUsageUtilization;
    if (cu && cu.utilization) fromFile = { u: cu.utilization, at: cu.fetchedAtMs || 0 };
  } catch (e) {
    // plik bywa czytany w trakcie zapisu - ignorujemy i lecimy dalej
  }

  const fromApi = livePayload
    ? { u: livePayload.utilization, at: livePayload.fetchedAtMs }
    : null;

  // Wygrywa swiezszy odczyt, nie zrodlo.
  let best = fromApi, source = 'api';
  if (!best || (fromFile && fromFile.at > best.at)) { best = fromFile; source = 'plik'; }
  if (!best) return lastGoodLimits;

  lastGoodLimits = mapUtilization(best.u, best.at, source);
  return lastGoodLimits;
}

function pick(limitEntry, fallback) {
  if (limitEntry) {
    return {
      percent: limitEntry.percent,
      resetsAt: limitEntry.resets_at,
      severity: limitEntry.severity || 'normal',
      active: !!limitEntry.is_active,
    };
  }
  if (fallback) {
    return {
      percent: fallback.utilization,
      resetsAt: fallback.resets_at,
      severity: 'normal',
      active: false,
    };
  }
  return null;
}

// ---------------------------------------------------------------- skan transkryptow
function listTranscripts() {
  const out = [];
  const cutoff = Date.now() - KEEP_DAYS * 86400000;
  let stack = [PROJECTS];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) stack.push(p);
      else if (e.name.endsWith('.jsonl')) {
        let st;
        try { st = fs.statSync(p); } catch { continue; }
        if (st.mtimeMs >= cutoff) out.push({ p, size: st.size, mtime: st.mtimeMs });
      }
    }
  }
  return out;
}

function ingest(file) {
  const seen = offsets.get(file.p) || 0;
  if (file.size <= seen) return 0;

  let buf;
  try {
    const fd = fs.openSync(file.p, 'r');
    const len = file.size - seen;
    buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, seen);
    fs.closeSync(fd);
  } catch { return 0; }

  const text = buf.toString('utf8');
  // ostatnia linia moze byc urwana w polowie zapisu - cofamy sie do ostatniego \n
  const lastNl = text.lastIndexOf('\n');
  if (lastNl === -1) return 0;
  offsets.set(file.p, seen + Buffer.byteLength(text.slice(0, lastNl + 1), 'utf8'));

  let added = 0;
  const projName = decodeProject(file.p);
  const sessName = path.basename(file.p, '.jsonl');
  const agent = isAgentTranscript(file.p);

  for (const line of text.slice(0, lastNl).split('\n')) {
    if (!line) continue;

    // Nazwy sesji. Tanie sprawdzenie na stringu, zanim ruszymy JSON.parse.
    if (line.indexOf('-title"') !== -1 || line.indexOf('"last-prompt"') !== -1) {
      try {
        const t = JSON.parse(line);
        const sid = t.sessionId;
        if (sid) {
          const cur = titles.get(sid) || {};
          if (t.customTitle) cur.custom = t.customTitle;
          if (t.aiTitle) cur.ai = t.aiTitle;
          if (t.lastPrompt) cur.prompt = String(t.lastPrompt).replace(/\s+/g, ' ').trim();
          titles.set(sid, cur);
        }
      } catch {}
      continue;
    }

    if (line.indexOf('"usage"') === -1) continue; // szybki filtr, oszczedza JSON.parse
    let j;
    try { j = JSON.parse(line); } catch { continue; }
    const m = j.message;
    if (!m || !m.usage || !j.timestamp) continue;
    const u = m.usage;
    records.push({
      ts: Date.parse(j.timestamp),
      model: m.model || '?',
      in: u.input_tokens || 0,
      out: u.output_tokens || 0,
      cw: u.cache_creation_input_tokens || 0,
      cr: u.cache_read_input_tokens || 0,
      proj: projName,
      sess: sessName,
      agent,
    });
    added++;
  }
  return added;
}

// Projekt = KATALOG NAJWYZSZEGO POZIOMU pod projects/. Glebiej siedza podkatalogi
// sesji, a w nich subagents/workflows/wf_* - to wciaz ten sam projekt.
function decodeProject(filePath) {
  const rel = path.relative(PROJECTS, filePath);
  const top = rel.split(path.sep)[0] || '';
  let s = top.replace(/^[A-Za-z]--/, '')        // Windows: "c--"            -> ""
             .replace(/^-?Users-[^-]+-?/, '');  // "(-)Users-<nazwa>-" -> "" (macOS ma wiodacy "-")
  return s ? s.replace(/-/g, ' ') : 'katalog domowy';
}

// Czy transkrypt nalezy do subagenta/workflowa, czy do glownej sesji.
function isAgentTranscript(filePath) {
  const rel = path.relative(PROJECTS, filePath);
  return /(^|[\\/])(subagents|workflows)([\\/]|$)/.test(rel);
}

// Sesja-rodzic. Pliki agentow leza w <projekt>/<uuid-sesji>/subagents/...,
// wiec ich wlascicielem jest katalog tuz pod projektem.
function sessionIdFor(filePath) {
  const parts = path.relative(PROJECTS, filePath).split(path.sep);
  return parts.length > 2 ? parts[1] : path.basename(filePath, '.jsonl');
}

// Nazwa sesji wg priorytetu: Twoja wlasna -> od AI -> pierwszy prompt -> UUID.
function titleFor(sessionId) {
  const t = titles.get(sessionId);
  if (t) {
    if (t.custom) return { name: t.custom, kind: 'wlasny' };
    if (t.ai) return { name: t.ai, kind: 'ai' };
    if (t.prompt) return { name: t.prompt.slice(0, 60), kind: 'prompt' };
  }
  return { name: sessionId.slice(0, 8), kind: 'uuid' };
}

function refresh() {
  const files = listTranscripts();
  for (const f of files) ingest(f);

  // przytnij historie
  const cutoff = Date.now() - KEEP_DAYS * 86400000;
  if (records.length > 200000) records = records.filter(r => r.ts >= cutoff);

  // Aktywne sesje - grupowane po sesji-rodzicu, zeby agenci nie robili
  // z listy sciany hashy.
  const now = Date.now();
  sessions = new Map();
  for (const f of files) {
    if (now - f.mtime > ACTIVE_MS) continue;
    const sid = sessionIdFor(f.p);
    const agent = isAgentTranscript(f.p);
    const cur = sessions.get(sid) || {
      sid,
      id: sid.slice(0, 8),
      project: decodeProject(f.p),
      agents: 0,
      idleMs: Infinity,
    };
    if (agent) cur.agents++;
    cur.idleMs = Math.min(cur.idleMs, now - f.mtime);
    sessions.set(sid, cur);
  }
  for (const [sid, s] of sessions) {
    const t = titleFor(sid);
    s.title = t.name;
    s.titleKind = t.kind;
  }
}

function countProcesses() {
  if (process.platform === 'win32') {
    execFile('tasklist', ['/FI', 'IMAGENAME eq claude.exe', '/FO', 'CSV', '/NH'], { windowsHide: true }, (err, stdout) => {
      if (err) return;
      procCount = (stdout.match(/^"claude\.exe"/gim) || []).length;
    });
  } else {
    execFile('pgrep', ['-x', 'claude'], (err, stdout) => {
      // pgrep konczy sie kodem 1, gdy nic nie znalazl - to nie blad
      procCount = err ? 0 : stdout.split('\n').filter(Boolean).length;
    });
  }
}

// ---------------------------------------------------------------- agregacja
function sumSince(fromMs) {
  const acc = { in: 0, out: 0, cw: 0, cr: 0, msgs: 0, models: {} };
  for (let i = records.length - 1; i >= 0; i--) {
    const r = records[i];
    if (r.ts < fromMs) continue;
    acc.in += r.in; acc.out += r.out; acc.cw += r.cw; acc.cr += r.cr; acc.msgs++;
    const key = prettyModel(r.model);
    if (!acc.models[key]) acc.models[key] = { out: 0, msgs: 0 };
    acc.models[key].out += r.out;
    acc.models[key].msgs++;
  }
  acc.total = acc.in + acc.out + acc.cw + acc.cr;
  return acc;
}

// Kubelki godzinowe - wykres "co sie dzialo w ciagu doby".
function hourly(hours) {
  const anchor = new Date();
  anchor.setMinutes(0, 0, 0);
  const first = anchor.getTime() - (hours - 1) * 3600000;
  const out = [];
  for (let i = 0; i < hours; i++) out.push({ t: first + i * 3600000, out: 0, msgs: 0 });
  for (const r of records) {
    if (r.ts < first) continue;
    const i = Math.floor((r.ts - first) / 3600000);
    if (i >= 0 && i < out.length) { out[i].out += r.out; out[i].msgs++; }
  }
  return out;
}

// Kubelki dobowe - ostatnie N dni.
function daily(days) {
  const anchor = new Date();
  anchor.setHours(0, 0, 0, 0);
  const first = anchor.getTime() - (days - 1) * 86400000;
  const out = [];
  for (let i = 0; i < days; i++) out.push({ t: first + i * 86400000, out: 0, msgs: 0 });
  for (const r of records) {
    if (r.ts < first) continue;
    const i = Math.floor((r.ts - first) / 86400000);
    if (i >= 0 && i < out.length) { out[i].out += r.out; out[i].msgs++; }
  }
  return out;
}

function topProjects(fromMs, limit) {
  const acc = new Map();
  for (const r of records) {
    if (r.ts < fromMs) continue;
    const cur = acc.get(r.proj) || { out: 0, msgs: 0 };
    cur.out += r.out; cur.msgs++;
    acc.set(r.proj, cur);
  }
  return [...acc.entries()]
    .map(([name, v]) => ({ name, ...v }))
    .sort((a, b) => b.out - a.out)
    .slice(0, limit || 6);
}

// Ile pracy zjadaja sesje glowne, a ile subagenci/workflow.
function splitMainAgents(fromMs) {
  const main = { name: 'sesje główne', out: 0, msgs: 0 };
  const ag = { name: 'agenci / workflow', out: 0, msgs: 0 };
  for (const r of records) {
    if (r.ts < fromMs) continue;
    const t = r.agent ? ag : main;
    t.out += r.out; t.msgs++;
  }
  return [main, ag];
}

function prettyModel(m) {
  if (!m || m === '<synthetic>') return 'inne';
  if (m.includes('fable')) return 'Fable';
  if (m.includes('opus-5')) return 'Opus 5';
  if (m.includes('opus-4-8')) return 'Opus 4.8';
  if (m.includes('opus')) return 'Opus';
  if (m.includes('sonnet')) return 'Sonnet';
  if (m.includes('haiku')) return 'Haiku';
  return m;
}

function buildState() {
  const lim = readLimits();
  const now = Date.now();

  // Okna liczymy wstecz od oficjalnych resetow, zeby sie zgadzaly z Anthropic -
  // ale TYLKO gdy reset jest jeszcze w przyszlosci. Cache bywa nieodswiezony
  // godzinami i przeterminowany reset rozciagnalby okno na kilkadziesiat godzin.
  const windowStart = (resetsAt, spanMs) => {
    const t = resetsAt ? Date.parse(resetsAt) : NaN;
    return (!isNaN(t) && t > now) ? t - spanMs : now - spanMs;
  };
  const fiveHourStart = windowStart(lim && lim.session && lim.session.resetsAt, 5 * 3600000);
  const weekStart = windowStart(lim && lim.weekly && lim.weekly.resetsAt, 7 * 86400000);

  return {
    now,
    limits: lim,
    window5h: sumSince(fiveHourStart),
    window7d: sumSince(weekStart),
    today: sumSince(new Date().setHours(0, 0, 0, 0)),
    hourly: hourly(24),
    daily: daily(7),
    projects: topProjects(weekStart, 6),
    split: splitMainAgents(weekStart),
    sessions: [...sessions.values()]
      .map(s => {
        const a = alerts.get(s.sid);
        const fresh = a && now - a.at < ALERT_TTL;
        // Hook ma pierwszenstwo - tylko on wie, ze Claude czeka na czlowieka.
        // Bez niego zostaje sam ruch w transkrypcie: pracuje albo skonczyla.
        let state = s.idleMs < WORKING_MS ? 'pracuje' : 'gotowe';
        if (fresh && a.state === 'czeka') state = 'czeka';
        else if (fresh && a.state === 'gotowe' && s.idleMs >= WORKING_MS) state = 'gotowe';
        return { ...s, state, hooked: !!fresh };
      })
      .sort((a, b) => {
        const rank = x => (x.state === 'czeka' ? 0 : x.state === 'pracuje' ? 1 : 2);
        return rank(a) - rank(b) || a.idleMs - b.idleMs;
      }),
    processes: procCount,
    alerts: [...alerts.entries()].map(([id, a]) => ({
      id: id.slice(0, 8),
      title: titleFor(id).name,
      ...a,
    })).filter(a => now - a.at < ALERT_TTL).sort((a, b) => b.at - a.at),
  };
}

// ---------------------------------------------------------------- serwer
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');

  if (url.pathname === '/api/state') {
    const body = JSON.stringify(buildState());
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
    return res.end(body);
  }

  // hooki Claude Code melduja tu stan sesji
  if (url.pathname === '/api/hook' && req.method === 'POST') {
    let raw = '';
    req.on('data', c => { raw += c; if (raw.length > 1e6) req.destroy(); });
    req.on('end', () => {
      try {
        const h = JSON.parse(raw || '{}');
        const id = h.session_id || 'unknown';
        const ev = h.hook_event_name || 'unknown';
        alerts.set(id, {
          state: ev === 'Notification' ? 'czeka' : ev === 'Stop' ? 'gotowe' : 'pracuje',
          event: ev,
          cwd: h.cwd ? path.basename(h.cwd) : '',
          message: h.message || '',
          at: Date.now(),
        });
      } catch {}
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{"ok":true}');
    });
    return;
  }

  // statyki
  let file = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
  const full = path.join(PUBLIC, file);
  if (!full.startsWith(PUBLIC)) { res.writeHead(403); return res.end('nope'); }
  fs.readFile(full, (err, data) => {
    if (err) { res.writeHead(404); return res.end('404'); }
    const ext = path.extname(full);
    const type = ext === '.html' ? 'text/html; charset=utf-8'
      : ext === '.css' ? 'text/css; charset=utf-8'
      : ext === '.js' ? 'text/javascript; charset=utf-8' : 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store' });
    res.end(data);
  });
});

console.log('Claude Panel - pierwszy skan transkryptow...');
const t0 = Date.now();
refresh();
countProcesses();
console.log('  wczytano ' + records.length + ' wpisow w ' + ((Date.now() - t0) / 1000).toFixed(1) + ' s');

setInterval(refresh, 4000);
setInterval(countProcesses, 10000);

if (LIVE_ENABLED) {
  // Odtwarzamy ostatni udany odczyt sprzed restartu, zeby panel nie cofnal
  // sie do starszej wartosci z pliku, gdy pierwsze odpytanie sie nie uda.
  try {
    const cached = JSON.parse(fs.readFileSync(USAGE_CACHE, 'utf8'));
    if (cached && cached.utilization && cached.fetchedAtMs) {
      livePayload = cached;
      console.log('  odtworzono odczyt z ' +
                  new Date(cached.fetchedAtMs).toLocaleTimeString('pl-PL'));
    }
  } catch (e) {
    // brak pliku przy pierwszym starcie to normalna sytuacja
  }

  let lastPollAt = 0;
  const tick = async () => {
    // Pauze po 429 sprawdzamy PRZED znacznikiem czasu - inaczej licznik
    // przesuwalby sie w trakcie pauzy i po jej koncu trzeba by odczekac
    // jeszcze cale okno 120 s.
    if (Date.now() < backoffUntil) return;
    if (Date.now() - lastPollAt < LIVE_POLL_MS) return;
    lastPollAt = Date.now();
    await pollLiveUsage();
  };
  tick().then(() => {
    console.log('  limity z API: ' + (liveError ? 'BLAD - ' + liveError : 'OK') +
                ' (odpytywanie co ' + Math.round(LIVE_POLL_MS / 1000) + ' s)');
  });
  // Sprawdzamy czesciej niz odpytujemy - inaczej pauza po 429 konczaca sie
  // tuz po tyknieciu zegara kazala czekac cale kolejne okno.
  setInterval(tick, 20000);
} else {
  console.log('  limity z API: WYLACZONE (PANEL_NO_API=1)');
}

// Bez tego proces, ktoremu nie udalo sie zajac portu, wisial dalej jako duch:
// nic nie obslugiwal, a nadzorca uznawal, ze zyje. Konczymy z bledem, zeby
// nadzorca mogl podjac czysta probe.
server.on('error', (e) => {
  if (e.code === 'EADDRINUSE') {
    console.error('port ' + PORT + ' jest juz zajety - koncze');
  } else {
    console.error('blad serwera: ' + e.message);
  }
  process.exit(1);
});

// Domyslnie sluchamy TYLKO lokalnie - na porcie widac tytuly sesji
// i zuzycie, a /api/hook przyjmuje POST-y bez uwierzytelnienia.
// Dostep z sieci (np. tablet) swiadomie: PANEL_BIND=0.0.0.0
const BIND = process.env.PANEL_BIND || '127.0.0.1';

server.listen(PORT, BIND, () => {
  console.log('\n  lokalnie : http://127.0.0.1:' + PORT);
  if (BIND === '0.0.0.0') {
    const nets = os.networkInterfaces();
    for (const name of Object.keys(nets)) {
      for (const n of nets[name]) {
        if (n.family === 'IPv4' && !n.internal) {
          console.log('  w sieci  : http://' + n.address + ':' + PORT);
        }
      }
    }
  } else {
    console.log('  (dostep z sieci wylaczony; wlacz przez PANEL_BIND=0.0.0.0)');
  }
  console.log('\nCtrl+C konczy.');
});
