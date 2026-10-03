/**
 * stats —— 全站统计页客户端聚合（/stats/，仅该页随 stats-data.js 注入）。
 *
 * 数据 window.MBD_STATS（build_site.build_stats_data 产出）：英文原文与分面字段，
 * 不含中文翻译、不含每周速递。筛选（期刊 / 类型 / 年份区间）→ 过滤 → 聚合 → 条形图
 * 重渲染；纯 CSS 条形，无第三方库。
 */
(function () {
  const DATA = window.MBD_STATS || [];
  const grid = document.getElementById("st-grid");
  if (!grid) return;

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

  const el = (id) => document.getElementById(id);
  /* HTML 转义：期刊名带 &（Nature Ecology & Evolution）、LLM 体裁标签为人工可编辑文本，
     进 innerHTML 前必须转义文本与属性两处 */
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const state = { j: "*", t: "*", from: "", to: "" };

  /* ── 筛选控件：选项来自全量数据 ── */
  function fillSelect(sel, values, label) {
    sel.innerHTML = `<option value="*">${label} 全部</option>` +
      values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  }
  const journals = [...new Set(DATA.map((d) => d.j).filter(Boolean))].sort();
  const types = [...new Set(DATA.map((d) => d.at).filter(Boolean))].sort();
  fillSelect(el("st-journal"), journals, "期刊");
  fillSelect(el("st-type"), types, "类型");

  function filtered() {
    return DATA.filter((d) =>
      (state.j === "*" || d.j === state.j) &&
      (state.t === "*" || d.at === state.t) &&
      (!state.from || (d.y && d.y >= state.from)) &&
      (!state.to || (d.y && d.y <= state.to)));
  }

  /* ── 聚合 ── */
  function countBy(list, get, topN, sortAsc) {
    const c = {};
    list.forEach((d) => {
      const v = get(d);
      if (!v) return;
      if (Array.isArray(v)) v.forEach((x) => { if (x) c[x] = (c[x] || 0) + 1; });
      else c[v] = (c[v] || 0) + 1;
    });
    const rows = Object.keys(c).map((k) => ({ k, n: c[k] }));
    rows.sort((a, b) => (sortAsc ? a.k.localeCompare(b.k) : b.n - a.n || a.k.localeCompare(b.k)));
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

  /* ── 渲染：纯 CSS 条形（宽度 = 占该图最大值的比例） ── */
  function renderBars(box, rows) {
    box.innerHTML = "";
    if (!rows.length) {
      box.innerHTML = '<p class="st-none">无数据</p>';
      return;
    }
    const max = Math.max(...rows.map((r) => r.n));
    box.innerHTML = rows.map((r) => {
      const w = Math.max(2, Math.round((r.n / max) * 100));
      return `<div class="st-row"><span class="st-k" title="${esc(r.k)}">${esc(r.k)}</span>`
        + `<span class="st-bar"><i style="width:${w}%"></i></span>`
        + `<span class="st-n">${r.n}</span></div>`;
    }).join("");
  }

  /* ── 词云：字号 ∝ 频次（lo–hi 线性映射），透明度随频次衰减；不定位不旋转，流式换行 ── */
  function renderCloud(box, rows) {
    box.innerHTML = "";
    if (!rows.length) {
      box.innerHTML = '<p class="st-none">无数据</p>';
      return;
    }
    const max = Math.max(...rows.map((r) => r.n));
    const min = Math.min(...rows.map((r) => r.n));
    const lo = 12, hi = 34;
    const mid = Math.round((lo + hi) / 2);
    box.innerHTML = rows.map((r) => {
      const size = max === min ? mid : Math.round(hi - ((max - r.n) / (max - min)) * (hi - lo));
      const op = (0.55 + 0.45 * (r.n / max)).toFixed(2);
      return `<span class="st-w" style="font-size:${size}px;opacity:${op}" title="${esc(r.k)} × ${r.n}">${esc(r.k)}</span>`;
    }).join("");
  }

  function render() {
    const list = filtered();
    el("st-count").textContent = `命中 ${list.length} / ${DATA.length} 篇`;
    el("st-empty").hidden = list.length > 0;
    grid.style.visibility = list.length ? "visible" : "hidden";

    const years = countBy(list, (d) => d.y, 0, true);
    const span = years.length ? `${years[0].k}–${years[years.length - 1].k}` : "—";
    const nJ = new Set(list.map((d) => d.j).filter(Boolean)).size;
    const nKw = new Set(list.flatMap((d) => d.kw || []).filter(Boolean)).size;
    el("st-overview").textContent =
      `${list.length} 篇 · ${nJ} 本期刊 · 年份跨度 ${span} · 去重关键词 ${nKw} 个`;

    renderBars(el("st-years"), years);
    renderBars(el("st-journals"), countBy(list, (d) => d.j, 10));
    renderBars(el("st-types"), countBy(list, (d) => d.at, 10));
    renderBars(el("st-kw"), countBy(list, (d) => d.kw, 15));
    renderBars(el("st-au"), countBy(list, (d) => d.au, 10));
    renderBars(el("st-words"), wordFreq(list));
    renderCloud(el("st-kwc"), countBy(list, (d) => d.kw, 30));
    renderCloud(el("st-wc"), wordFreq(list, 30));
  }

  /* ── 被浏览排行 Top 5：全量口径，不随筛选重算；只统计文献卡片页（排除 /weekly/），
     浏览数相同按随机排序（每次访问都可能不同），只展示前 5 ── */
  const rankBox = el("st-rank");
  const API = (window.MBD && window.MBD.api) || "";
  const LOCAL = location.hostname.match(/^(localhost|127\.0\.0\.1|)$/);
  if (rankBox && !LOCAL && API) {
    fetch(API + "/api/v1/stats/views?prefix=/macrobiodiv/")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d || !d.items) throw 0;
        const rows = d.items
          .filter((x) => /^\/macrobiodiv\/[^/]+\/$/.test(x.path))    // 两段以上 = 周报等，排除
          .map((x) => {
            const id = x.path.replace(/^\/macrobiodiv\//, "").replace(/\/$/, "");
            const meta = DATA.find((m) => m.id === id);
            return { id, n: x.views || 0, t: (meta && meta.t) || id, rnd: Math.random() };
          })
          .sort((a, b) => b.n - a.n || a.rnd - b.rnd)                // 浏览数降序；同数随机
          .slice(0, 5);
        if (!rows.length) { rankBox.innerHTML = '<p class="st-none">暂无浏览数据</p>'; return; }
        rankBox.innerHTML = rows.map((r, i) =>
          `<div class="st-rank"><span class="rk">${i + 1}</span>`
          + `<a href="../${r.id}/" title="${esc(r.t)}">${esc(r.t)}</a>`
          + `<span class="n">${r.n} 次</span></div>`).join("");
      })
      .catch(() => { rankBox.innerHTML = '<p class="st-none">浏览数据获取失败（统计服务不可达）</p>'; });
  } else if (rankBox) {
    rankBox.innerHTML = '<p class="st-none">本地预览无浏览统计（部署后按访客实际浏览计入）</p>';
  }

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
