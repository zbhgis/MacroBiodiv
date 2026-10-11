(function () {
  var CFG = window.MBD, ITEMS = window.MBD_DATA || [], PAGE = window.MBD_PAGE || 30;
  if (!CFG) return;

  /* 统计打点：所有页面（首页 + 详情页）都要执行。之前放在网格逻辑之后，
     详情页因没有 #grid 提前 return，打点从未跑过（GeoSciPlot 踩过的坑） */
  if (CFG.tracker && !location.hostname.match(/^(localhost|127\.0\.0\.1|)$/)) {
    try {
      if (navigator.doNotTrack === "1") return;
      fetch(CFG.tracker, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path: "/macrobiodiv" + location.pathname, referrer: document.referrer || undefined }),
        keepalive: true,
      }).catch(function () {});
    } catch (e) {}
  }

  /* ── 每篇文献浏览量（来自统计服务的按 path 计数）。
     本地预览（localhost / 127.0.0.1 / file://）没有统计后端，请求注定失败，
     直接跳过不再发（消除控制台网络报错），与上方打点的 hostname 判断同一口径；
     占位「…」回写「—」，避免停留在加载中状态 ── */
  var viewsEl = document.getElementById("views");
  if (viewsEl && !location.hostname.match(/^(localhost|127\.0\.0\.1|)$/)) {
    fetch((CFG.api || "") + "/api/v1/stats/views?prefix=/macrobiodiv/")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.items) throw 0;
        var map = {};
        d.items.forEach(function (x) { map[x.path.replace(/\/$/, "")] = x.views; });
        var n = map["/macrobiodiv" + location.pathname.replace(/\/$/, "")] || 0;
        viewsEl.textContent = n ? n + " 次" : "首次";
        viewsEl.style.color = "var(--text)";
      }).catch(function () { viewsEl.textContent = "—"; });
  } else if (viewsEl) {
    viewsEl.textContent = "—";
  }

  /* ── 作者行两行折叠（仅详情页有 .p-auth / #authToggle）：
     超过两行才加 .auth-clamp 并亮出「展开全部 N 位作者」按钮；
     两行以内 / 无 JS = 完整展示，渐进增强 ── */
  var authEl = document.querySelector(".p-auth");
  var authBtn = document.getElementById("authToggle");
  if (authEl && authBtn) {
    var authLh = parseFloat(getComputedStyle(authEl).lineHeight) || 23;
    if (authEl.scrollHeight > authLh * 2 + 1) {
      authEl.classList.add("auth-clamp");
      var authN = authBtn.dataset.n;
      var authOpenTxt = authN ? "展开全部 " + authN + " 位作者" : "展开全部作者";
      authBtn.textContent = authOpenTxt;
      authBtn.classList.add("on");
      authBtn.addEventListener("click", function () {
        var opened = authEl.classList.toggle("open");
        authBtn.textContent = opened ? "收起作者" : authOpenTxt;
      });
    }
  }

  var themeBtn = document.getElementById("themeBtn");
  if (themeBtn) themeBtn.addEventListener("click", function () {
    var root = document.documentElement;
    // 默认暗色（不跟随系统偏好）；仅当访客手动切过才用其选择
    var light = root.getAttribute("data-theme") === "light";
    var t = light ? "dark" : "light";
    root.setAttribute("data-theme", t);
    try { localStorage.setItem("mbd-theme", t); } catch (e) {}
  });
  var topBtn = document.getElementById("topBtn");
  if (topBtn) {
    topBtn.addEventListener("click", function () { window.scrollTo({ top: 0, behavior: "smooth" }); });
    // 阈值 400px 与主站 zbhgis.com 的 .v3-rail-top 保持一致，两站行为统一
    var onScroll = function () { topBtn.classList.toggle("show", window.scrollY > 400); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ── 字号切换（FAB「A」按钮）：标准 / 放大两档，只覆盖 --fs-* 变量（菜单与大标题
     固定不动）；偏好存 localStorage mbd-fs，head 内联脚本已前置位防闪烁 ── */
  var fsBtn = document.getElementById("fsBtn");
  if (fsBtn) {
    var fsOn = false;
    try { fsOn = localStorage.getItem("mbd-fs") === "lg"; } catch (e) {}
    var syncFs = function () {
      if (fsOn) document.documentElement.setAttribute("data-fs", "lg");
      else document.documentElement.removeAttribute("data-fs");
      fsBtn.title = fsOn ? "字号：放大（点击还原）" : "字号：标准（点击放大）";
      fsBtn.classList.toggle("on", fsOn);
      try { localStorage.setItem("mbd-fs", fsOn ? "lg" : ""); } catch (e) {}
    };
    fsBtn.addEventListener("click", function () { fsOn = !fsOn; syncFs(); });
    syncFs();
  }

  /* ── 详情页：通用复制按钮（data-copy 指向源元素 id：引用条 / BibTeX） ── */
  var copyFallback = function (t) {
    var ta = document.createElement("textarea");
    ta.value = t;
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    ta.remove();
  };
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = document.getElementById(btn.getAttribute("data-copy"));
      var txt = src ? (src.textContent || "") : "";
      var old = btn.textContent;
      var done = function () {
        btn.textContent = "✓ 已复制";
        setTimeout(function () { btn.textContent = old; }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(done, function () { copyFallback(txt); done(); });
      } else { copyFallback(txt); done(); }
    });
  });

  /* ── 全站搜索独立页（/search/）：静态站没有检索后端，直接在 papers-data.js 的
     文献元数据 + weekly-data.js 的周报全文上做客户端匹配（文献：标题/作者/DOI/期刊/
     关键词/标签/摘要；周报：期标题/日期/摘要行/正文全文。多词空格分隔 = 同时命中）。
     放在网格逻辑之前 —— 搜索页没有 #grid 会提前 return ── */
  var spageQ = document.getElementById("spage-q");
  if (spageQ) {
    var sList = document.getElementById("spage-list");
    var sCount = document.getElementById("spage-count");
    var sUp = sList ? (sList.getAttribute("data-up") || "") : "";
    /* 周报数据（仅搜索页注入 weekly-data.js）：u 路径 / t 标题 / d 日期 /
       s 摘要行 / w 字数 / x 正文全文（构建期已截 30000 字符） */
    var WK = window.MBD_WK || [];
    WK.forEach(function (w) {
      w.se = (w.t + " " + (w.d || "") + " " + (w.s || "") + " " + (w.x || "")).toLowerCase();
    });

    function escHtml(s) {
      return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }
    function hl(text, tokens) {
      // 先转义再高亮：token 也过同一套转义，两边一致，特殊字符不会破坏 HTML 结构
      var out = escHtml(text);
      tokens.forEach(function (t) {
        var et = escHtml(t).replace(/[.*+?^${}()|[\]\\]/g, "$&");
        out = out.replace(new RegExp(et, "gi"), "<mark>$&</mark>");
      });
      return out;
    }
    /* 周报正文命中片段：在正文自身内找首个命中词，前后各取若干字符，省略号收边
       （注意不能在标题+日期+正文拼接串里找 —— 那样的下标对纯正文切片是错位的） */
    function wkSnip(w, tokens) {
      var text = w.x || "", low = text.toLowerCase();
      var pos = -1;
      for (var i = 0; i < tokens.length && pos < 0; i++) pos = low.indexOf(tokens[i]);
      if (pos < 0) return escHtml(text.slice(0, 120)) + (text.length > 120 ? "…" : "");
      var start = Math.max(0, pos - 50), end = Math.min(text.length, pos + 90);
      return (start > 0 ? "…" : "") + hl(text.slice(start, end), tokens) + (end < text.length ? "…" : "");
    }
    function paperRow(it, tokens) {
      var meta = [];
      if (it.a) meta.push(hl(it.a, tokens));
      if (it.j) meta.push(hl(it.j, tokens));
      if (it.y) meta.push(escHtml(it.y));
      if (it.doi) meta.push(hl(it.doi, tokens));
      // 主标题已是中文优先；此时把英文原题放进元信息行（可高亮），英文关键词搜索不失上下文
      if (it.tz && it.t === it.tz && it.te) meta.push(hl(it.te, tokens));
      return '<a class="sres" href="' + sUp + escHtml(it.id) + '/">'
        + '<span class="sres-body"><span class="sres-id">' + hl(it.t, tokens) + '</span>'
        + '<span class="sres-meta">' + meta.join('<i class="sres-sep">·</i>') + '</span></span></a>';
    }
    function wkRow(w, tokens) {
      var meta = [];
      if (w.d) meta.push(escHtml(w.d));
      if (w.s) meta.push(hl(w.s, tokens));
      if (w.w) meta.push(escHtml(w.w + " 字"));
      return '<a class="sres" href="' + sUp + escHtml(w.u) + '">'
        + '<span class="sres-body"><span class="sres-id">' + hl(w.t, tokens) + '</span>'
        + '<span class="sres-meta">' + meta.join('<i class="sres-sep">·</i>') + '</span>'
        + '<span class="sres-snip">' + wkSnip(w, tokens) + '</span></span></a>';
    }
    function renderSearch(raw) {
      var tokens = raw.trim().toLowerCase().split(/\s+/).filter(Boolean);
      if (!tokens.length) {
        sCount.textContent = "";
        sList.innerHTML = '<p class="spage-hint">输入 标题 / 作者 / DOI / 期刊 / 关键词 / 摘要 / 周报内容 开始检索；多个词用空格分隔（需同时命中）</p>';
        return;
      }
      var hits = ITEMS.filter(function (it) {
        var hay = (it.se || "").toLowerCase();
        return tokens.every(function (t) { return hay.indexOf(t) > -1; });
      });
      var wkHits = WK.filter(function (w) {
        return tokens.every(function (t) { return w.se.indexOf(t) > -1; });
      });
      sCount.textContent = "找到 " + hits.length + " / " + ITEMS.length + " 篇文献 · "
        + wkHits.length + " / " + WK.length + " 期周报";
      if (!hits.length && !wkHits.length) {
        sList.innerHTML = '<p class="spage-empty">未找到与 “' + escHtml(raw) + '” 相关的文献或周报</p>';
        return;
      }
      var html = hits.map(function (it) { return paperRow(it, tokens); }).join("");
      if (wkHits.length) {
        html += '<div class="spage-grouphd">每周速递</div>'
          + wkHits.map(function (w) { return wkRow(w, tokens); }).join("");
      }
      sList.innerHTML = html;
    }
    // ?q= 预填：与主站 /search?q= 行为一致，结果可分享
    var sq = "";
    try { sq = new URLSearchParams(location.search).get("q") || ""; } catch (e) {}
    spageQ.value = sq;
    renderSearch(sq);
    spageQ.addEventListener("input", function () {
      renderSearch(spageQ.value);
      try {
        var v = spageQ.value.trim();
        history.replaceState(null, "", v ? "?q=" + encodeURIComponent(v) : location.pathname);
      } catch (e) {}
    });
  }

  /* 详情页「返回全部」（a.back 只在详情页有）：优先回最近一次图库地址
     （URL 自带筛选/排序/页码，随机排序也不重排 —— GSP gsp-back 同源思路）；
     无记录（直链打开详情页）保持 ../ 兜底 */
  try {
    var backLink = document.querySelector("a.back");
    var backUrl = sessionStorage.getItem("mbd-back");
    if (backLink && backUrl) backLink.setAttribute("href", backUrl);
  } catch (e) {}

  var grid = document.getElementById("grid");
  if (!grid) return;

  /* ── 分页 + 筛选 + 排序 ── */
  var state = { q: "", tag: null, journal: null, from: "", to: "", sort: "pub", page: 1, per: PAGE };
  try {
    var savedPer = parseInt(localStorage.getItem("mbd-per"), 10);
    if ([20, 30, 50].indexOf(savedPer) > -1) state.per = savedPer;   // 仅接受合法档位，旧值自动回默认 30
    if (localStorage.getItem("mbd-sort2")) { state.sort = localStorage.getItem("mbd-sort2"); }
  } catch (e) {}
  reshuffle();       // 会话种子键：同会话内稳定，详情页返回 / 刷新 / 翻页不重排
  var q = document.getElementById("q");
  var empty = document.getElementById("empty");
  var info = document.getElementById("pageinfo");
  var prev = document.getElementById("prev");
  var next = document.getElementById("next");
  var pgnums = document.getElementById("pgnums");

  /* 视口越窄，页码窗口收得越小：
     ≥1000 → 当前页 ±2 且总页数 ≤9 时全列；≥640 → ±1；更窄 → 只列当前页（总数缩到 4 个节点） */
  function windowSize() {
    var w = window.innerWidth || 1024;
    return w < 640 ? 0 : (w < 1000 ? 1 : 2);
  }

  /* 页码节点用事件委托：一次绑定，翻页时只重建 innerHTML，不重新挂监听 */
  function paintNums(page, pages) {
    if (!pgnums) return;
    if (pages <= 1) { pgnums.innerHTML = ""; return; }
    var win = windowSize();
    var nums = [];
    for (var i = 1; i <= pages; i++) {
      if (i === 1 || i === pages || Math.abs(i - page) <= win) nums.push(i);
    }
    var html = "", last = 0;
    nums.forEach(function (i) {
      if (last && i - last > 1) html += '<span class="pggap">…</span>';
      html += '<button type="button" class="pgnum" data-page="' + i + '"'
            + (i === page ? ' data-on="true" aria-current="page"' : '')
            + ' title="第 ' + i + ' 页">' + i + '</button>';
      last = i;
    });
    pgnums.innerHTML = html;
  }
  if (pgnums) {
    pgnums.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest(".pgnum") : null;
      if (!b) return;
      var n = parseInt(b.getAttribute("data-page"), 10);
      if (!n || n === state.page) return;
      state.page = n;
      render();
      var bar = document.querySelector(".pgbar");
      if (bar && bar.getBoundingClientRect().top < 0) bar.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function pass(it) {
    /* 多选语义（GSP 同源）：tag/journal 为 null = 全部选中 = 不筛选；
       否则为「入选值」数组，命中任一即通过（OR），无标签/期刊的内容随之隐藏 */
    if (state.tag && !(it.tg || []).some(function (t) { return state.tag.indexOf(t) > -1; })) return false;
    if (state.journal && state.journal.indexOf(it.j || "") === -1) return false;
    /* 日期区间按重叠匹配：pd 只有月精度（YYYY-MM）时按整月 [月初, 月底] 算 */
    if (state.from || state.to) {
      const v = it.pd || "";
      if (!v) return false;
      const ps = v.length === 7 ? v + "-01" : v, pe = v.length === 7 ? v + "-31" : v;
      if (state.from && pe < state.from) return false;
      if (state.to && ps > state.to) return false;
    }
    if (state.q && (it.se || "").indexOf(state.q) === -1) return false;
    return true;
  }
  function cmp(a, b) {
    var dir = state.sort === "pub_asc" ? 1 : -1;
    var aa = a.pd || "", ab = b.pd || "";
    if (aa !== ab) return (aa < ab ? -1 : 1) * dir;
    // 同日期内按 id 排：与 build_site.py 的静态首屏顺序保持一致
    return (a.id || "").localeCompare(b.id || "");
  }
  /* 随机排序（GSP 路由 + 会话种子）：键 = hash(文献id + 会话种子)，种子存
     sessionStorage —— 同一会话内返回 / 刷新 / 翻页 / 筛选都不重排；
     点「随机」或新开会话才换新种子重洗。此前 Math.random 每次加载全新键，
     从详情页返回 Home 随机顺序全变（实际踩坑） */
  function hash01(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 100000) / 100000;
  }
  function newSeed() {
    var s = Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
    try { sessionStorage.setItem("mbd-rseed", s); } catch (e) {}
    return s;
  }
  function reshuffle() {
    var s = "";
    try { s = sessionStorage.getItem("mbd-rseed") || ""; } catch (e) {}
    if (!s) s = newSeed();
    ITEMS.forEach(function (it) { it._rk = hash01((it.id || "") + ":" + s); });
  }
  function cmpRand(a, b) { return (a._rk || 0) - (b._rk || 0); }
  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined) e.textContent = txt;
    return e;
  }
  function cardNode(it) {
    var a = el("a", "card");
    a.href = it.id + "/";
    // 主标题已中文优先（数据构建期定好）；悬停提示给另一种语言的标题
    a.title = (it.tz && it.t === it.tz) ? (it.te || it.t) : (it.tz || it.t);
    if (it.ct || it.cv) {                 // 封面通栏顶图（自然比例），与静态首屏输出一致
      var box = el("span", "c-covbox");   // 垫「加载中」占位层，img 加载后自然盖住
      var cov = document.createElement("img");
      cov.className = "c-cov";
      cov.loading = "lazy";
      if (it.ct) { cov.src = it.ct; cov.dataset.full = it.cv; }   // 缩略外链优先，失败回退原图
      else cov.src = it.cv;
      if (it.ctw && it.cth) { cov.width = it.ctw; cov.height = it.cth; }  // 预留位防抖动
      cov.alt = "封面图";
      box.appendChild(cov);
      a.appendChild(box);
    } else {                              // 无封面：期刊缩写占位块
      a.appendChild(el("span", "c-ph", it.jb || it.j || "—"));
    }
    var cap = el("span", "c-cap");
    var meta = el("span", "c-meta");      // 文章类型 + 期刊徽章
    if (it.pt) meta.appendChild(el("span", "c-ty", it.pt));
    var jb = el("span", "c-j", it.jb || it.j || "—");
    if (it.j && it.jb) jb.title = it.j;
    meta.appendChild(jb);
    cap.appendChild(meta);
    cap.appendChild(el("span", "c-t", it.t));
    a.appendChild(cap);
    return a;
  }
  function perSize(list) { return state.per > 0 ? state.per : (list.length || 1); }
  function render() {
    var list = ITEMS.filter(pass);
    list.sort(state.sort === "rand" ? cmpRand : cmp);
    var per = perSize(list);
    var pages = Math.max(1, Math.ceil(list.length / per));
    if (state.page > pages) state.page = pages;
    if (state.page < 1) state.page = 1;
    var slice = list.slice((state.page - 1) * per, state.page * per);
    grid.innerHTML = "";
    slice.forEach(function (it) { grid.appendChild(cardNode(it)); });
    if (info) info.textContent = "共 " + pages + " 页";
    if (prev) prev.disabled = state.page <= 1;
    if (next) next.disabled = state.page >= pages;
    paintNums(state.page, pages);
    if (empty) empty.style.display = list.length ? "none" : "block";
    syncFilterBtn();
    syncUrl();
  }
  /* 状态 → URL（GSP 同款路由）：筛选/排序/页码写进地址栏，replaceState 不新增
     历史记录、不打断返回手势；顺带把图库当前地址记进 sessionStorage（mbd-back），
     详情页「返回全部」据此跳回 —— 随机排序也不会因返回而重排 */
  function syncUrl() {
    try {
      var parts = [];
      if (state.q) parts.push("q=" + encodeURIComponent(state.q));
      if (state.tag) parts.push("tag=" + encodeURIComponent(state.tag.join("|")));
      if (state.journal) parts.push("journal=" + encodeURIComponent(state.journal.join("|")));
      if (state.from) parts.push("from=" + encodeURIComponent(state.from));
      if (state.to) parts.push("to=" + encodeURIComponent(state.to));
      if (state.sort !== "pub") parts.push("sort=" + encodeURIComponent(state.sort));
      if (state.page > 1) parts.push("page=" + state.page);
      history.replaceState(null, "", location.pathname + (parts.length ? "?" + parts.join("&") : ""));
      sessionStorage.setItem("mbd-back", location.pathname + location.search);
    } catch (e) {}
  }
  function resetPage() { state.page = 1; render(); }

  /* ── 多选 chips（GSP 同源）：aria-pressed 是唯一状态真源，state 从 DOM 派生。
     默认全选（构建期即 pressed=true）= 不筛选（state[key]=null）；
     点击 chip = 剔除/恢复；全选/反选 为虚线胶囊 chip-act ── */
  function chipBoxes() { return Array.prototype.slice.call(document.querySelectorAll(".chips[data-key]")); }
  function boxChips(box) { return box.querySelectorAll("button.chip[data-v]"); }
  function readChips(box) {
    var key = box.getAttribute("data-key"), sel = [], c = boxChips(box);
    c.forEach(function (b) { if (b.getAttribute("aria-pressed") === "true") sel.push(b.getAttribute("data-v")); });
    state[key] = sel.length === c.length ? null : sel;   // 全部选中 = 不筛选
  }
  function writeChips(box) {
    var sel = state[box.getAttribute("data-key")];
    boxChips(box).forEach(function (b) {
      b.setAttribute("aria-pressed", String(!sel || sel.indexOf(b.getAttribute("data-v")) > -1));
    });
  }
  chipBoxes().forEach(function (box) {
    box.addEventListener("click", function (e) {
      var act = e.target.closest ? e.target.closest(".chip-act") : null;
      if (act) {
        var all = act.getAttribute("data-act") === "all";
        boxChips(box).forEach(function (b) { b.setAttribute("aria-pressed", String(all ? true : b.getAttribute("aria-pressed") !== "true")); });
      } else {
        var chip = e.target.closest ? e.target.closest("button.chip[data-v]") : null;
        if (!chip) return;
        chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
      }
      readChips(box); resetPage();
    });
  });

  if (q) {
    q.addEventListener("input", function () { state.q = q.value.trim().toLowerCase(); resetPage(); });
    q.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { q.value = ""; state.q = ""; resetPage(); }
      if (e.key === "Enter") { e.preventDefault(); runSearch(); }
    });
  }
  var qBtn = document.getElementById("qBtn");
  function runSearch() {
    state.q = (q ? q.value : "").trim().toLowerCase();
    resetPage();
    var g = document.getElementById("grid");
    if (g && g.getBoundingClientRect().top < 0) g.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  if (qBtn) qBtn.addEventListener("click", runSearch);
  var sortseg = document.getElementById("sortseg");
  if (sortseg) {
    if (["pub", "pub_asc", "rand"].indexOf(state.sort) === -1) state.sort = "pub";
    var sortBtns = sortseg.querySelectorAll("button");
    sortBtns.forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-sort") === state.sort));
      b.addEventListener("click", function () {
        state.sort = b.getAttribute("data-sort");
        if (state.sort === "rand") { newSeed(); reshuffle(); }   // 每次点「随机」换新种子重洗
        try { localStorage.setItem("mbd-sort2", state.sort); } catch (e) {}
        sortBtns.forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        resetPage();
      });
    });
  }
  var fFrom = document.getElementById("f-from"), fTo = document.getElementById("f-to");
  if (fFrom) fFrom.addEventListener("change", function () { state.from = fFrom.value; resetPage(); });
  if (fTo) fTo.addEventListener("change", function () { state.to = fTo.value; resetPage(); });
  if (prev) prev.addEventListener("click", function () { state.page -= 1; render(); });
  if (next) next.addEventListener("click", function () { state.page += 1; render(); });

  var reset = document.getElementById("reset");
  if (reset) reset.addEventListener("click", function () {
    state = { q: "", tag: null, journal: null, from: "", to: "", sort: state.sort, page: 1,
              per: state.per };   // 保留每页数量（此 bug 继承自 GeoSciPlot，文献多时重置后分页会失效）
    var fF = document.getElementById("f-from"), fT = document.getElementById("f-to");
    if (fF) fF.value = "";
    if (fT) fT.value = "";
    if (q) q.value = "";
    /* 重置 = 恢复默认全选：所有 chip 写回 pressed=true（老单选时代「只留第一枚」
       的写法必须删净，否则在 URL 写回后执行会覆盖状态 —— 实际踩坑） */
    chipBoxes().forEach(function (box) { writeChips(box); });
    render();
  });

  /* ── 每页显示数量 ── */
  var perSel = document.getElementById("perpage");
  if (perSel) {
    perSel.value = String(state.per);
    perSel.addEventListener("change", function () {
      state.per = parseInt(perSel.value, 10) || 0;
      try { localStorage.setItem("mbd-per", String(state.per)); } catch (e) {}
      resetPage();
    });
  }

  /* ── 筛选区折叠（状态记在 localStorage） ── */
  var filterBtn = document.getElementById("filterBtn");
  var filterBox = document.getElementById("filters");
  /* 筛选摘要（GSP 同源）：被剔除的 ≤3 个显示「排除 X、Y」，否则「仅 入选值」 */
  function chipSummary(key, label) {
    if (!state[key]) return null;
    var box = document.querySelector('.chips[data-key="' + key + '"]');
    if (!box) return null;
    var off = [];
    boxChips(box).forEach(function (b) {
      if (b.getAttribute("aria-pressed") === "false") off.push(b.getAttribute("data-v"));
    });
    return label + " " + (off.length > 0 && off.length <= 3 ? "排除 " + off.join("、") : "仅 " + state[key].join("、"));
  }
  function activeFilters() {
    var parts = [], s;
    if ((s = chipSummary("tag", "标签"))) parts.push(s);
    if ((s = chipSummary("journal", "期刊"))) parts.push(s);
    if (state.from || state.to) parts.push("时间 " + (state.from || "…") + " ~ " + (state.to || "…"));
    if (state.q) parts.push("搜索 " + state.q);
    return parts;
  }
  function syncFilterBtn() {
    if (!filterBtn || !filterBox) return;
    var open = !filterBox.hasAttribute("hidden");
    var list = activeFilters();
    filterBtn.textContent = (open ? "筛选 ▴" : "筛选 ▾") + (list.length ? " · " + list.length : "");
    filterBtn.setAttribute("aria-expanded", String(open));
    filterBtn.title = list.length ? "当前筛选：" + list.join(" / ") : "展开或收起筛选条件";
  }
  if (filterBox) {
    var collapsed = false;
    try { collapsed = localStorage.getItem("mbd-filters") === "hidden"; } catch (e) {}
    if (collapsed) filterBox.setAttribute("hidden", "");
  }
  if (filterBtn && filterBox) {
    filterBtn.addEventListener("click", function () {
      var open = !filterBox.hasAttribute("hidden");
      if (open) filterBox.setAttribute("hidden", ""); else filterBox.removeAttribute("hidden");
      try { localStorage.setItem("mbd-filters", open ? "hidden" : "shown"); } catch (e) {}
      syncFilterBtn();
    });
  }

  /* 支持带参数的链接（标签跳转 / 分享筛选结果）：/?tag=海冰&journal=Nature&sort=rand */
  var applied = false;
  try {
    var params = new URLSearchParams(location.search);
    ["q", "tag", "journal", "from", "to", "sort", "page"].forEach(function (k) {
      var v = params.get(k);
      if (!v) return;
      if (k === "page") {
        /* 页码随路由回填（syncUrl 写入）：返回 / 刷新都停在离开时的那一页 */
        applied = true;
        state.page = Math.max(1, parseInt(v, 10) || 1);
        return;
      }
      if (k === "sort") {
        // 排序可分享，但仅本次生效 —— 分享链接不应永久改写接收者的排序偏好
        if (["pub", "pub_asc", "rand"].indexOf(v) === -1) return;
        applied = true;
        state.sort = v;
        if (sortseg) {
          sortseg.querySelectorAll("button").forEach(function (x) {
            x.setAttribute("aria-pressed", String(x.getAttribute("data-sort") === v));
          });
        }
        return;
      }
      applied = true;
      if (k === "q") {
        state.q = v.trim().toLowerCase();
        if (q) q.value = v;
      } else if (k === "tag" || k === "journal") {
        /* 多选：?tag=A|B|C（旧单值链接天然兼容）。先写 state 再写回 chip 的
           aria-pressed，最后从 DOM 重读 —— 保持「DOM 是唯一真源」 */
        var box = document.querySelector('.chips[data-key="' + k + '"]');
        if (box) {
          state[k] = v.split("|");
          writeChips(box);
          readChips(box);
        }
      } else {
        state[k] = v;
        // 日期区间：回填输入框，避免"筛选生效但输入框是空的"的困惑
        var inp = document.getElementById("f-" + k);
        if (inp) inp.value = v;
      }
    });
  } catch (e) {}

  /* 首屏卡片是构建时静态输出的（对爬虫友好）。只有当"每页数量/排序"被用户改过、
     或链接带了筛选参数时，才用 JS 重新渲染，保证 DOM 与状态一致。
     （不重渲染的默认情况下，静态输出与 JS 首屏完全一致） */
  var needRender = applied || state.per !== PAGE || state.sort !== "pub";
  if (needRender) {
    state.page = 1;
    render();
  } else {
    var per0 = perSize(ITEMS);
    var pages0 = Math.max(1, Math.ceil(ITEMS.length / per0));
    if (info) info.textContent = "共 " + pages0 + " 页";
    if (prev) prev.disabled = true;
    if (next) next.disabled = ITEMS.length <= per0;
    paintNums(1, pages0);
    syncFilterBtn();
  }

  /* 窄屏 / 宽屏切换时页码窗口会变，重算一次节点（不重建卡片） */
  var lastWin = windowSize();
  var onResize = function () {
    if (windowSize() === lastWin) return;
    lastWin = windowSize();
    var per1 = perSize(ITEMS.filter(pass));
    paintNums(state.page, Math.max(1, Math.ceil(ITEMS.filter(pass).length / per1)));
  };
  window.addEventListener("resize", onResize);

})();

/* ── 渲染样式切换（文章详情头部的两枚圆点）：html[data-rstyle] 已由
   <head> 内联脚本按 localStorage 设好，这里只回写按钮选中态；
   点击写偏好后刷新 —— 样式切换走整页重渲染 ── */
(function () {
  var btns = document.querySelectorAll(".rst-btn");
  if (!btns.length) return;
  var cur = document.documentElement.getAttribute("data-rstyle") || "plain";
  Array.prototype.forEach.call(btns, function (b) {
    b.setAttribute("aria-pressed", String(b.getAttribute("data-rs") === cur));
    b.addEventListener("click", function () {
      try { localStorage.setItem("mbd-rstyle", b.getAttribute("data-rs")); } catch (e) {}
      location.reload();
    });
  });
})();

/* ── 每周速递列表翻页（每页最多 3 个月）：分组 hidden 由构建期预置，这里只切换
   页码态与上下页可用性；chips 点击改为「跳到目标月所在页并滚动到分组」；
   无 JS 时 noscript 展开全部分组（行为同旧版长页） ── */
(function () {
  var bar = document.getElementById("wk-pgbar");
  if (!bar) return;
  var groups = Array.prototype.slice.call(document.querySelectorAll(".wk-group[data-page]"));
  if (!groups.length) return;
  var cur = 1, maxP = 1;
  groups.forEach(function (g) { maxP = Math.max(maxP, +g.getAttribute("data-page")); });
  function show(n, scroll) {
    cur = Math.max(1, Math.min(maxP, n));
    groups.forEach(function (g) { g.hidden = +g.getAttribute("data-page") !== cur; });
    Array.prototype.forEach.call(bar.querySelectorAll("[data-p]"), function (b) {
      var on = +b.getAttribute("data-p") === cur;
      b.classList.toggle("on", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    document.getElementById("wk-pg-prev").disabled = cur <= 1;
    document.getElementById("wk-pg-next").disabled = cur >= maxP;
    try { history.replaceState(null, "", "#p" + cur); } catch (e) {}
    if (scroll) {
      var q = document.querySelector(".wk-quicknav");
      if (q) window.scrollTo({ top: q.getBoundingClientRect().top + window.pageYOffset - 70, behavior: "smooth" });
    }
  }
  bar.addEventListener("click", function (e) {
    var b = e.target.closest("[data-p]");
    if (b) show(+b.getAttribute("data-p"), true);
  });
  document.getElementById("wk-pg-prev").addEventListener("click", function () { show(cur - 1, true); });
  document.getElementById("wk-pg-next").addEventListener("click", function () { show(cur + 1, true); });
  Array.prototype.forEach.call(document.querySelectorAll(".wk-quicknav .wk-btn"), function (a) {
    a.addEventListener("click", function (e) {
      var g = document.getElementById(a.getAttribute("href").slice(1));
      if (!g) return;
      e.preventDefault();
      show(+g.getAttribute("data-page"), false);
      g.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  var m = location.hash.match(/^#p(\d+)$/);
  show(m ? Math.max(1, Math.min(maxP, +m[1])) : 1, false);
})();
