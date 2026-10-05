/**
 * stats —— 全站统计页（/statistics/，仅该页随 stats-data.js 注入）。
 *
 * 访客向数据面板：hero 总览（数字滚动）→ 热读文献 Top 5（全量口径）→
 * 筛选（期刊/类型/年份区间）联动重算：Online 发表动态面积图（SVG）· 文章类型环形图
 * （SVG）· 期刊条形图 · 研究热词词云（关键词 / 标题·摘要 两个 tab）。
 * 手写 SVG + vanilla JS，无第三方库；数据 window.MBD_STATS 只含英文原文与分面字段。
 */
(function () {
  const DATA = window.MBD_STATS || [];
  const grid = document.getElementById("st-grid");
  if (!grid) return;

  const el = (id) => document.getElementById(id);
  /* HTML 转义：期刊名带 &（Nature Ecology & Evolution）、体裁/关键词为可编辑文本，
     进 innerHTML 前必须转义文本与属性两处 */
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  /* 停用词：纯功能词 + 少量学术高频填充词（study/results 之类），
     内容词不剪 —— 词频统计的是标题与摘要的英文原文 */
  const STOP = new Set(("the of and in to a is that for on with as by at from this these those it its "
    + "are be was were we our us their they them he she his her you your i me my "
    + "an or not but can may has have had than into during under over between across "
    + "more most other such which who whom what when where how all also both each few "
    + "some no nor only own same so too very s t d ll re ve been being do does did done "
    + "will would shall should could might must about after before while whilst thus "
    + "there here then when why because upon among via et al using used use based shown "
    + "show shows suggest suggests revealed reveal results result study studies paper "
    + "data analysis methods method approach approaches different between").split(" "));

  const LOCAL = location.hostname.match(/^(localhost|127\.0\.0\.1|)$/);
  const API = (window.MBD && window.MBD.api) || "";
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const state = { j: "*", t: "*", from: "", to: "" };

  /* 分类调色板（暗色亮色各一档，与站点 token 同源）：环形图 / hero 顶条 / 图例共用 */
  const PAL = [["#58a6ff", "#0969da"], ["#3fb950", "#1a7f37"], ["#a371f7", "#8250df"],
    ["#f0883e", "#bc4c00"], ["#db61a2", "#a2306e"], ["#39c5cf", "#0a7c84"]];
  const isLight = () => document.documentElement.getAttribute("data-theme") === "light";
  const col = (i) => PAL[i % PAL.length][isLight() ? 1 : 0];

  /* ── 数字滚动：hero 总览 tile（reduced-motion 直接落值）── */
  function countUp(node, to) {
    if (!REDUCED) {
      const t0 = performance.now();
      const step = (t) => {
        const k = Math.min(1, (t - t0) / 700);
        node.textContent = Math.round(to * (1 - Math.pow(1 - k, 3)));
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    } else node.textContent = to;
  }

  /* ── 筛选控件：选项来自全量数据 ── */
  function fillSelect(sel, values, label) {
    /* label 由筛选行的 .flabel 承担，首项只写「全部」 */
    sel.innerHTML = `<option value="*">${esc(label)}</option>` +
      values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  }
  fillSelect(el("st-journal"), [...new Set(DATA.map((d) => d.j).filter(Boolean))].sort(), "全部");
  fillSelect(el("st-type"), [...new Set(DATA.map((d) => d.at).filter(Boolean))].sort(), "全部");

  function filtered() {
    /* 时间筛选按 online 发表日期（po）做年月日比较。po 有月精度的（YYYY-MM，
       Crossref 只给到月）按整月区间 [月初, 月底] 与筛选区间取重叠 ——
       字符串比较即可，"-31" 只是月内上界、不校验真实历法 */
    return DATA.filter((d) => {
      if (state.j !== "*" && d.j !== state.j) return false;
      if (state.t !== "*" && d.at !== state.t) return false;
      if (state.from || state.to) {
        const v = d.po || "";
        if (!v) return false;                     // 无发表日期：筛选激活时隐藏
        const ps = v.length === 7 ? v + "-01" : v;
        const pe = v.length === 7 ? v + "-31" : v;
        if (state.from && pe < state.from) return false;
        if (state.to && ps > state.to) return false;
      }
      return true;
    });
  }

  /* ── 聚合 ── */
  function countBy(list, get, topN) {
    const c = {};
    list.forEach((d) => {
      const v = get(d);
      if (!v) return;
      if (Array.isArray(v)) v.forEach((x) => { if (x) c[x] = (c[x] || 0) + 1; });
      else c[v] = (c[v] || 0) + 1;
    });
    const rows = Object.keys(c).map((k) => ({ k, n: c[k] }))
      .sort((a, b) => b.n - a.n || a.k.localeCompare(b.k));
    return topN ? rows.slice(0, topN) : rows;
  }

  const wordRe = /[^a-z]+/;
  function wordFreq(list, topN) {
    const c = {};
    list.forEach((d) => {
      ((d.t || "") + " " + (d.ab || "")).toLowerCase().split(wordRe).forEach((w) => {
        if (w.length < 3 || STOP.has(w)) return;
        c[w] = (c[w] || 0) + 1;
      });
    });
    return Object.keys(c).map((k) => ({ k, n: c[k] }))
      .sort((a, b) => b.n - a.n || a.k.localeCompare(b.k)).slice(0, topN || 15);
  }

  /* ── 条形图：细轨 + 进场生长动画（innerHTML 重建即触发）── */
  function renderBars(box, rows) {
    box.innerHTML = "";
    if (!rows.length) { box.innerHTML = '<p class="st-none">无数据</p>'; return; }
    const max = Math.max(...rows.map((r) => r.n));
    box.innerHTML = rows.map((r) => {
      const w = Math.max(2, Math.round((r.n / max) * 100));
      return `<div class="st-row"><span class="st-k" title="${esc(r.k)}">${esc(r.k)}</span>`
        + `<span class="st-bar"><i style="width:${w}%"></i></span>`
        + `<span class="st-n">${r.n}</span></div>`;
    }).join("");
  }

  /* ── 环形图：stroke-dasharray 段（r=15.9155 → 周长恰为 100，直接用百分比），
     中心合计，段与图例双向联动高亮。nTypes = 全部类目数（合并「其他」后
     rows.length 会少计，中心「N 类」标注用真实类目数） ── */
  function renderDonut(box, rows, nTypes) {
    box.innerHTML = "";
    if (!rows.length) { box.innerHTML = '<p class="st-none">无数据</p>'; return; }
    const total = rows.reduce((s, r) => s + r.n, 0);
    let acc = 0;
    const segs = rows.map((r, i) => {
      const pct = (r.n / total) * 100;
      const off = 25 - acc;               // 25 = 把起点从 3 点钟转到 12 点钟
      acc += pct;
      return `<circle class="seg" data-i="${i}" cx="18" cy="18" r="15.9155"`
        + ` style="stroke:${col(i)};stroke-dasharray:${pct} ${100 - pct};stroke-dashoffset:${off}"`
        + `><title>${esc(r.k)} · ${r.n} 篇（${Math.round(pct)}%）</title></circle>`;
    }).join("");
    const legend = rows.map((r, i) =>
      `<div class="st-lg" data-i="${i}" style="--lc:${col(i)}"><i></i>`
      + `<span title="${esc(r.k)}">${esc(r.k)}</span><b>${r.n}</b><em>${Math.round(r.n / total * 100)}%</em></div>`
    ).join("");
    box.innerHTML = `<svg viewBox="0 0 36 36" role="img" aria-label="文章类型构成">`
      + `<circle cx="18" cy="18" r="15.9155" style="fill:none;stroke:var(--line);stroke-width:3.6"/>`
      + segs
      + `<text x="18" y="16.6" text-anchor="middle" class="don-v">${total}</text>`
      + `<text x="18" y="21.2" text-anchor="middle" class="don-k">篇 · ${nTypes || rows.length} 类</text></svg>`
      + `<div class="st-legend">${legend}</div>`;
    const segEls = [...box.querySelectorAll(".seg")];
    const lgEls = [...box.querySelectorAll(".st-lg")];
    const hl = (on) => {
      segEls.forEach((s, j) => {
        s.classList.toggle("off", on >= 0 && j !== on);
        s.classList.toggle("big", j === on);
      });
      lgEls.forEach((l, j) => l.classList.toggle("hl", j === on));
    };
    segEls.concat(lgEls).forEach((s) => {
      const i = +s.dataset.i;
      s.addEventListener("mouseenter", () => hl(i));
      s.addEventListener("mouseleave", () => hl(-1));
    });
  }

  /* ── Online 发表动态面积图：按文献 online 发表日期（d.po）逐日累计；
     SVG 按容器实测像素构建（文字不变形），resize 防抖重绘；
     悬停显示「日期 · 累计 N 篇」── */
  function renderGrowth(box, list) {
    /* 日期归一：部分文献只有年月（2026-03）甚至只有年 —— 补成该月/当年首日，
       否则 new Date("2026-03T00:00:00Z") 是 Invalid Date 会直接炸掉图表 */
    const norm = (s) => s.length === 7 ? s + "-01" : s.length === 4 ? s + "-01-01" : s;
    const byDay = {};
    list.forEach((d) => {
      if (!d.po) return;
      const k = norm(d.po);
      if (isNaN(new Date(k + "T00:00:00Z"))) return;   // 脏日期兜底：跳过不进图
      byDay[k] = (byDay[k] || 0) + 1;
    });
    const days = Object.keys(byDay).sort();
    if (!days.length) { box.innerHTML = '<p class="st-none">无发表日期数据</p>'; return; }
    const t0 = new Date(days[0] + "T00:00:00Z");
    const N = Math.max(1, Math.round((new Date(days[days.length - 1] + "T00:00:00Z") - t0) / 864e5));
    const W = Math.max(320, box.clientWidth || 640), H = 200;
    const pl = 40, pr = 16, pt = 16, pb = 28;
    const pw = W - pl - pr, ph = H - pt - pb;
    let cum = 0;
    const pts = [];
    for (let i = 0; i <= N; i++) {
      const k = new Date(+t0 + i * 864e5).toISOString().slice(0, 10);
      cum += byDay[k] || 0;
      pts.push([k, cum, byDay[k] || 0]);
    }
    const max = pts[pts.length - 1][1];
    const X = (i) => pl + (i / N) * pw;
    const Y = (v) => pt + (1 - (max ? v / max : 0)) * ph;
    const line = pts.map((p, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(p[1]).toFixed(1)).join(" ");
    const base = (pt + ph).toFixed(1);
    const area = `${line} L${X(N).toFixed(1)} ${base} L${X(0).toFixed(1)} ${base} Z`;
    /* 网格：0 / 半值 / 峰值 三条 */
    const ticks = [...new Set([0, Math.round(max / 2), max])];
    const gridSvg = ticks.map((v) =>
      `<line class="gl" x1="${pl}" y1="${Y(v).toFixed(1)}" x2="${W - pr}" y2="${Y(v).toFixed(1)}"/>`
      + `<text class="gt" x="${pl - 7}" y="${(Y(v) + 3.5).toFixed(1)}" text-anchor="end">${v}</text>`).join("");
    const mid = Math.round(N / 2);
    /* x 轴刻度：首/尾/中三档（端点优先）；索引重复（N≤1 时 mid 撞端点）或
       中点与两端间距 <44px 时丢弃，跨度为 0 只画一枚 */
    const seenI = new Set();
    const xlab = (N === 0 ? [[0, "middle"]] : [[0, "start"], [N, "end"], [mid, "middle"]]
        .filter(([i]) => {
          if (seenI.has(i)) return false;
          if (i !== 0 && i !== N && (X(i) - X(0) < 44 || X(N) - X(i) < 44)) return false;
          seenI.add(i);
          return true;
        }))
      .map(([i, a]) =>
        `<text class="gt" x="${X(i).toFixed(1)}" y="${H - 8}" text-anchor="${a}">${pts[i][0].slice(5)}</text>`).join("");
    /* 数据点：只有真实入库的日期出点；hit 大圆承担 hover */
    const marks = pts.map((p, i) => ({ x: X(i), y: Y(p[1]), k: p[0], n: p[1], add: p[2] }))
      .filter((p) => p.add > 0);
    const dotsSvg = marks.map((p, i) =>
      `<circle class="dotc" data-i="${i}" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3"/>`
      + `<circle class="hit" data-i="${i}" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="12"/>`).join("");
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}"><defs><linearGradient id="stgg" x1="0" y1="0" x2="0" y2="1">`
      + `<stop offset="0" style="stop-color:var(--accent);stop-opacity:.26"/>`
      + `<stop offset="1" style="stop-color:var(--accent);stop-opacity:0"/></linearGradient></defs>`
      + gridSvg
      + `<path d="${area}" style="fill:url(#stgg);stroke:none"/>`
      + `<path d="${line}" style="fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round;stroke-linecap:round"/>`
      + dotsSvg + xlab + `</svg><div class="st-gtip"></div>`;
    const tip = box.querySelector(".st-gtip");
    const dots = [...box.querySelectorAll(".dotc")];
    box.querySelectorAll(".hit").forEach((h) => {
      const i = +h.dataset.i;
      h.addEventListener("mouseenter", () => {
        const p = marks[i];
        const b = box.getBoundingClientRect();
        const r = h.getBoundingClientRect();
        const x = Math.max(46, Math.min(b.width - 46, r.left + r.width / 2 - b.left));
        tip.innerHTML = `<b>${p.k.slice(5)}</b> · 累计 ${p.n} 篇（+${p.add}）`;
        tip.style.left = x + "px";
        tip.style.top = (r.top - b.top) + "px";
        tip.style.opacity = 1;
        if (dots[i]) dots[i].style.fill = "var(--accent)";
      });
      h.addEventListener("mouseleave", () => {
        tip.style.opacity = 0;
        if (dots[i]) dots[i].style.fill = "";
      });
    });
  }

  /* ── 词云 v2：经典螺旋布局 —— 首词落中心，其余沿阿基米德螺旋外溢，
     用 DOM 实测包围盒做精确碰撞（不重叠不稀疏）；字号按频次对数映射 11–34px，
     颜色按名次分档（accent → 绿 → 紫 → 橙 → 灰阶），中后段长词偶发竖排；
     布局完全确定性 —— 同样的数据永远同样的形状（主题切换/筛选重绘不跳变）；
     椭圆系数按容器实测宽高归一，宽卡铺满不留大片空白；
     hidden 卡（display:none 无法测量）先临时展开为不可见，渲染完复原 ── */
  function unhideBox(box) {
    if (!box.hidden) return () => {};
    box.style.visibility = "hidden";
    box.style.display = "block";
    return () => { box.style.display = ""; box.style.visibility = ""; };
  }

  function renderCloud(box, rows) {
    const restore = unhideBox(box);
    box.innerHTML = "";
    if (!rows.length) { box.innerHTML = '<p class="st-none">无数据</p>'; restore(); return; }
    const W = Math.max(280, box.clientWidth), H = Math.max(200, box.clientHeight);
    const small = W < 480;
    const data = rows.slice(0, small ? 16 : 32);
    const max = data[0].n, min = data[data.length - 1].n;
    const lr = Math.log(max / Math.max(1, min));            // 频次对数跨度
    const cx = W / 2, cy = H / 2;
    const TMAX = 620;
    const ax = (W / 2 - 6) / (5.5 * Math.sqrt(TMAX));       // 螺旋半径 → 容器宽映射
    const ay = (H / 2 - 6) / (5.5 * Math.sqrt(TMAX));
    const placed = [];
    const PAD = 3;
    const hits = (x, y, w, h) => placed.some((b) =>
      x < b.x + b.w + PAD && x + w > b.x - PAD && y < b.y + b.h + PAD && y + h > b.y - PAD);
    const tryPlace = (bw, bh) => {
      for (let t = 0; t < TMAX; t++) {
        const rad = 5.5 * Math.sqrt(t);
        const a = -Math.PI / 2 + t * 0.105;
        const x = cx + Math.cos(a) * rad * ax - bw / 2;
        const y = cy + Math.sin(a) * rad * ay - bh / 2;
        if (x < 2 || y < 2 || x + bw > W - 2 || y + bh > H - 2) continue;
        if (!hits(x, y, bw, bh)) return [x, y];
      }
      return null;
    };
    /* 颜色/透明度按名次分档：头两词主色、其后绿→紫→橙、长尾灰阶垫底 */
    const TCOL = [col(0), col(1), col(2), col(3), "var(--faint)"];
    const TOP = [1, .92, .85, .8, .72];
    const tier = (i) => i < 2 ? 0 : i < 6 ? 1 : i < 12 ? 2 : i < 19 ? 3 : 4;
    const frag = document.createDocumentFragment();
    data.forEach((r, i) => {
      const k = lr > 0 ? Math.log(r.n / Math.max(1, min)) / lr : 1;
      const size = Math.round(11 + k * (small ? 17 : 23));  // 11–34px
      const s = document.createElement("span");
      s.className = "st-w";
      s.textContent = r.k;
      s.title = r.k + " × " + r.n;
      s.style.fontSize = size + "px";
      s.style.color = TCOL[tier(i)];
      s.style.opacity = TOP[tier(i)];
      s.style.animationDelay = Math.min(480, i * 18) + "ms";
      frag.appendChild(s);
    });
    box.appendChild(frag);                                  // 入 DOM 才有真实包围盒
    const els = [...box.querySelectorAll(".st-w")];
    /* 面积守恒：词总面积超过容器可用面积（50%）时整体等比缩小字号 ——
       中屏/小屏也能放下全部词（层级关系不变；缩后逐词重测无需重排档位） */
    let area = 0;
    els.forEach((s) => { area += s.offsetWidth * s.offsetHeight; });
    const usable = W * H * 0.5;
    if (area > usable) {
      const f = Math.max(0.55, Math.sqrt(usable / area));   // 最多缩到 55% 防过小
      els.forEach((s) => s.style.fontSize = Math.max(9, parseFloat(s.style.fontSize) * f) + "px");
    }
    let fb = 0;                                             // 兜底堆叠行号
    els.forEach((s, i) => {
      let w = s.offsetWidth, h = s.offsetHeight;
      const vert = !small && i >= 8 && i % 6 === 4 && w > h * 2.2;   // 长词偶发竖排
      if (vert) s.classList.add("st-wv");
      let bw = vert ? h : w, bh = vert ? w : h;             // 旋转后包围盒
      let pos = tryPlace(bw, bh);
      if (!pos) {                                           // 放不下 → 缩 15% 再试
        s.style.fontSize = parseFloat(s.style.fontSize) * 0.85 + "px";
        w = s.offsetWidth; h = s.offsetHeight;
        bw = vert ? h : w; bh = vert ? w : h;
        pos = tryPlace(bw, bh);
      }
      if (pos) {                                            // left/top 取旋转前盒的左上角
        s.style.left = (pos[0] + (bw - w) / 2) + "px";
        s.style.top = (pos[1] + (bh - h) / 2) + "px";
        placed.push({ x: pos[0], y: pos[1], w: bw, h: bh });
      } else {
        s.style.left = "2px";
        s.style.top = (2 + fb * 20) + "px";                 // 兜底：左侧纵向排开不互叠
        placed.push({ x: 2, y: 2 + fb * 20, w: bw, h: bh });
        fb++;
      }
    });
    restore();
  }

  function render() {
    const list = filtered();
    el("st-count").textContent = `命中 ${list.length} / ${DATA.length} 篇`;
    el("st-empty").hidden = list.length > 0;
    grid.style.visibility = list.length ? "visible" : "hidden";

    renderGrowth(el("st-growth"), list);
    /* 环形图画前 5 类、其余合并为「其他」（调色板 6 色，第 6 色给「其他」）——
       中心合计恢复全量口径，与 hero 的「收录文献」一致；
       此前 topN=6 直接截断，第 7 类起的文献不进扇区也不进合计（102≠103 的根因） */
    const typeRows = countBy(list, (d) => d.at);
    const donutRows = typeRows.slice(0, 5);
    const restN = typeRows.slice(5).reduce((s, r) => s + r.n, 0);
    if (restN > 0) donutRows.push({ k: "其他", n: restN });
    renderDonut(el("st-types"), donutRows, typeRows.length);
    renderBars(el("st-journals"), countBy(list, (d) => d.j, 10));
    renderCloud(el("st-kwc"), countBy(list, (d) => d.kw, 34));
    renderCloud(el("st-wc"), wordFreq(list, 34));
  }

  /* ── hero 总览（全量口径）── */
  countUp(el("stv-papers"), DATA.length);
  countUp(el("stv-journals"), new Set(DATA.map((d) => d.j).filter(Boolean)).size);
  countUp(el("stv-kw"), new Set(DATA.flatMap((d) => d.kw || []).filter(Boolean)).size);

  /* ── 热读文献 Top 5 + hero「文献被浏览」：一次 fetch 同时喂两处；
     全量口径不随筛选重算；只统计文献卡片页（排除 /weekly/ 与首页），
     浏览数相同按随机排序，只展示前 5 ── */
  const rankBox = el("st-rank");
  const viewsEl = el("stv-views");
  if (!LOCAL && API) {
    fetch(API + "/api/v1/stats/views?prefix=/macrobiodiv/")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d || !d.items) throw 0;
        const rows = d.items
          .filter((x) => /^\/macrobiodiv\/[^/]+\/$/.test(x.path))    // 两段以上 = 周报等，排除
          .map((x) => {
            const id = x.path.replace(/^\/macrobiodiv\//, "").replace(/\/$/, "");
            const meta = DATA.find((m) => m.id === id);
            return { id, n: x.views || 0, t: (meta && meta.t) || id,
              j: (meta && meta.j) || "", y: (meta && meta.y) || "", rnd: Math.random() };
          });
        countUp(viewsEl, rows.reduce((s, r) => s + r.n, 0));
        const top = rows.slice().sort((a, b) => b.n - a.n || a.rnd - b.rnd).slice(0, 5);
        if (!top.length) { rankBox.innerHTML = '<p class="st-none">暂无浏览数据</p>'; return; }
        const max = top[0].n || 1;
        rankBox.innerHTML = top.map((r, i) => {
          const medal = i === 0 ? " top" : i < 3 ? " pod" : "";
          const sub = [r.j || "期刊未知", r.y].filter(Boolean).join(" · ");
          return `<div class="st-rank${medal}" style="--w:${Math.max(6, Math.round((r.n / max) * 100))}%">`
            + `<span class="rk">${i + 1}</span>`
            + `<span class="tt"><a href="../${r.id}/" title="${esc(r.t)}">${esc(r.t)}</a>`
            + `<small>${esc(sub)}</small></span>`
            + `<span class="n">${r.n} 次</span></div>`;
        }).join("");
      })
      .catch(() => {
        viewsEl.textContent = "—";
        rankBox.innerHTML = '<p class="st-none">浏览数据获取失败（统计服务不可达）</p>';
      });
  } else {
    viewsEl.textContent = "—";
    viewsEl.title = "部署后按访客实际浏览计入";
    rankBox.innerHTML = '<p class="st-none">本地预览无浏览统计（部署后按访客实际浏览计入）</p>';
  }

  /* ── 词云 tab 切换（两个云都随筛选重渲染，切 tab 只是显隐）── */
  document.querySelectorAll(".st-tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".st-tab").forEach((x) => x.classList.toggle("on", x === b));
    el("st-kwc").hidden = b.dataset.tab !== "kwc";
    el("st-wc").hidden = b.dataset.tab !== "wc";
  }));

  /* ── 明暗主题切换后重绘（SVG 颜色构建时写死，换主题需重建）── */
  new MutationObserver(render).observe(document.documentElement,
    { attributes: true, attributeFilter: ["data-theme"] });

  /* ── 窗口尺寸变化：面积图与词云都按容器实测像素构建，统一防抖重算 ── */
  let rzT;
  window.addEventListener("resize", () => {
    clearTimeout(rzT);
    rzT = setTimeout(render, 180);
  });

  /* ── 筛选事件 ── */
  el("st-journal").addEventListener("change", (e) => { state.j = e.target.value; render(); });
  el("st-type").addEventListener("change", (e) => { state.t = e.target.value; render(); });
  el("st-from").addEventListener("input", (e) => { state.from = e.target.value.trim(); render(); });
  el("st-to").addEventListener("input", (e) => { state.to = e.target.value.trim(); render(); });
  el("st-reset").addEventListener("click", () => {
    state.j = "*"; state.t = "*"; state.from = ""; state.to = "";
    el("st-journal").value = "*"; el("st-type").value = "*";
    el("st-from").value = ""; el("st-to").value = "";
    render();
  });

  render();
})();
