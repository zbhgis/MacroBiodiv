/* ── 外链图多源降级（GeoSciPlot gallery.js 同源思路）：
   jsdelivr gh 直链逐源切换 cdn → fastly → gcore → raw.githubusercontent；
   首页卡片封面（.c-cov）在非 jsdelivr 直链失败、或所有候选耗尽时，
   换成期刊缩写占位块（.c-ph，与无封面卡片同语言）——不留破图；
   周报图表图维持原行为（候选耗尽即止，不做占位替换）。
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
window.__imgFallback = function (img) {
  /* 缩略外链失败 → 先回退原图外链（data-full），原图走下面的多源降级链 */
  if (img.dataset.full && !img.dataset.fullTried) {
    img.dataset.fullTried = "1";
    img.src = img.dataset.full;
    return;
  }
  var orig = img.dataset.origSrc || "";
  if (!orig) {
    orig = img.src;
    if (orig.indexOf("https://cdn.jsdelivr.net/gh/") !== 0) { window.__coverPlaceholder(img); return; }
    img.dataset.origSrc = orig;
    img.dataset.srcTry = "0";
  }
  /* 真实外链形如 gh/{user}/{repo}@{branch}/{path}（@ 前是「用户/仓库」两段） */
  var m = orig.match(/^https:\/\/cdn\.jsdelivr\.net\/gh\/([^\/@\s]+)\/([^\/@\s]+)(?:@([^\/\s]+))?\/(.+)$/);
  if (!m) { window.__coverPlaceholder(img); return; }
  var spec = m[1] + "/" + m[2] + (m[3] ? "@" + m[3] : "");
  var n = parseInt(img.dataset.srcTry || "0", 10);
  var alts = ["https://fastly.jsdelivr.net/gh/" + spec + "/" + m[4],
              "https://gcore.jsdelivr.net/gh/" + spec + "/" + m[4]];
  if (m[3]) alts.push("https://raw.githubusercontent.com/" + m[1] + "/" + m[2] + "/" + m[3] + "/" + m[4]);
  if (n < alts.length) {
    img.dataset.srcTry = String(n + 1);
    img.src = alts[n];
  } else {
    window.__coverPlaceholder(img);
  }
};
document.addEventListener("error", function (e) {
  var t = e.target;
  if (t && t.tagName === "IMG" && (t.classList.contains("c-cov") || t.src.indexOf("jsdelivr") > -1)) window.__imgFallback(t);
}, true);
