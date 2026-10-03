/**
 * article-toc —— 文章目录组件（桌面侧栏 + 移动端抽屉 + 当前小节高亮）
 *
 * 拷自 mystation 项目：数据层 frontend/lib/md-shared.ts 的 slugify / extractTOC、
 * 组件 frontend/components/BlogTOC.tsx（React → 原生 JS 改写，交互口径与上游一致）。
 * 本文件为零依赖原生 JS；配套样式（v3-side-hd / v3-side-link / v3-toc-link /
 * v3-noscrollbar / toc-fab 等）在站点 style.css 中，CSS 变量已映射本站 token。
 *
 * 挂载规则（脚本在 body 末尾注入，DOM 已就绪）：
 *   - 周报文章页（.wk-content）：桌面端已有服务端渲染的 .wk-toc 侧栏 —— 只做增强
 *     （滚动高亮 + 平滑滚动），并按 viewport 提供 FAB + 抽屉（<1280px）；
 *   - 文献详情页（.pbody）：无现成目录 —— 组件全量代建：≥1280px 出 fixed 右侧栏
 *     （避让右缘 .fab 工具排），<1280px 出 FAB + 抽屉。
 * 标题 id 缺失时（详情页节标题无 id）用 slugify+去重现补，保证锚点稳定。
 */

/* ── 数据层：slugify / extractTOC（拷自 mystation md-shared.ts，删类型转 DOM 版）── */

// 中文友好的标题→锚点 id：保留 CJK，去掉其余非词字符，空白转连字符，60 字符截断
function tocSlugify(text) {
  return String(text)
    .toLowerCase()
    .replace(/[^\w一-鿿\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/--+/g, "-")
    .substring(0, 60);
}

// 从容器 DOM 抓 h2–h5，产出 [{id, title, level}]。
// level = 标签序 - 1：本站 markdown 渲染管线把标题整体降了一级（md 的 # 输出 <h2>），
// 与 mystation extractTOC 的回拨口径一致，目录层级即源 markdown 层级。
// 无 id 的标题（详情页节标题）用 slugify + 去重现补 —— 同 mystation 渲染器的 uniqueSlug。
function extractTOC(root) {
  const counts = {};
  const toc = [];
  root.querySelectorAll("h2, h3, h4, h5").forEach((h) => {
    let id = h.id;
    if (!id) {
      const base = tocSlugify(h.textContent || "");
      counts[base] = (counts[base] || 0) + 1;
      id = counts[base] > 1 ? `${base}-${counts[base]}` : base;
      h.id = id;
    }
    toc.push({
      id,
      title: (h.textContent || "").trim(),
      level: parseInt(h.tagName[1], 10) - 1,
    });
  });
  return toc;
}

/* ── 组件（BlogTOC 的原生 JS 改写）── */

function escToc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

const TOC_ICO_LIST = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6h16M4 12h8m-8 6h16"/></svg>';
const TOC_ICO_CLOSE = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 18 18 6M6 6l12 12"/></svg>';

function initArticleTOC() {
  const container = document.querySelector(".wk-content") || document.querySelector(".pbody");
  if (!container) return;
  const isWeekly = container.classList.contains("wk-content");
  const toc = extractTOC(container);
  if (toc.length < 2) return;          // 单节页面不值得出目录

  let activeId = "";

  // —— 当前小节高亮：标题进入视口上部 1/3 时点亮（rootMargin 同上游 BlogTOC）
  const setActive = (id) => {
    if (id === activeId) return;
    activeId = id;
    document.querySelectorAll("[data-toc-id]").forEach((a) => {
      a.setAttribute("data-active", String(a.getAttribute("data-toc-id") === id));
    });
    // 周报页服务端渲染的既有侧栏链接同步点亮
    document.querySelectorAll(".wk-toc-link").forEach((a) => {
      a.setAttribute("data-active", String((a.getAttribute("href") || "") === "#" + id));
    });
  };
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((en) => { if (en.isIntersecting) setActive(en.target.id); });
  }, { rootMargin: "-80px 0px -66% 0px" });
  toc.forEach((it) => {
    const el = document.getElementById(it.id);
    if (el) observer.observe(el);
  });

  // —— 点击：平滑滚动 + pushState 更新 hash + 关抽屉（同上游 scrollTo）
  const wireLink = (a) => {
    a.addEventListener("click", (e) => {
      const id = (a.getAttribute("href") || "").slice(1);
      const el = id && document.getElementById(id);
      if (!el) return;
      e.preventDefault();
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      try { history.pushState(null, "", "#" + id); } catch (err) { /* file:// 等场景忽略 */ }
      closeDrawer();
    });
  };

  // —— 抽屉（移动端；周报/详情通用）
  let drawer = null;
  const openDrawer = () => { if (drawer) drawer.hidden = false; };
  const closeDrawer = () => { if (drawer) drawer.hidden = true; };

  const linksHtml = () => toc.map((it) =>
    `<a class="v3-side-link v3-toc-link" data-toc-id="${escToc(it.id)}" data-lv="${it.level}"
        href="#${escToc(it.id)}">${escToc(it.title)}</a>`).join("");

  const fab = document.createElement("div");
  fab.className = "toc-fab";
  fab.innerHTML = `<button type="button" aria-label="目录">${TOC_ICO_LIST}</button>`;
  fab.querySelector("button").addEventListener("click", openDrawer);

  drawer = document.createElement("div");
  drawer.className = "toc-drawer";
  drawer.hidden = true;
  drawer.innerHTML = `
    <div class="toc-mask"></div>
    <div class="toc-panel v3-noscrollbar">
      <div class="toc-panel-hd">
        <h4 class="v3-side-hd">此页内容</h4>
        <button type="button" class="toc-close" aria-label="关闭目录">${TOC_ICO_CLOSE}</button>
      </div>
      <nav class="toc-nav">${linksHtml()}</nav>
    </div>`;
  drawer.querySelector(".toc-mask").addEventListener("click", closeDrawer);
  drawer.querySelector(".toc-close").addEventListener("click", closeDrawer);
  // Esc 关抽屉（上游未做，顺手补上；与灯箱的 Esc 关闭习惯一致）
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  document.body.appendChild(fab);
  document.body.appendChild(drawer);
  drawer.querySelectorAll("[data-toc-id]").forEach(wireLink);
  fab.addEventListener("click", (e) => e.stopPropagation());

  // —— 桌面端
  if (isWeekly) {
    // 周报页 ≥1280px 已有服务端渲染的 .wk-toc 侧栏：只接平滑滚动（高亮走 setActive）
    document.querySelectorAll(".wk-toc-link").forEach(wireLink);
  } else {
    // 详情页无现成目录：≥1280px 出 fixed 右侧栏（右移 70px 避让竖排 .fab 工具排）
    const rail = document.createElement("aside");
    rail.className = "p-toc v3-noscrollbar";
    rail.innerHTML = `<h4 class="v3-side-hd">此页内容</h4><nav class="toc-nav">${linksHtml()}</nav>`;
    document.body.appendChild(rail);
    rail.querySelectorAll("[data-toc-id]").forEach(wireLink);
  }
}

initArticleTOC();
