/* 馬克信箱搜尋站
 *
 * 搜尋單位是「一則故事」（說明欄的一個章節），不是「一集」。
 * 中文沒有詞邊界，所以用子字串比對而不是分詞索引 —— 這正好符合中文使用者的
 * 搜尋直覺，也不會有斷詞錯誤。約一萬筆線性掃描，每次輸入都在幾毫秒內完成。
 */
'use strict';

const PAGE = 60;

const el = {
  q: document.getElementById('q'),
  clear: document.getElementById('clear'),
  suggest: document.getElementById('suggest'),
  filters: document.getElementById('filters'),
  years: document.getElementById('years'),
  count: document.getElementById('count'),
  sort: document.getElementById('sort'),
  results: document.getElementById('results'),
  empty: document.getElementById('empty'),
  more: document.getElementById('more'),
  tagline: document.getElementById('tagline'),
  footmeta: document.getElementById('footmeta'),
  tpl: document.getElementById('tpl-row'),
};

let EPS = [];        // 集數
let ITEMS = [];      // 可搜尋的項目（章節 + 集數本身）
let T2S = new Map(); // 繁 → 簡
let view = [];       // 目前結果
let shown = 0;
let playing = null;  // { key, node }

const state = { q: '', year: 0, sort: 'rel', ep: '' };

/* ------------------------------------------------------------ 文字正規化 */

/** 只留下文字與數字，並轉小寫、全形轉半形、繁體轉簡體。
 *  回傳正規化字串；needMap 為真時同時回傳每個字元對應的原字串索引。 */
function normalize(str, needMap) {
  let out = '';
  const map = needMap ? [] : null;
  for (let i = 0; i < str.length; i++) {
    let c = str[i];
    const code = str.charCodeAt(i);
    // 全形英數與標點 → 半形
    if (code >= 0xff01 && code <= 0xff5e) c = String.fromCharCode(code - 0xfee0);
    if (!/[\p{L}\p{N}]/u.test(c)) continue;      // 標點、空白、emoji 一律略過
    c = c.toLowerCase();
    const s = T2S.get(c);
    if (s) c = s;
    out += c;
    if (map) map.push(i);
  }
  return needMap ? { n: out, map } : out;
}

/* --------------------------------------------------------------- 小工具 */

function hms(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function ymd(d) {
  return d && d.length === 8 ? `${d.slice(0, 4)}/${d.slice(4, 6)}/${d.slice(6)}` : '';
}

function esc(s) {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function linkFor(item) {
  const t = Math.max(0, item.s | 0);
  return `https://youtu.be/${EPS[item.e].i}${t ? `?t=${t}` : ''}`;
}

let toastTimer;
function toast(msg) {
  let t = document.querySelector('.toast');
  if (!t) {
    t = document.createElement('div');
    t.className = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  requestAnimationFrame(() => t.classList.add('show'));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 1800);
}

async function copy(text, btn, label) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch { /* 沒轍就算了 */ }
    ta.remove();
  }
  toast(label);
  if (btn) {
    const old = btn.textContent;
    btn.textContent = '已複製 ✓';
    btn.classList.add('done');
    setTimeout(() => { btn.textContent = old; btn.classList.remove('done'); }, 1500);
  }
}

/* ----------------------------------------------------------------- 搜尋 */

function search() {
  const nq = normalize(state.q);
  const yr = state.year;
  const out = [];

  // 只看某一集：列出該集全部段落，依時間先後，忽略查詢字與年份
  if (state.ep) {
    const e = EPS.findIndex((x) => x.i === state.ep);
    if (e >= 0) {
      for (const it of ITEMS) {
        if (it.e !== e) continue;
        if (it.whole && EPS[e].c.length) continue;
        out.push({ it, sc: 0 });
      }
      out.sort((a, b) => a.it.s - b.it.s);
      view = out;
      shown = 0;
      render(true);
      return;
    }
    state.ep = '';
  }

  if (!nq) {
    // 沒有查詢字：呈現最新的故事，讓人一進來就知道這裡有什麼
    for (const it of ITEMS) {
      if (it.whole && EPS[it.e].c.length) continue;   // 有章節的集數不重複列出整集
      if (yr && EPS[it.e].y !== yr) continue;
      out.push({ it, sc: 0 });
    }
  } else {
    for (const it of ITEMS) {
      if (yr && EPS[it.e].y !== yr) continue;
      const idx = it.k.indexOf(nq);
      if (idx < 0) continue;
      let sc = 40;
      if (it.k === nq) sc += 60;             // 完全相符
      else if (idx === 0) sc += 20;          // 開頭就命中
      sc -= Math.min(idx, 24) * 0.4;         // 命中位置越前面越好
      sc -= Math.min(it.k.length / 10, 8);   // 越短的標題，命中越精準
      if (it.b) sc -= 30;                    // 開場／感謝／業配等罐頭段落降權
      if (it.whole) sc -= 6;                 // 整集比單則故事略低
      out.push({ it, sc });
    }
  }

  if (state.sort === 'rel' && nq) {
    out.sort((a, b) => {
      if (b.sc !== a.sc) return b.sc - a.sc;
      const A = EPS[a.it.e].d, B = EPS[b.it.e].d;
      if (A !== B) return A > B ? -1 : 1;    // 同分時新的在前
      return a.it.s - b.it.s;                // 同一集內依時間先後
    });
  } else {
    // 「相關度」在沒有查詢字時等同於最新優先
    const asc = state.sort === 'old';
    out.sort((a, b) => {
      const A = EPS[a.it.e].d, B = EPS[b.it.e].d;
      if (A !== B) return asc === (A < B) ? -1 : 1;
      return a.it.s - b.it.s;          // 同一集內依時間先後
    });
  }

  view = out;
  shown = 0;
  render(true);
}

/* ----------------------------------------------------------------- 渲染 */

function highlight(text, nq) {
  if (!nq) return esc(text);
  const { n, map } = normalize(text, true);
  const i = n.indexOf(nq);
  if (i < 0) return esc(text);
  const a = map[i];
  const b = map[i + nq.length - 1] + 1;
  return esc(text.slice(0, a)) + '<mark>' + esc(text.slice(a, b)) + '</mark>' + esc(text.slice(b));
}

function render(reset) {
  if (reset) {
    el.results.innerHTML = '';
    if (playing) playing = null;
  }

  const nq = normalize(state.q);
  const frag = document.createDocumentFragment();
  const end = Math.min(shown + PAGE, view.length);

  for (let i = shown; i < end; i++) {
    const { it } = view[i];
    const ep = EPS[it.e];
    const node = el.tpl.content.firstElementChild.cloneNode(true);
    const key = `${ep.i}@${it.s}`;
    node.dataset.key = key;
    node.dataset.idx = String(i);
    if (it.whole) node.classList.add('whole');

    node.querySelector('.title').innerHTML = highlight(it.x, nq);

    const epLabel = it.whole
      ? (ep.n ? `${ep.n} 整集` : '整集')
      : `${ep.n ? ep.n + '　' : ''}${ep.t}`;
    node.querySelector('.ep').innerHTML = highlight(epLabel, it.whole ? '' : nq);
    const time = node.querySelector('.date');
    time.textContent = ymd(ep.d);
    time.dateTime = ep.d ? `${ep.d.slice(0, 4)}-${ep.d.slice(4, 6)}-${ep.d.slice(6)}` : '';
    // 整集列：如果這集有分段，就告訴使用者裡面有幾則，點下去會展開
    node.querySelector('.ts').textContent = it.whole
      ? (ep.c.length ? `共 ${ep.c.length} 則` : (ep.s ? `全長 ${hms(ep.s)}` : ''))
      : hms(it.s);
    node.querySelector('.yt').href = linkFor(it);

    if (ep.r) {
      const b = document.createElement('span');
      b.className = 'badge';
      b.textContent = '年齡限制';
      b.title = '這集是限制級，需要登入 YouTube 且滿 18 歲才能播放';
      node.querySelector('.sub').appendChild(b);
    }

    frag.appendChild(node);
  }

  el.results.appendChild(frag);
  shown = end;
  el.more.hidden = shown >= view.length;
  el.more.textContent = `載入更多（還有 ${view.length - shown} 則）`;

  // 只看某一集時的提示列
  const scope = document.getElementById('scope');
  if (state.ep) {
    const ep = EPS.find((x) => x.i === state.ep);
    scope.innerHTML = `<span class="txt">只看這一集：<span class="name"></span></span>
      <button type="button" id="scope-clear">看全部</button>`;
    scope.querySelector('.name').textContent =
      `${ep.n ? ep.n + '　' : ''}${ep.t}`;
    scope.hidden = false;
    el.count.textContent = `這一集共 ${view.length} 則`;
    el.empty.hidden = true;
    return;
  }
  scope.hidden = true;

  // 統計列。故事數與集數要算同一批項目，不然會出現「16 則故事來自 17 集」
  const stories = view.filter((v) => !v.it.whole);
  const epsHit = new Set(stories.map((v) => v.it.e)).size;
  const wholeHits = view.length - stories.length;
  if (state.q) {
    const parts = [];
    if (stories.length) parts.push(`找到 ${stories.length.toLocaleString()} 則故事，來自 ${epsHit} 集`);
    if (wholeHits) parts.push(`${wholeHits} 集的標題符合`);
    el.count.textContent = parts.join('　·　');
  } else {
    el.count.textContent =
      `共 ${view.length.toLocaleString()} 則，來自 ${new Set(view.map((v) => v.it.e)).size} 集`;
  }

  const none = view.length === 0;
  el.empty.hidden = !none;
  if (none) {
    el.empty.innerHTML = `<strong>找不到「${esc(state.q)}」</strong>
      <div class="hint">試試更短的關鍵字，或改用故事裡會出現的詞。<br>
      提醒：約四分之一的集數，說明欄沒有寫分段，那些集數只能搜到標題。</div>`;
  }
}

/* ------------------------------------------------------------- 播放與連結 */

function play(node, item) {
  document.querySelectorAll('.row.is-playing').forEach((n) => n.classList.remove('is-playing'));
  document.querySelectorAll('.player').forEach((n) => n.remove());

  const ep = EPS[item.e];
  const t = Math.max(0, item.s | 0);
  const box = document.createElement('div');
  box.className = 'player';
  const f = document.createElement('iframe');
  f.src = `https://www.youtube-nocookie.com/embed/${ep.i}?start=${t}&autoplay=1&rel=0`;
  f.title = item.x;
  f.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; picture-in-picture';
  f.allowFullscreen = true;
  f.referrerPolicy = 'strict-origin-when-cross-origin';
  box.appendChild(f);

  node.classList.add('is-playing');
  node.after(box);
  playing = { key: node.dataset.key, node };

  const u = new URL(location.href);
  u.searchParams.set('v', ep.i);
  if (t) u.searchParams.set('t', String(t)); else u.searchParams.delete('t');
  history.replaceState(null, '', u);
}

el.results.addEventListener('click', (e) => {
  const node = e.target.closest('.row');
  if (!node) return;
  const item = view[Number(node.dataset.idx)]?.it;
  if (!item) return;

  if (e.target.closest('.play')) {
    if (playing && playing.key === node.dataset.key) {
      document.querySelectorAll('.player').forEach((n) => n.remove());
      node.classList.remove('is-playing');
      playing = null;
    } else {
      play(node, item);
    }
    return;
  }
  if (e.target.closest('.copy')) {
    copy(linkFor(item), e.target, '連結已複製');
    return;
  }
  if (e.target.closest('.copytext')) {
    copy(`${item.x} — ${linkFor(item)}`, e.target, '標題與連結已複製');
    return;
  }
  if (e.target.closest('.ep')) {
    setScope(EPS[item.e].i);
    return;
  }
  if (e.target.closest('.yt')) return;   // 讓連結正常開啟

  // 點在卡片其他地方 = 播放。
  // 但如果這是「整集」列、而且該集有分段，從 0:00 播一小時的長集沒什麼用，
  // 展開這集的信件清單才是使用者要的。
  if (item.whole && EPS[item.e].c.length && !state.ep) {
    setScope(EPS[item.e].i);
    return;
  }
  play(node, item);
});

/* ------------------------------------------------------------- 狀態與輸入 */

function syncURL() {
  const u = new URL(location.href);
  state.q ? u.searchParams.set('q', state.q) : u.searchParams.delete('q');
  state.year ? u.searchParams.set('y', String(state.year)) : u.searchParams.delete('y');
  state.sort !== 'rel' ? u.searchParams.set('sort', state.sort) : u.searchParams.delete('sort');
  state.ep ? u.searchParams.set('e', state.ep) : u.searchParams.delete('e');
  u.searchParams.delete('v');
  u.searchParams.delete('t');
  history.replaceState(null, '', u);
}

/** 切換成「只看某一集」，或傳空字串回到全部結果。 */
function setScope(id) {
  state.ep = id || '';
  syncURL();
  search();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

document.getElementById('scope').addEventListener('click', (e) => {
  if (e.target.closest('#scope-clear')) setScope('');
});

function setQuery(q, focus) {
  state.q = q;
  state.ep = '';          // 重新搜尋就離開「只看這一集」
  el.q.value = q;
  el.clear.hidden = !q;
  syncURL();
  search();
  if (focus) el.q.focus();
}

el.q.addEventListener('input', () => {
  state.q = el.q.value;
  state.ep = '';
  el.clear.hidden = !state.q;
  syncURL();
  search();
});

el.clear.addEventListener('click', () => setQuery('', true));

el.suggest.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-q]');
  if (b) setQuery(b.dataset.q, false);
  if (e.target.closest('#random')) randomStory();
});

el.sort.addEventListener('change', () => {
  state.sort = el.sort.value;
  syncURL();
  search();
});

el.more.addEventListener('click', () => render(false));

document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== el.q) {
    e.preventDefault();
    el.q.focus();
    el.q.select();
  } else if (e.key === 'Escape' && document.activeElement === el.q) {
    setQuery('', true);
  }
});

function randomStory() {
  const pool = ITEMS.filter((it) => !it.whole && !it.b);
  if (!pool.length) return;
  const pick = pool[Math.floor(Math.random() * pool.length)];

  // 先清掉年份篩選，否則抽到的故事可能被濾掉，按鈕就變成沒反應
  state.year = 0;
  el.years.querySelectorAll('button').forEach((x) =>
    x.setAttribute('aria-pressed', String(x.dataset.year === '0')));
  setQuery('', false);

  const i = view.findIndex((v) => v.it === pick);
  if (i < 0) return;
  while (shown <= i && shown < view.length) render(false);
  const node = el.results.querySelector(`.row[data-idx="${i}"]`);
  if (node) {
    node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    play(node, pick);
  }
}

/* --------------------------------------------------------------- 初始化 */

function buildYears() {
  const years = [...new Set(EPS.map((e) => e.y).filter(Boolean))].sort((a, b) => b - a);
  const mk = (val, label) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    b.dataset.year = String(val);
    b.setAttribute('aria-pressed', String(state.year === val));
    return b;
  };
  el.years.appendChild(mk(0, '全部年份'));
  years.forEach((y) => el.years.appendChild(mk(y, String(y))));

  el.years.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-year]');
    if (!b) return;
    state.year = Number(b.dataset.year);
    state.ep = '';
    el.years.querySelectorAll('button').forEach((x) =>
      x.setAttribute('aria-pressed', String(Number(x.dataset.year) === state.year)));
    syncURL();
    search();
  });
}

async function init() {
  let data;
  try {
    // no-cache 是「一定要跟伺服器確認」，沒變就回 304，不會重新下載。
    // 每週更新索引後，回訪的人才不會看到舊資料。
    const res = await fetch('data/index.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error(res.status);
    data = await res.json();
  } catch (err) {
    el.tagline.textContent = '資料載入失敗，請重新整理試試。';
    console.error(err);
    return;
  }

  EPS = data.eps;
  // t2s 是兩條平行字串。用 Array.from 依 code point 拆，非 BMP 的字才不會錯位。
  const [tradStr, simpStr] = data.t2s || ['', ''];
  const trad = Array.from(tradStr), simp = Array.from(simpStr);
  for (let i = 0; i < trad.length; i++) T2S.set(trad[i], simp[i]);

  // 攤平成可搜尋項目：每個章節一則，另外每集本身也是一則
  ITEMS = [];
  for (let e = 0; e < EPS.length; e++) {
    const ep = EPS[e];
    ITEMS.push({ e, s: 0, x: ep.t, b: 0, whole: true, k: normalize(ep.t) });
    for (const [start, title, bp] of ep.c) {
      ITEMS.push({ e, s: start, x: title, b: bp, whole: false, k: normalize(title) });
    }
  }

  const nWithCh = EPS.filter((e) => e.c.length).length;
  el.tagline.textContent = '超過千集上萬則故事，點一下就可以收聽';
  el.footmeta.textContent =
    `收錄至 ${ymd(data.latest)}　·　${nWithCh}/${EPS.length} 集有分段時間戳`;

  // 加一顆隨機按鈕
  const rnd = document.createElement('button');
  rnd.id = 'random';
  rnd.type = 'button';
  rnd.textContent = '🎲 隨機一則';
  el.suggest.appendChild(rnd);

  // 還原網址帶進來的狀態
  const p = new URLSearchParams(location.search);
  state.q = p.get('q') || '';
  state.year = Number(p.get('y')) || 0;
  state.sort = ['rel', 'new', 'old'].includes(p.get('sort')) ? p.get('sort') : 'rel';
  state.ep = p.get('e') || '';
  el.q.value = state.q;
  el.clear.hidden = !state.q;
  el.sort.value = state.sort;

  buildYears();
  el.filters.hidden = false;
  search();

  // 網址若指定了影片與時間，直接開起來
  const v = p.get('v');
  if (v) {
    const t = Number(p.get('t')) || 0;
    const i = view.findIndex((x) => EPS[x.it.e].i === v && x.it.s === t);
    if (i >= 0) {
      while (shown <= i && shown < view.length) render(false);
      const node = el.results.querySelector(`.row[data-idx="${i}"]`);
      if (node) {
        play(node, view[i].it);
        node.scrollIntoView({ block: 'center' });
      }
    }
  }

  if (!state.q) el.q.focus({ preventScroll: true });
}

init();
