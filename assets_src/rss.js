/* RSS 定制订阅配置页（/rss/）交互：期刊/类型 chips 正向多选 + 全选/反选 +
   标题语言 + 条数，实时拼 /rss/feed.atom 动态链接（复制按钮走 data-copy 通用机制）。
   站点 base 由页面 .rscp[data-base] 携带（构建期写入），本文件随 assets 原样
   拷贝、无构建期注入；脚本经 extra_assets 注入到 body 末尾，绑定元素已就绪。 */
(function(){try{
  var host = document.querySelector(".rscp");
  var base = host ? (host.getAttribute("data-base") || "") : "";
  function sel(id){
    return Array.prototype.slice.call(
      document.querySelectorAll("#" + id + " [aria-pressed='true']"))
      .map(function(b){ return b.getAttribute("data-v"); });
  }
  /* 组语义：全选 = 不过滤该维（返回空 → 链接省略参数，与「不选 = 全部」自洽） */
  function grp(id){
    var all = document.querySelectorAll("#" + id + " .rscp-chip");
    var on = Array.prototype.filter.call(all, function(b){
      return b.getAttribute("aria-pressed") === "true";
    });
    return on.length === all.length
      ? [] : on.map(function(b){ return b.getAttribute("data-v"); });
  }
  function q(k, v){ return v.length ? "&" + k + "=" + encodeURIComponent(v.join("|")) : ""; }
  function upd(){
    var j = grp("rJ"), t = grp("rT");
    var lang = sel("rL")[0] || "zh", n = sel("rN")[0] || "20";
    var url = base + "/rss/feed.atom?lang=" + lang + "&n=" + n + q("j", j) + q("t", t);
    document.getElementById("rssUrl").textContent = url;
  }
  document.querySelectorAll("#rJ .rscp-chip, #rT .rscp-chip").forEach(function(b){
    b.addEventListener("click", function(){
      b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true"));
      upd();
    });
  });
  document.querySelectorAll("#rJ .rscp-act, #rT .rscp-act").forEach(function(b){
    b.addEventListener("click", function(){
      var all = b.getAttribute("data-act") === "all";
      b.closest(".rscp-chips").querySelectorAll(".rscp-chip").forEach(function(c){
        c.setAttribute("aria-pressed",
          String(all ? true : c.getAttribute("aria-pressed") !== "true"));
      });
      upd();
    });
  });
  ["rL", "rN"].forEach(function(id){
    document.querySelectorAll("#" + id + " .rscp-lang").forEach(function(b){
      b.addEventListener("click", function(){
        document.querySelectorAll("#" + id + " .rscp-lang").forEach(function(x){
          x.setAttribute("aria-pressed", "false");
        });
        b.setAttribute("aria-pressed", "true");
        upd();
      });
    });
  });
  upd();
}catch(e){}})();
