/* ── 外链图多源降级（mystation layout.tsx 方案 + 本站原有 fastly/gcore 合并）：
   jsdelivr gh 直链逐源切换 cdn → fastly → gcore → ghfast(raw) → gh-proxy(raw) → raw；
   ghfast/gh-proxy 是 raw 直链的加速代理——jsDelivr 系（含 fastly/gcore）对超过
   单包 50MB 限制的仓库会一致拒发，只有 raw 系能取到超限后新增的文件。
   首页卡片封面（.c-cov）在非 jsdelivr 直链失败、或所有候选耗尽时，
   换成期刊缩写占位块（.c-ph，与无封面卡片同语言）——不留破图；
   周报等文章图候选耗尽换 .img-ph 占位块（样式在 style.css）——同样不留破图；
   灯箱内部图只换源、不做占位替换（失败提示由灯箱 caption 负责）。
   渲染路径脚本：head 内同步外链（parser-blocking），执行时机与内联等价——
   先于正文图片执行，捕获阶段 error 监听才能接到首张失败图 ── */
window.__coverPlaceholder = function (img) {
  var card = img.closest ? img.closest("a.card") : null;
  if (!card) return;
  var ph = document.createElement("span");
  ph.className = "c-ph";
  var jb = card.querySelector(".c-j");
  ph.textContent = (jb && jb.textContent) || "—";
  /* 连 c-covbox 一起换掉：占位块就位的同时把「加载中」层撤掉 */
  (img.closest(".c-covbox") || img).replaceWith(ph);
};
window.__articlePlaceholder = function (img) {
  img.dataset.fbDone = "1";
  var s = document.createElement("span");
  s.className = "img-ph";
  var w = img.getAttribute("width"), h = img.getAttribute("height");
  if (w && h) { s.style.aspectRatio = w + "/" + h; s.style.width = "100%"; }
  s.textContent = "图片加载失败（图源暂时不可用）";
  if (img.dataset.origSrc) s.title = img.dataset.origSrc;
  if (img.parentNode) img.parentNode.replaceChild(s, img);
};
window.__imgFallback = function (img) {
  if (img.dataset.fbDone === "1") return;
  /* 缩略外链失败 → 先回退原图外链（data-full），原图走下面的多源降级链 */
  if (img.dataset.full && !img.dataset.fullTried) {
    img.dataset.fullTried = "1";
    img.src = img.dataset.full;
    return;
  }
  var orig = img.dataset.origSrc || "";
  if (!orig) {
    orig = img.src;
    img.dataset.origSrc = orig;
    img.dataset.srcTry = "0";
  }
  /* 真实外链形如 gh/{user}/{repo}@{branch}/{path}（@ 前是「用户/仓库」两段） */
  var m = orig.match(/^https:\/\/cdn\.jsdelivr\.net\/gh\/([^\/@\s]+)\/([^\/@\s]+)(?:@([^\/\s]+))?\/(.+)$/);
  var cover = img.classList.contains("c-cov");
  if (!m) {
    if (cover) window.__coverPlaceholder(img);
    else window.__articlePlaceholder(img);
    return;
  }
  var spec = m[1] + "/" + m[2] + (m[3] ? "@" + m[3] : "");
  var n = parseInt(img.dataset.srcTry || "0", 10);
  var alts = ["https://fastly.jsdelivr.net/gh/" + spec + "/" + m[4],
              "https://gcore.jsdelivr.net/gh/" + spec + "/" + m[4]];
  if (m[3]) {
    var raw = "https://raw.githubusercontent.com/" + m[1] + "/" + m[2] + "/" + m[3] + "/" + m[4];
    alts.push("https://ghfast.top/" + raw, "https://gh-proxy.com/" + raw, raw);
  }
  if (n < alts.length) {
    img.dataset.srcTry = String(n + 1);
    img.src = alts[n];
  } else if (cover) {
    window.__coverPlaceholder(img);
  } else if (img.closest && img.closest("[data-lbx]")) {
    return;   /* 灯箱内部图：只换源不占位，失败提示由灯箱 caption 负责 */
  } else {
    window.__articlePlaceholder(img);
  }
};
document.addEventListener("error", function (e) {
  var t = e.target;
  if (!t || t.tagName === undefined || t.tagName !== "IMG" || t.dataset.fbDone === "1") return;
  /* 接管口径：封面图、jsdelivr 系直链、以及本脚本已接管的图——换源后 src
     离开 jsdelivr 域名（ghfast/gh-proxy/raw）也要继续跟完降级链 */
  if (t.classList.contains("c-cov") || t.dataset.origSrc !== undefined ||
      (t.src || "").indexOf("jsdelivr") > -1) window.__imgFallback(t);
}, true);
