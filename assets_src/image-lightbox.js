/**
 * image-lightbox —— 零依赖文章图片灯箱（纯原生 JS，框架无关）
 *
 * 拷自 mystation 项目 frontend/lib/image-lightbox.ts（删除 TypeScript 类型标注转纯 JS），
 * 其余逻辑与上游保持一致，升级时可直接对照上游文件重新转换。
 *
 * 特性：
 *   - 点击正文图片弹出灯箱；同容器内多图可左右切换（按钮 / 方向键 / 触摸滑动）
 *   - 滚轮缩放（以光标为中心）、双击/双触缩放、双指捏合、拖拽平移
 *   - Esc / 点击背景 / 关闭按钮关闭；±/0 键盘缩放；相邻图预加载与加载失败提示
 *   - 打开时锁定背景滚动（带滚动条宽度补偿）；焦点移入灯箱、关闭后归还
 *   - 自动跳过链接内的图、超小图（徽章/图标）、data-no-lightbox 标记的图
 *
 * 复用方式：对任意容器调用 attachImageLightbox(selector, { exclude: ".no-zoom img" })，
 * 返回 detach() 供卸载时清理。样式随模块自动注入（<style data-lbx-style>），
 * 类名统一 lbx- 前缀，不污染宿主页面；配色可用 CSS 变量覆盖：
 * --lbx-backdrop / --lbx-accent / --lbx-fg。
 *
 * HTML 标记：data-no-lightbox 强制排除；data-lightbox="on" 让链接内的图强制参与；
 * data-lightbox-src="高清图URL" 指定灯箱里加载的更清晰版本。
 */

const STYLES_ID = "lbx-styles";
const ROOT_Z = 99990;

const STYLES = `
[data-lbx]{position:fixed;inset:0;z-index:${ROOT_Z};opacity:0;transition:opacity .18s ease}
[data-lbx].lbx-open{opacity:1}
[data-lbx] .lbx-backdrop{position:absolute;inset:0;background:var(--lbx-backdrop,rgba(0,0,0,.93))}
[data-lbx] .lbx-stage{position:absolute;inset:0;touch-action:none;overflow:hidden;cursor:grab}
[data-lbx] .lbx-stage.lbx-panning{cursor:grabbing}
[data-lbx] .lbx-img{position:absolute;left:50%;top:50%;max-width:none;user-select:none;
  -webkit-user-drag:none;opacity:0;transition:opacity .2s ease;
  will-change:transform;border-radius:4px;box-shadow:0 8px 60px rgba(0,0,0,.45)}
[data-lbx] .lbx-img.lbx-loaded{opacity:1}
[data-lbx].lbx-anim .lbx-img{transition:transform .22s cubic-bezier(.2,.8,.3,1),opacity .2s ease}
[data-lbx] .lbx-spinner{position:absolute;left:50%;top:50%;width:34px;height:34px;margin:-17px 0 0 -17px;
  border:2.5px solid rgba(255,255,255,.18);border-top-color:var(--lbx-accent,#58a6ff);
  border-radius:50%;animation:lbx-spin .8s linear infinite;display:none}
[data-lbx] .lbx-spinner.lbx-on{display:block}
@keyframes lbx-spin{to{transform:rotate(360deg)}}
[data-lbx] .lbx-topbar{position:absolute;top:0;left:0;right:0;display:flex;align-items:center;
  justify-content:space-between;padding:14px 18px;pointer-events:none;
  font:12.5px/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.08em;
  color:var(--lbx-fg,rgba(255,255,255,.75))}
[data-lbx] .lbx-counter{margin-left:2px}
[data-lbx] .lbx-close{pointer-events:auto;width:38px;height:38px;display:flex;align-items:center;
  justify-content:center;border:0;border-radius:50%;background:rgba(255,255,255,.06);cursor:pointer;
  color:var(--lbx-fg,rgba(255,255,255,.85));transition:background .15s ease,transform .15s ease}
[data-lbx] .lbx-close:hover{background:rgba(255,255,255,.16);transform:scale(1.06)}
[data-lbx] .lbx-arrow{position:absolute;top:50%;transform:translateY(-50%);width:46px;height:46px;
  display:flex;align-items:center;justify-content:center;border:0;border-radius:50%;cursor:pointer;
  background:rgba(255,255,255,.06);color:var(--lbx-fg,rgba(255,255,255,.85));
  transition:background .15s ease,opacity .15s ease}
[data-lbx] .lbx-arrow:hover{background:rgba(255,255,255,.16)}
[data-lbx] .lbx-arrow.lbx-prev{left:14px}
[data-lbx] .lbx-arrow.lbx-next{right:14px}
[data-lbx] .lbx-arrow.lbx-hidden{opacity:0;pointer-events:none}
[data-lbx] .lbx-caption{position:absolute;left:0;right:0;bottom:0;padding:14px 56px;text-align:center;
  font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  color:var(--lbx-fg,rgba(255,255,255,.78));pointer-events:none;
  text-shadow:0 1px 4px rgba(0,0,0,.6)}
[data-lbx] .lbx-caption.lbx-error{color:#ff7b72}
[data-lbx] .lbx-zoom{position:absolute;right:18px;bottom:16px;padding:3px 9px;border-radius:999px;
  background:rgba(255,255,255,.1);font:11.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  color:var(--lbx-fg,rgba(255,255,255,.8));opacity:0;transition:opacity .15s ease;pointer-events:none}
[data-lbx] .lbx-zoom.lbx-on{opacity:1}
@media (max-width:640px){
  [data-lbx] .lbx-arrow{width:40px;height:40px}
  [data-lbx] .lbx-arrow.lbx-prev{left:6px}
  [data-lbx] .lbx-arrow.lbx-next{right:6px}
  [data-lbx] .lbx-caption{padding:12px 20px}
  [data-lbx] .lbx-zoom{display:none}
}
`;

function attachImageLightbox(target, options = {}) {
  const {
    selector = "img",
    exclude,
    minNaturalSize = 48,
    maxScale = 8,
  } = options;

  const container =
    typeof target === "string" ? document.querySelector(target) : target;
  if (!container) return function () {};

  // ───────────────────────── 样式注入（幂等） ─────────────────────────
  if (!document.getElementById(STYLES_ID)) {
    const style = document.createElement("style");
    style.id = STYLES_ID;
    // 标记用 data-lbx-style，与灯箱根节点的 data-lbx 区分开
    style.setAttribute("data-lbx-style", "");
    style.textContent = STYLES;
    document.head.appendChild(style);
  }
  const candidateStyle = document.createElement("style");
  candidateStyle.setAttribute("data-lbx-candidates", "");
  candidateStyle.textContent = ".lbx-candidate{cursor:zoom-in}";
  document.head.appendChild(candidateStyle);

  // ───────────────────────── 图片资格判定 ─────────────────────────
  // 链接里的图默认是导航元素（徽章、封面跳转），不劫持点击，data-lightbox="on"
  // 可强制参与；data-no-lightbox 反向排除；自然尺寸过小的按徽章/图标处理。
  const isEligible = (img) => {
    if (!img.matches(selector)) return false;
    if (img.hasAttribute("data-no-lightbox")) return false;
    if (img.closest("a[href]") && img.dataset.lightbox !== "on") return false;
    if (exclude && (img.matches(exclude) || img.closest(exclude))) return false;
    // 懒加载未解码时 naturalWidth 为 0，视为"未知"放行，点击后由灯箱自己加载
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    if (minNaturalSize > 0 && w > 0 && h > 0 && w < minNaturalSize && h < minNaturalSize) {
      return false;
    }
    return true;
  };

  // 合格图标注抓手光标；先清后加，让"加载后变小图"的也能摘掉标记
  const decorate = () => {
    const imgs = container.querySelectorAll("img");
    imgs.forEach((img) => {
      const eligible = isEligible(img);
      img.classList.toggle("lbx-candidate", eligible);
    });
  };
  decorate();

  // 动态插入的图（预览、AJAX 正文替换）也要有抓手光标
  const observer = new MutationObserver(() => decorate());
  observer.observe(container, { childList: true, subtree: true });

  // ───────────────────────── 灯箱 DOM（首次打开时创建） ─────────────────────────
  let root = null;
  let stageEl;
  let imgEl;
  let spinnerEl;
  let captionEl;
  let counterEl;
  let zoomEl;
  let prevBtn;
  let nextBtn;
  let closeBtn;

  const ensureDom = () => {
    if (root) return;
    root = document.createElement("div");
    root.setAttribute("data-lbx", "");
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", "图片查看");
    root.style.display = "none";
    root.innerHTML = `
      <div class="lbx-backdrop"></div>
      <div class="lbx-stage">
        <img class="lbx-img" alt="" draggable="false">
        <div class="lbx-spinner"></div>
      </div>
      <div class="lbx-topbar">
        <span class="lbx-counter"></span>
        <button type="button" class="lbx-close" aria-label="关闭">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
      <button type="button" class="lbx-arrow lbx-prev" aria-label="上一张">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
      </button>
      <button type="button" class="lbx-arrow lbx-next" aria-label="下一张">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>
      </button>
      <div class="lbx-caption"></div>
      <div class="lbx-zoom"></div>
    `;
    stageEl = root.querySelector(".lbx-stage");
    imgEl = root.querySelector(".lbx-img");
    spinnerEl = root.querySelector(".lbx-spinner");
    captionEl = root.querySelector(".lbx-caption");
    counterEl = root.querySelector(".lbx-counter");
    zoomEl = root.querySelector(".lbx-zoom");
    prevBtn = root.querySelector(".lbx-prev");
    nextBtn = root.querySelector(".lbx-next");
    closeBtn = root.querySelector(".lbx-close");
    document.body.appendChild(root);
    bindStage();
  };

  // ───────────────────────── 变换状态 ─────────────────────────
  // scale=1 即"适配视口"的基准尺寸；tx/ty 是相对屏幕中心的平移。
  let scale = 1;
  let tx = 0;
  let ty = 0;
  let baseW = 0;
  let baseH = 0;
  let items = [];
  let currentIndex = 0;

  const applyTransform = () => {
    imgEl.style.width = `${baseW}px`;
    imgEl.style.height = `${baseH}px`;
    imgEl.style.transform = `translate(-50%, -50%) translate(${tx}px, ${ty}px) scale(${scale})`;
    const zoomed = scale > 1.005;
    zoomEl.textContent = `${Math.round(scale * 100)}%`;
    zoomEl.classList.toggle("lbx-on", zoomed);
    stageEl.classList.toggle("lbx-zoomed", zoomed);
  };

  // 缩放后把图拖出视口的位移拉回边缘，不让图"飞丢"
  const clampPan = () => {
    if (scale <= 1) {
      tx = 0;
      ty = 0;
      return;
    }
    const maxX = Math.max(0, (baseW * scale) / 2 - window.innerWidth / 2);
    const maxY = Math.max(0, (baseH * scale) / 2 - window.innerHeight / 2);
    tx = Math.min(Math.max(tx, -maxX), maxX);
    ty = Math.min(Math.max(ty, -maxY), maxY);
  };

  // 以视口点 (px, py) 为不动点缩放：光标指着哪儿，哪儿不动
  const zoomAt = (nextScale, px, py) => {
    const clamped = Math.min(Math.max(nextScale, 1), maxScale);
    if (clamped === scale) return;
    const cx = window.innerWidth / 2;
    const cy = window.innerHeight / 2;
    const k = clamped / scale;
    tx = px - cx - (px - cx - tx) * k;
    ty = py - cy - (py - cy - ty) * k;
    scale = clamped;
    clampPan();
    root.classList.add("lbx-anim");
    applyTransform();
  };

  const fitAndReset = () => {
    const nw = imgEl.naturalWidth || 1;
    const nh = imgEl.naturalHeight || 1;
    const margin = 88; // 顶栏 + 底部说明的留白
    const k = Math.min(
      (window.innerWidth - 32) / nw,
      (window.innerHeight - margin) / nh,
      1, // 超过自然尺寸不放大
    );
    baseW = nw * k;
    baseH = nh * k;
    scale = 1;
    tx = 0;
    ty = 0;
    applyTransform();
  };

  // ───────────────────────── 打开 / 关闭 / 切换 ─────────────────────────
  let lastFocused = null;
  let scrollLocked = false;
  let isOpen = false;

  const onKeyDown = (e) => {
    if (!isOpen) return;
    switch (e.key) {
      case "Escape":
        close();
        break;
      case "ArrowLeft":
        if (items.length > 1) go(currentIndex - 1);
        break;
      case "ArrowRight":
        if (items.length > 1) go(currentIndex + 1);
        break;
      case "+":
      case "=":
        zoomAt(scale * 1.4, window.innerWidth / 2, window.innerHeight / 2);
        break;
      case "-":
        zoomAt(scale / 1.4, window.innerWidth / 2, window.innerHeight / 2);
        break;
      case "0":
        fitAndReset();
        break;
    }
  };

  const onResize = () => {
    if (isOpen) fitAndReset();
  };

  const lockScroll = () => {
    if (scrollLocked) return;
    scrollLocked = true;
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    document.documentElement.style.overflow = "hidden";
    if (scrollbar > 0) document.body.style.paddingRight = `${scrollbar}px`;
  };

  const unlockScroll = () => {
    if (!scrollLocked) return;
    scrollLocked = false;
    document.documentElement.style.overflow = "";
    document.body.style.paddingRight = "";
  };

  const open = (index, list) => {
    ensureDom();
    items = list;
    lastFocused = document.activeElement;
    isOpen = true;
    root.style.display = "";
    // 强制回流让 display:none → block 的淡入过渡生效。
    // 不用 rAF：后台/被遮挡标签页会无限节流 rAF，遮罩将停留在透明状态。
    void root.offsetHeight;
    root.classList.add("lbx-open");
    lockScroll();
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("resize", onResize);
    go(index);
    closeBtn.focus({ preventScroll: true });
  };

  const close = () => {
    if (!isOpen || !root) return;
    isOpen = false;
    root.classList.remove("lbx-open");
    window.removeEventListener("keydown", onKeyDown, true);
    window.removeEventListener("resize", onResize);
    unlockScroll();
    const el = root;
    setTimeout(() => {
      if (!isOpen && el === root) el.style.display = "none";
    }, 190);
    if (lastFocused instanceof HTMLElement) lastFocused.focus({ preventScroll: true });
  };

  const finishLoad = () => {
    spinnerEl.classList.remove("lbx-on");
    imgEl.classList.add("lbx-loaded");
    fitAndReset();
    root.classList.add("lbx-anim");
  };

  const failLoad = () => {
    spinnerEl.classList.remove("lbx-on");
    captionEl.textContent = "图片加载失败";
    captionEl.classList.add("lbx-error");
  };

  const go = (index) => {
    if (!items.length || !root) return;
    currentIndex = (index + items.length) % items.length;
    const item = items[currentIndex];

    // 相邻图预加载，切换时秒开
    for (const offset of [1, -1]) {
      const neighbor = items[(currentIndex + offset + items.length) % items.length];
      if (neighbor) void (new Image().src = neighbor.fullSrc);
    }

    imgEl.classList.remove("lbx-loaded");
    imgEl.alt = item.alt;
    spinnerEl.classList.add("lbx-on");
    captionEl.classList.remove("lbx-error");
    captionEl.textContent = item.alt;
    counterEl.textContent = items.length > 1 ? `${currentIndex + 1} / ${items.length}` : "";
    const single = items.length <= 1;
    prevBtn.classList.toggle("lbx-hidden", single);
    nextBtn.classList.toggle("lbx-hidden", single);

    imgEl.onload = finishLoad;
    imgEl.onerror = failLoad;
    // src 相同的重复打开不触发 load 事件，complete 时直接收尾
    if (imgEl.src === item.fullSrc && imgEl.complete && imgEl.naturalWidth > 0) {
      finishLoad();
    } else {
      imgEl.src = item.fullSrc;
    }
  };

  // ───────────────────────── 交互：滚轮 / 指针 / 双击 ─────────────────────────
  const onWheel = (e) => {
    if (!isOpen) return;
    e.preventDefault();
    zoomAt(scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY);
  };

  // 指针状态机：down 记录起点 → move 分派拖拽/滑动/捏合 → up 收尾。
  // Map 持有全部活动指针，单指走平移/换图，双指走捏合。
  const pointers = new Map();
  let panStart = null;
  let swipeStartX = null;
  let swipeMoved = false;
  let pinchStart = null;
  // pointer capture 会把 click/dblclick 的 target 重定向到 stage，
  // "按下点是否在图片上"只能在 pointerdown 时记录原始 target
  let pressedOnImage = false;

  const onPointerDown = (e) => {
    if (!isOpen) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      const [a, b] = Array.from(pointers.values());
      pinchStart = { dist: Math.hypot(a.x - b.x, a.y - b.y), scale };
      panStart = null;
      swipeStartX = null;
    } else if (pointers.size === 1) {
      pressedOnImage = e.target === imgEl;
      panStart = { x: e.clientX, y: e.clientY, tx, ty };
      swipeStartX = e.clientX;
      swipeMoved = false;
      stageEl.classList.add("lbx-panning");
    }
    stageEl.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e) => {
    if (!isOpen || !pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (pointers.size === 2 && pinchStart) {
      const [a, b] = Array.from(pointers.values());
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      root.classList.remove("lbx-anim"); // 捏合要跟手，不能有过渡
      scale = Math.min(Math.max((pinchStart.scale * dist) / pinchStart.dist, 1), maxScale);
      clampPan();
      applyTransform();
      return;
    }

    if (!panStart) return;
    const dx = e.clientX - panStart.x;
    const dy = e.clientY - panStart.y;
    if (Math.abs(dx) + Math.abs(dy) > 6) swipeMoved = true;

    if (scale > 1.005) {
      root.classList.remove("lbx-anim");
      tx = panStart.tx + dx;
      ty = panStart.ty + dy;
      clampPan();
      applyTransform();
    } else if (swipeStartX !== null && items.length > 1) {
      // 未缩放时当前图水平跟手，给切换的手感
      root.classList.remove("lbx-anim");
      tx = e.clientX - swipeStartX;
      applyTransform();
    }
  };

  const onPointerUp = (e) => {
    if (!isOpen) return;
    pointers.delete(e.pointerId);
    stageEl.classList.remove("lbx-panning");

    if (pointers.size === 0) {
      if (scale > 1.005) {
        root.classList.add("lbx-anim");
        clampPan();
        applyTransform();
      } else if (swipeStartX !== null && items.length > 1) {
        const dx = e.clientX - swipeStartX;
        root.classList.add("lbx-anim");
        if (!swipeMoved || Math.abs(dx) < 48) {
          tx = 0;
          applyTransform(); // 不够一格，弹回
        } else {
          go(dx < 0 ? currentIndex + 1 : currentIndex - 1);
        }
      }
      panStart = null;
      swipeStartX = null;
      pinchStart = null;
    } else if (pointers.size === 1) {
      // 双指抬起一指 → 剩余一指重新校准平移起点，避免跳变
      const rest = Array.from(pointers.values())[0];
      panStart = { x: rest.x, y: rest.y, tx, ty };
      swipeStartX = null;
      pinchStart = null;
    }
  };

  // 双击/双触：在 1x 与 2.5x 间切换，以点击点为中心。
  // 鼠标走 dblclick 事件；触摸没有 dblclick，用 pointerup 间隔判定。
  // （合成双击可能只派发一对带 clickCount=2 的 pointer 事件，鼠标路径不可靠）
  let lastTap = 0;
  const toggleZoom = (px, py) => {
    if (scale > 1.005) {
      root.classList.add("lbx-anim");
      scale = 1;
      tx = 0;
      ty = 0;
      applyTransform();
    } else {
      zoomAt(2.5, px, py);
    }
  };
  const onDblClick = (e) => {
    e.preventDefault();
    toggleZoom(e.clientX, e.clientY);
  };
  const onDoubleTap = (e) => {
    if (e.pointerType === "mouse") return;
    const now = Date.now();
    if (now - lastTap > 320) {
      lastTap = now;
      return;
    }
    lastTap = 0;
    e.preventDefault();
    toggleZoom(e.clientX, e.clientY);
  };

  // 点击 stage 空白处（非图片、非拖拽后）关闭。stage 铺满全屏承载手势，
  // backdrop 永远被它盖住，所以"点背景关闭"必须在 stage 上判定。
  const onStageClick = (e) => {
    if (swipeMoved || pressedOnImage) return; // 平移/滑动/点图片，都不算点空白
    if (e.target !== stageEl) return; // capture 重定向失效的兜底
    close();
  };

  const bindStage = () => {
    stageEl.addEventListener("wheel", onWheel, { passive: false });
    stageEl.addEventListener("pointerdown", onPointerDown);
    stageEl.addEventListener("pointermove", onPointerMove);
    stageEl.addEventListener("pointerup", onPointerUp);
    stageEl.addEventListener("pointerup", onDoubleTap);
    stageEl.addEventListener("pointercancel", onPointerUp);
    stageEl.addEventListener("dblclick", onDblClick);
    stageEl.addEventListener("click", onStageClick);
    closeBtn.addEventListener("click", close);
    prevBtn.addEventListener("click", () => go(currentIndex - 1));
    nextBtn.addEventListener("click", () => go(currentIndex + 1));
    root.querySelector(".lbx-backdrop").addEventListener("click", close);
  };

  // ───────────────────────── 容器级委托点击 ─────────────────────────
  // 打开瞬间收集容器内全部合格图并定位 index——集合随 DOM 实时变化（懒加载、
  // 预览替换），预扫描建列表会过期；委托点击时现算，天然不过期。
  const onClick = (e) => {
    const target = e.target;
    const img = target && target.closest && target.closest("img");
    if (!img || !container.contains(img)) return;
    // 资格在点击时重算：懒加载图此刻可能刚好已解码（尺寸可判定）
    if (!isEligible(img)) return;
    e.preventDefault();

    const list = [];
    let index = 0;
    container.querySelectorAll("img").forEach((el) => {
      if (!isEligible(el)) return;
      const src = el.currentSrc || el.src;
      if (!src) return;
      if (el === img) index = list.length;
      list.push({
        src,
        alt: el.alt || el.title || "",
        fullSrc: el.dataset.lightboxSrc || el.dataset.zoomSrc || src,
      });
    });
    if (!list.length) return;
    open(index, list);
  };

  container.addEventListener("click", onClick, true);

  return function detach() {
    container.removeEventListener("click", onClick, true);
    observer.disconnect();
    container
      .querySelectorAll("img.lbx-candidate")
      .forEach((img) => img.classList.remove("lbx-candidate"));
    candidateStyle.remove();
    if (root) {
      const el = root;
      el.remove();
      root = null;
    }
    window.removeEventListener("keydown", onKeyDown, true);
    window.removeEventListener("resize", onResize);
    unlockScroll();
    if (isOpen && lastFocused instanceof HTMLElement) {
      lastFocused.focus({ preventScroll: true });
    }
  };
}

/* ── 本站接入：文献详情页（封面图表）与周报文章页正文自动挂载。
   脚本经 page_shell 注入且位于 body 末尾，DOM 已就绪；容器判断放行，
   其他页面（首页 / 搜索 / 周报列表）不挂载、不加载。 ── */
(function () {
  const pbody = document.querySelector(".pbody");
  if (pbody) attachImageLightbox(pbody);
  const wk = document.querySelector(".wk-content");
  if (wk) attachImageLightbox(wk);
})();
