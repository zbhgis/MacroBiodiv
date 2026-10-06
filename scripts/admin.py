#!/usr/bin/env python3
"""MacroBiodiv 本地管理界面 —— 只有本机能访问（服务只绑定 127.0.0.1）。

用法：
    python scripts/admin.py                 # 自动打开 http://127.0.0.1:5201
    python scripts/admin.py --port 5201     # 换端口
    python scripts/admin.py --no-open       # 不自动打开浏览器

功能（流程照搬 GeoSciPlot：上传内容 → 填手动字段 → 点发布）：
    1. 粘贴 DOI（单条 / 多行批量 / 含 DOI 的任意文本）→ 按 DOI 自动抓取
       基本信息（Crossref + OpenAlex，见 fetch_doi.py）
       同时识别粘贴 / 拖拽的图片（封面图）：进入「待配区」，抓取成功后
       按顺序自动配给新文献（GeoSciPlot「标准导入」的同款配对逻辑）
    2. 逐条补 标签 / 中文标题 / 备注 / 封面图
    3. 文献管理：编辑手动字段、重新抓取、删除（勾选多选可批量删）
    4. 每周速递：上传周报 md → 落盘 content/weekly/（周报页自动收录）
       + 解析「# 文献N」的 DOI/图表 → 抓元数据入库（周报自带中文直接预填）
       + 图表图作封面（图表为无 → 站点 logo 兜底）
    5. 点「发布」→ 自动执行：写 meta/papers.json → build_site.py
       → git add / commit / push →（可选）同步到服务器

为什么不需要登录：服务只监听 127.0.0.1，物理上只有本机能连；推送用你本机已配置的
git 凭据（SSH key 或凭据管理器），**不需要在服务器上存任何 token**。

数据源说明：GeoSciPlot 的手动字段在 titles.csv 里留底，本站手动字段
（tags / title_zh / note）直接存 meta/papers.json —— 条目少、结构稳定，
单一数据源更不容易出不同步。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_doi import fetch_paper, normalize_doi, paper_id  # noqa: E402
from render_md import parse_front_matter, parse_weekly_papers  # noqa: E402
import llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
META_DIR = ROOT / "meta"
PAPERS_JSON = META_DIR / "papers.json"
SITE = ROOT / "site"                     # build_site.py 的产物目录（「同步服务器」用）
UI_HTML = Path(__file__).resolve().parent / "admin_ui.html"

PYTHON = sys.executable
DEFAULT_REMOTE = "https://github.com/zbhgis/MacroBiodiv.git"
MAX_BODY = 20 * 1024 * 1024
# 封面图（文章封面 / 图形摘要）：随 git 入库存 assets_src/covers/{id}.{ext}，
# build_site.py 构建时整体拷到 site/assets/covers/。papers.json 里的 cover 字段
# 存 "covers/{id}.{ext}"（相对 assets/），由服务端按 id 探测文件得出 —— 不信任
# 客户端传来的路径，文件在才是真相。
COVERS = ROOT / "assets_src" / "covers"
COVER_EXTS = ("png", "jpg", "jpeg", "webp", "gif")
COVER_MAX = 20 * 1024 * 1024          # 周报图表原图常超 10MB，上限放宽到 20MB
COVER_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
              "webp": "image/webp", "gif": "image/gif"}
# 手动字段：自动抓取不会覆盖；abstract_zh 由大模型初填，之后视同手动字段（可在界面修改，
# 重新抓取都会保留，只有显式「重新翻译」才重写）
MANUAL_TEXT = ("title_zh", "abstract_zh", "article_type", "note")
# keywords 抓取时自动预填（OpenAlex 词表，截前 10 个），仍可手动修改 —— 与 tags 一样
# 视作手动字段：编辑保存的值优先，重新抓取只在空缺时回填、绝不覆盖已填值
MANUAL_LIST = ("tags", "keywords")
# ── 每周速递：内容源 content/weekly/*.md（build_site.py 直接消费该目录），
#    上传的周报 md 在此落盘；文献封面缺图时用站点 logo 兜底 ──
WEEKLY_SRC = ROOT / "content" / "weekly"
LOGO_SRC = ROOT / "assets_src" / "logo.png"


# ────────────────────────── 工具 ──────────────────────────

def run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, f"命令超时：{' '.join(cmd)}"
    except FileNotFoundError as e:
        return 127, f"命令不存在：{e}"


def today() -> str:
    return date.today().isoformat()


def load_papers() -> dict:
    if PAPERS_JSON.exists():
        try:
            data = json.loads(PAPERS_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except json.JSONDecodeError:
            pass
    return {"generated": today(), "count": 0, "items": []}


def save_papers(papers: dict) -> None:
    papers["generated"] = today()
    papers["count"] = len(papers["items"])
    PAPERS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PAPERS_JSON.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def apply_manual(item: dict, upd: dict) -> None:
    """把手动字段（tags / title_zh / note）合并进条目，自动字段不动。"""
    for k in MANUAL_TEXT:
        if k in upd:
            item[k] = str(upd[k] or "").strip()
    for k in MANUAL_LIST:
        if k in upd:
            v = upd[k]
            if isinstance(v, list):
                item[k] = [str(t).strip() for t in v if str(t).strip()]
            else:
                item[k] = [t.strip() for t in str(v).split("|") if t.strip()]


# ────────────────────────── 封面图 ──────────────────────────

def _image_ext(raw: bytes) -> str:
    """魔数嗅探图片真实格式 —— data URL 声明的 MIME 可伪造，以文件头为准。"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return ""


def save_cover_bytes(fid: str, raw: bytes) -> tuple[bool, str]:
    """图片字节 → assets_src/covers/{fid}.{ext}（魔数定格式）。返回 (ok, cover路径或错误)。"""
    if not raw:
        return False, "图片数据为空"
    if len(raw) > COVER_MAX:
        return False, f"图片超过 {COVER_MAX // (1024 * 1024)}MB 上限"
    ext = _image_ext(raw)
    if not ext:
        return False, "只支持 PNG / JPEG / WebP / GIF"
    # 换格式上传时清掉旧格式文件，避免站点引用到过期副本
    for e in COVER_EXTS:
        old = COVERS / f"{fid}.{e}"
        if old.exists() and e != ext:
            old.unlink()
    COVERS.mkdir(parents=True, exist_ok=True)
    (COVERS / f"{fid}.{ext}").write_bytes(raw)
    return True, f"covers/{fid}.{ext}"


def save_cover(fid: str, data_url: str) -> tuple[bool, str]:
    """base64 data URL → assets_src/covers/{fid}.{ext}。返回 (ok, cover路径或错误信息)。"""
    if not re.fullmatch(r"[0-9a-f]{10}", fid or ""):
        return False, "id 不合法（须为 10 位十六进制）"
    m = re.match(r"data:image/[a-z.+-]+;base64,(.+)", data_url or "", re.S)
    if not m:
        return False, "不是 base64 图片数据"
    try:
        raw = base64.b64decode(m.group(1))
    except (binascii.Error, ValueError):
        return False, "base64 解码失败"
    return save_cover_bytes(fid, raw)


def remove_cover(fid: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{10}", fid or ""):
        return
    for e in COVER_EXTS:
        f = COVERS / f"{fid}.{e}"
        if f.exists():
            f.unlink()


def detect_cover(fid: str) -> str:
    """按 id 探测封面文件 → papers.json 的 cover 字段值；无则空串。"""
    if not re.fullmatch(r"[0-9a-f]{10}", fid or ""):
        return ""
    for e in COVER_EXTS:
        if (COVERS / f"{fid}.{e}").exists():
            return f"covers/{fid}.{e}"
    return ""


def set_cover_url(fid: str, url: str) -> tuple[bool, str]:
    """外链封面：cover 字段直接存完整 URL（与周报管线「外链直显」同模式，不落盘）。
    本地旧封面文件一并清掉 —— 编辑保存以磁盘文件为准，留着文件外链永远不会生效。
    返回 (ok, cover路径或错误信息)。"""
    if not re.fullmatch(r"[0-9a-f]{10}", fid or ""):
        return False, "无效的文献 id"
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False, "外链必须以 http:// 或 https:// 开头"
    papers = load_papers()
    hit = [it for it in papers.get("items", []) if it.get("id") == fid]
    if not hit:
        return False, "该文献尚未入库（新文献的外链会随条目在发布时写入）"
    remove_cover(fid)
    hit[0]["cover"] = u
    save_papers(papers)
    return True, u


def clear_cover_url(fid: str) -> None:
    """外链封面的「移除」：磁盘上没有文件可删，把 papers.json 里的外链字段清掉。
    只动外链值；本地文件封面照旧走 remove_cover + 编辑保存时按磁盘探测。"""
    if not re.fullmatch(r"[0-9a-f]{10}", fid or ""):
        return
    papers = load_papers()
    hit = [it for it in papers.get("items", [])
           if it.get("id") == fid and str(it.get("cover") or "").startswith(("http://", "https://"))]
    if hit:
        for it in hit:
            it.pop("cover", None)
        save_papers(papers)


def repo_state() -> dict:
    papers = load_papers()
    items = papers.get("items", [])
    is_repo = (ROOT / ".git").is_dir()
    remote = ""
    branch = "main"
    dirty = 0
    if is_repo:
        code, out = run(["git", "remote", "get-url", "origin"])
        remote = out if code == 0 else ""
        code, out = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        branch = out if code == 0 else "main"
        code, out = run(["git", "status", "--porcelain"])
        dirty = len([l for l in out.splitlines() if l.strip()]) if code == 0 else 0
    return {
        "isRepo": is_repo,
        "remote": remote,
        "branch": branch,
        "dirty": dirty,
        "count": len(items),
        "tags": sorted({t for i in items for t in (i.get("tags") or [])}),
        "journals": sorted({i.get("journal") for i in items if i.get("journal")}),
        "defaultRemote": DEFAULT_REMOTE,
        "weekly": len(list(WEEKLY_SRC.glob("*.md"))) if WEEKLY_SRC.is_dir() else 0,
    }


# ────────────────────────── 后台任务系统 ──────────────────────────
# 发布/删除/刷新/推送都是「生成站点 + git push + scp 服务器」或网络批量操作，
# 同步执行会让界面停滞。POST 立即返回任务号，后台线程跑流水线，
# 前端轮询 /api/job/<id> 看实时进度。

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
# papers.json 的读写锁：发布/更新/刷新/补译/删除都是「读→改→写回」，
# 并发执行（如补译未完又点发布）会互相覆盖丢数据，统一在此串行化
DATA_LOCK = threading.Lock()


def step(log: list[dict], name: str, cmd: list[str] | None, timeout: int = 300) -> tuple[bool, str]:
    """带「运行中」标记的步骤：先写入 ok=None 条目（前端显示 …），命令结束后回填结果。"""
    log.append({"step": name, "out": "", "ok": None})
    if cmd is None:
        return True, ""
    code, out = run(cmd, timeout)
    entry = log[-1]
    entry["ok"] = code == 0
    entry["out"] = out
    return entry["ok"], out


def start_job(kind: str, body: dict) -> str:
    """启动后台任务，立即返回任务号。"""
    jid = uuid.uuid4().hex[:8]
    job = {"kind": kind, "log": [], "done": False, "ok": None, "hint": ""}
    with JOBS_LOCK:
        JOBS[jid] = job
        done_ids = [k for k, v in JOBS.items() if v["done"]]
        for k in done_ids[:-20]:          # 只保留最近 20 个已完成的任务
            JOBS.pop(k, None)

    def worker():
        try:
            if kind == "publish":
                res = do_publish(body.get("items") or [], body.get("message") or "",
                                 bool(body.get("push", True)), bool(body.get("sync", True)))
            elif kind == "update":
                res = do_update_and_finish(body.get("items") or [], body.get("message") or "",
                                           bool(body.get("push", True)), bool(body.get("sync", True)))
            elif kind == "refresh":
                res = do_refresh_and_finish(body.get("ids") or [], body.get("message") or "",
                                            bool(body.get("push", True)), bool(body.get("sync", True)))
            elif kind == "translate":
                res = do_translate_and_finish(body.get("ids") or [], bool(body.get("only_missing")),
                                              body.get("message") or "",
                                              bool(body.get("push", True)), bool(body.get("sync", True)))
            elif kind == "delete":
                ids = body.get("ids")
                if not ids and body.get("id"):
                    ids = [body.get("id")]      # 兼容旧客户端的单 id 入参
                res = do_delete_and_finish(ids or [], body.get("message") or "",
                                           bool(body.get("push", True)), bool(body.get("sync", True)))
            elif kind == "weekly":
                res = do_weekly_and_finish(body.get("name") or "", body.get("content") or "",
                                           body.get("message") or "",
                                           bool(body.get("push", True)), bool(body.get("sync", True)))
            elif kind == "weekly-delete":
                names = body.get("names")
                if not names and body.get("name"):
                    names = [body.get("name")]      # 兼容旧客户端的单文件名入参
                res = do_weekly_delete_and_finish(names or [], body.get("message") or "",
                                                  bool(body.get("push", True)), bool(body.get("sync", True)))
            elif kind == "sync-server":
                res = do_sync_server()
            elif kind == "init":
                res = do_init(body.get("remote") or "")
            elif kind == "push":
                res = do_push()
            else:
                res = {"ok": False, "log": [{"step": kind, "ok": False, "out": "未知任务类型"}]}
        except Exception as e:            # 后台线程绝不能静默死掉
            res = {"ok": False, "hint": "", "log": [{"step": "后台任务异常", "ok": False, "out": repr(e)}]}
        with JOBS_LOCK:
            job["log"] = res.get("log", job["log"])
            job["done"] = True
            job["ok"] = bool(res.get("ok"))
            job["hint"] = res.get("hint", "")

    threading.Thread(target=worker, daemon=True, name=f"job-{kind}-{jid}").start()
    return jid


def job_snapshot(jid: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if not job:
            return {"done": True, "ok": False, "log": [], "hint": "任务不存在（服务重启过？刷新页面重试）"}
        return {"done": job["done"], "ok": job["ok"], "hint": job["hint"],
                "log": [dict(x) for x in job["log"]]}


# ────────────────────────── 抓取与入库 ──────────────────────────

def do_fetch(dois: list) -> dict:
    """按 DOI 批量抓取（顺序执行、礼貌限速），返回每条的抓取结果供表单预览。
    纯抓取不改 papers.json —— 入库统一发生在「发布」。"""
    out = []
    known = {str(i.get("doi") or "").lower() for i in load_papers().get("items", [])}
    for i, raw in enumerate(dois):
        if i:
            time.sleep(0.3)
        doi = normalize_doi(str(raw or ""))
        rec: dict = {"input": str(raw or ""), "doi": doi, "ok": False}
        if not doi:
            rec["error"] = "无法从输入中提取 DOI"
            out.append(rec)
            continue
        if doi.lower() in known:
            rec["duplicate"] = True
            rec["error"] = "该 DOI 已在文献库中，跳过"
            out.append(rec)
            continue
        try:
            record = fetch_paper(doi)
            record["id"] = paper_id(record["doi"])
            record["title_zh"] = ""
            record["abstract_zh"] = ""
            record["article_type"] = ""
            record["note"] = ""
            record["tags"] = []
            rec["ok"] = True
            rec["record"] = record
            known.add(doi.lower())        # 同批次里再遇到同一 DOI 也算重复
            # ── 自动中文翻译（两重：初译+审校）。失败不阻断抓取，界面会提示。
            #    budget=200：浏览器在同步等这个请求，翻译须在预算内给出结果
            #    （超预算自动降级为仅初译；初译本身失败才整体失败） ──
            if llm.llm_configured():
                try:
                    hint = record.get("oa_type") or record.get("type") or ""
                    res = llm.translate_paper(record.get("title", ""), record.get("abstract", ""),
                                              budget=200, type_hint=hint)
                    record["title_zh"] = res["title_zh"]
                    record["abstract_zh"] = res["abstract_zh"]
                    record["article_type"] = res.get("article_type", "")
                    rec["translated"] = True
                    rec["passes"] = res.get("passes", 1)
                    rec["tnote"] = res.get("note", "")
                except llm.LLMError as e:
                    rec["translate_error"] = str(e)
            else:
                rec["translate_error"] = "未配置大模型（LLM_API_KEY 等环境变量），中文留空可稍后手动填写"
        except Exception as e:
            rec["error"] = str(e) or repr(e)
        out.append(rec)
    return {"items": out}


def do_publish(items: list, message: str, push: bool, sync: bool) -> dict:
    """把「待发布」条目写入 papers.json → 构建站点 → git → 同步服务器。"""
    log: list[dict] = []
    added, skipped, failed = 0, 0, 0
    with DATA_LOCK:
        papers = load_papers()
        by_doi = {str(i.get("doi") or "").lower(): i for i in papers["items"]}

        for it in items:
            doi = normalize_doi(str(it.get("doi") or ""))
            if not doi:
                failed += 1
                continue
            if doi.lower() in by_doi:
                skipped += 1
                continue
            rec = dict(it.get("record") or {})
            title = str(rec.get("title") or "").strip()
            if not title:
                failed += 1
                log.append({"step": f"跳过 {doi}", "ok": False, "out": "缺少标题（抓取记录不完整）"})
                continue
            # 服务端重新定死关键字段，不信任 UI 传来的 id / doi 形态
            rec["doi"] = doi
            rec["id"] = paper_id(doi)
            rec.setdefault("added", today())
            apply_manual(rec, it)
            # 封面：按 id 探测 assets_src/covers/ 里的实际文件（上传接口早已落盘）
            cov = detect_cover(rec["id"])
            if cov:
                rec["cover"] = cov
            by_doi[doi.lower()] = rec
            added += 1

        papers["items"] = list(by_doi.values())
        save_papers(papers)
    summary = f"papers.json 新增 {added} 条"
    if skipped:
        summary += f"，跳过重复 {skipped} 条"
    if failed:
        summary += f"，失败 {failed} 条"
    log.append({"step": "写入文献索引", "ok": added > 0, "out": summary})
    if added == 0:
        return {"ok": False, "log": log, "hint": "没有新增文献（全部重复或无效），未构建站点"}

    return pipeline_after_content(log, message or f"add: {added} 篇文献", push, sync)


def do_update(updates: list) -> dict:
    """批量更新手动字段（tags / title_zh / abstract_zh / article_type / note / keywords），
    不动自动抓取字段。"""
    upd_map = {}
    for u in updates:
        fid = (u.get("id") or "").strip()
        if fid:
            upd_map[fid] = u
    if not upd_map:
        return {"ok": False, "log": [{"step": "更新元数据", "ok": False, "out": "没有可更新的字段"}]}

    n = 0
    with DATA_LOCK:
        papers = load_papers()
        for it in papers["items"]:
            upd = upd_map.get(it.get("id"))
            if upd:
                apply_manual(it, upd)
                # 封面以磁盘文件为准：编辑中上传/移除过封面，这里同步增删字段
                # （cover 为外链 URL 时不适用 —— 外链不落盘，保留原值）
                cov = detect_cover(it.get("id"))
                if cov:
                    it["cover"] = cov
                elif not str(it.get("cover") or "").startswith(("http://", "https://")):
                    it.pop("cover", None)
                n += 1
        save_papers(papers)
    return {"ok": True,
            "log": [{"step": "更新元数据", "ok": True, "out": f"papers.json 更新 {n} 条"}]}


def do_refresh(ids: list) -> dict:
    """重新抓取指定条目的元数据（保留 tags / title_zh / note / added）。"""
    want = {i for i in ids if i}
    ok_n, err_n = 0, 0
    log: list[dict] = []
    with DATA_LOCK:
        papers = load_papers()
        for idx, it in enumerate(papers["items"]):
            if it.get("id") not in want:
                continue
            if idx:
                time.sleep(0.3)
            try:
                record = fetch_paper(str(it.get("doi") or ""))
                keep = {k: it[k] for k in ("id", "added", "title_zh", "abstract_zh", "article_type", "note", "tags", "cover", "cover_thumb", "cover_thumb_w", "cover_thumb_h") if k in it}
                if it.get("keywords"):
                    keep["keywords"] = it["keywords"]   # 手动填过才保留；空缺由重新抓取回填
                record.update(keep)
                record["id"] = it.get("id")     # id 由入库时的 DOI 算出，保持不变
                cov = detect_cover(record["id"])
                if cov:
                    record["cover"] = cov
                elif not str(record.get("cover") or "").startswith(("http://", "https://")):
                    record.pop("cover", None)   # 外链封面不落盘，本地探测不到也不清除
                papers["items"][idx] = record
                ok_n += 1
                log.append({"step": f"刷新 {it.get('id')}", "ok": True,
                            "out": (record.get("title") or "")[:60]})
            except Exception as e:
                err_n += 1
                log.append({"step": f"刷新 {it.get('id')}", "ok": False, "out": str(e)})
        save_papers(papers)
    if not log:
        return {"ok": False, "log": [{"step": "重新抓取", "ok": False, "out": "没有匹配的条目"}]}
    return {"ok": err_n == 0, "log": log,
            "hint": "" if err_n == 0 else f"{err_n} 条刷新失败（多为网络问题，稍后重试）"}


def do_translate(ids: list, only_missing: bool = False) -> dict:
    """大模型两重翻译（初译+审校）写条目的 title_zh / abstract_zh。

    only_missing=True（「翻译缺中文的」批量入口）：只补空缺，绝不覆盖已有译文
    —— 这是限流/故障后的批量回补通道，安全的幂等操作。
    默认模式（条目上的「重新翻译」按钮）：显式要求重写，会覆盖现有中文。
    """
    if not llm.llm_configured():
        return {"ok": False,
                "log": [{"step": "翻译", "ok": False,
                         "out": "未配置大模型：请先设置环境变量 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME"}]}
    want = {i for i in ids if i}
    log: list[dict] = []
    ok_n, err_n, skip_n = 0, 0, 0
    papers = None
    with DATA_LOCK:
        papers = load_papers()
        for idx, it in enumerate(papers["items"]):
            if it.get("id") not in want:
                continue
            if only_missing and str(it.get("title_zh") or "").strip() \
                    and (str(it.get("abstract_zh") or "").strip() or not str(it.get("abstract") or "").strip()):
                skip_n += 1
                log.append({"step": f"翻译 {it.get('id')}", "ok": True, "out": "已有中文，跳过（不覆盖）"})
                continue
            # 断路冷却中且已有失败：剩余条目注定失败，直接中止而不是逐条空转
            cool = llm.cooling_down()
            if cool and err_n:
                left = len(want) - ok_n - err_n - skip_n
                log.append({"step": "中止剩余翻译", "ok": False,
                            "out": f"LLM 断路冷却（剩 {cool}s）——服务刚连续失败过，剩余 {left} 条未处理；"
                                   f"稍后用「翻译缺中文的」批量回补"})
                break
            title = str(it.get("title") or "").strip()
            if not title:
                log.append({"step": f"翻译 {it.get('id')}", "ok": False, "out": "缺少英文标题"})
                err_n += 1
                continue
            try:
                # budget=600：后台任务没有浏览器在等，预算放宽
                hint = str(it.get("oa_type") or it.get("type") or "")
                res = llm.translate_paper(title, str(it.get("abstract") or ""), budget=600,
                                          type_hint=hint)
                # 覆写保护：模型偶尔丢字段，空结果不落盘，保留现有译文
                if res.get("title_zh"):
                    it["title_zh"] = res["title_zh"]
                if res.get("abstract_zh"):
                    it["abstract_zh"] = res["abstract_zh"]
                if res.get("article_type"):
                    it["article_type"] = res["article_type"]
                ok_n += 1
                tag = "初译+审校" if res.get("passes", 2) >= 2 else "仅初译"
                note = res.get("note") or ""
                log.append({"step": f"翻译 {it.get('id')}", "ok": True,
                            "out": f"{it.get('title_zh', '')[:40]}（{tag}{'：' + note if note else ''}）"})
            except llm.LLMError as e:
                err_n += 1
                log.append({"step": f"翻译 {it.get('id')}", "ok": False, "out": str(e)})
            time.sleep(0.5)     # 每篇间隔，配合 llm 层的请求节流
        save_papers(papers)
    if not log:
        return {"ok": False, "log": [{"step": "翻译", "ok": False, "out": "没有匹配的条目"}]}
    summary = f"成功 {ok_n} 条" + (f"，跳过 {skip_n} 条" if skip_n else "") \
              + (f"，失败 {err_n} 条" if err_n else "")
    log.insert(0, {"step": "翻译汇总", "ok": err_n == 0, "out": summary})
    return {"ok": err_n == 0, "changed": ok_n, "log": log,
            "hint": "" if err_n == 0 else f"{err_n} 条翻译失败（见日志）——失败的可先发布英文版，稍后用「翻译缺中文的」补齐"}


def do_delete(ids) -> dict:
    """批量删除：ids（列表；兼容单个 id 字符串）每一条都从 papers.json 移除并清掉封面文件。
    部分成功也算成功 —— 逐条记日志，返回 removed 供默认提交信息使用。"""
    want: list[str] = []
    for raw in (ids if isinstance(ids, list) else [ids]):
        s = str(raw or "").strip()
        if s and s not in want:
            want.append(s)
    if not want:
        return {"ok": False, "log": [{"step": "删除", "ok": False, "out": "没有指定要删除的条目"}]}

    log: list[dict] = []
    with DATA_LOCK:
        papers = load_papers()
        items = papers.get("items", [])
        by_id = {x.get("id"): x for x in items}
        removed = [by_id[i] for i in want if i in by_id]
        missing = [i for i in want if i not in by_id]
        if not removed:
            return {"ok": False,
                    "log": [{"step": "删除", "ok": False, "out": "id 均不存在：" + " ".join(missing)}]}
        drop = {x.get("id") for x in removed}
        papers["items"] = [x for x in items if x.get("id") not in drop]
        save_papers(papers)
    for it in removed:
        remove_cover(str(it.get("id") or ""))   # 封面文件随条目一起删（站点重建后不再引用）
        log.append({"step": f"删除 {it.get('id')}", "ok": True,
                    "out": f"已移除「{str(it.get('title') or '')[:50]}」"})
    for fid in missing:
        log.append({"step": f"删除 {fid}", "ok": False, "out": "id 不存在（可能已被删除）"})
    return {"ok": True, "removed": [str(x.get("id") or "") for x in removed], "log": log}


# ────────────────────────── 发布流水线 ──────────────────────────

def do_sync_server() -> dict:
    cfg = {}
    cfg_path = ROOT / "site.config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    srv = cfg.get("server") or {}
    host = srv.get("host")
    webroot = srv.get("webroot", "/var/www/macrobiodiv")
    if not host:
        return {"ok": False,
                "log": [{"step": "同步服务器", "ok": False,
                         "out": "未配置 server.host —— 在 site.config.json 里加 "
                                "\"server\": {\"host\": \"root@47.98.133.104\", \"webroot\": \"/var/www/macrobiodiv\"}"}]}

    ssh_base = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", host]
    # 原子换台式同步：先整体传到 staging 目录，成功后再毫秒级 rename 就位。
    # 旧流程「清空 webroot → 逐文件 scp」有分钟级发布空窗——期间访问 /
    # 是 403（目录在但 index.html 未传到），访问详情页会拿到引用缺失资源的
    # 半套页面（2026-10-04 实证）。staging 失败时旧站点原样保留，发布失败
    # 不再破坏线上。
    stage = webroot + ".staging"
    code, out = run(ssh_base + [f"rm -rf {stage} && mkdir -p {stage}"],
                    timeout=60)
    if code != 0:
        return {"ok": False, "log": [{"step": "同步服务器", "ok": False,
                                      "out": "SSH 连接失败（需先把本机公钥加入服务器 authorized_keys，"
                                             "命令见 README「服务器部署」一节）\n" + out}]}

    code, out = run(["scp", "-r", "-o", "BatchMode=yes", str(SITE) + "/.", f"{host}:{stage}/"], timeout=600)
    if code != 0:
        run(ssh_base + [f"rm -rf {stage}"], timeout=60)  # 清掉残局，线上保持旧版本
        return {"ok": False, "log": [{"step": "同步服务器", "ok": False, "out": "scp 失败（线上保持旧版本）\n" + out}]}
    code, out = run(ssh_base + [f"mv {webroot} {webroot}.old 2>/dev/null; "
                                f"mv {stage} {webroot} && rm -rf {webroot}.old"], timeout=120)
    if code != 0:
        return {"ok": False, "log": [{"step": "同步服务器", "ok": False, "out": "切换 staging 失败\n" + out}]}
    # 部署后搜索引擎推送：服务器端 ping-search.sh 按 sitemap 增量对比推 IndexNow
    # （脚本与主站共用同一份，按传入 URL 分账状态；失败只记日志不影响发布）
    site_url = (cfg.get("site_url") or "").rstrip("/")
    if site_url:
        ping, ping_out = run(ssh_base + [f"bash /opt/mystation/deploy/ping-search.sh {site_url}"], timeout=120)
        ping_log = {"step": "搜索引擎推送（IndexNow）", "ok": ping == 0, "out": ping_out.strip() or "完成"}
        return {"ok": True, "log": [{"step": "同步服务器", "ok": True, "out": f"site/ → {host}:{webroot}"},
                                    ping_log]}
    return {"ok": True, "log": [{"step": "同步服务器", "ok": True, "out": f"site/ → {host}:{webroot}"}]}


def pipeline_after_content(log: list[dict], message: str, push: bool, sync: bool) -> dict:
    """内容变更后的公共收尾：封面缩略图 → 生成站点 → git 提交推送 →（可选）同步服务器。
    所有步骤用 step() 执行，前端能看到每一步的实时状态。"""
    # 增量本地化封面（下载原图入本仓库 images/ → 压缩缩略图 → cover/cover_thumb
    # 改写为本仓库 jsDelivr 链接）。必须在 build 之前：新引用要进本次构建产物。
    # 失败不阻断发布 —— 缺缩略图的卡片自动回退原图外链，下次发布自动重试
    ok_t, out_t = step(log, "封面图片本地化（增量 → 本仓库 images/）",
                       [PYTHON, "scripts/prepare_thumbs.py", "--quiet"], timeout=580)
    if not ok_t:
        log.append({"step": "封面缩略图（未阻断发布）", "ok": False,
                    "out": "缺缩略图的卡片回退原图外链；下次发布会自动重试\n" + out_t[:300]})

    ok, out = step(log, "生成静态站 (build_site.py)", [PYTHON, "scripts/build_site.py"])
    if not ok:
        return {"ok": False, "log": log, "hint": "build_site.py 失败，未提交"}

    if not (ROOT / ".git").is_dir():
        log.append({"step": "Git", "ok": False,
                    "out": "尚未初始化 git 仓库 —— 点上方「初始化仓库」后再发布"})
        return {"ok": False, "log": log, "hint": "请先初始化 git 仓库"}

    if not message.strip():
        message = "update: 文献库内容变更"

    step(log, "git add", ["git", "add", "-A"])
    ok, out = step(log, f"git commit -m \"{message}\"", ["git", "commit", "-m", message])
    if not (ok or "nothing to commit" in out):
        return {"ok": False, "log": log, "hint": "提交失败（检查 git user.name / user.email）"}

    if push:
        # 国内网络 push 到 GitHub 可能很慢，后台任务模式下超时放宽到 5 分钟
        ok, out = step(log, "git push（推送到 GitHub）", ["git", "push"], timeout=300)
        if not ok:
            return {"ok": False, "log": log, "hint": "推送失败：检查 origin 与凭据（也可能是网络波动，稍后手动 git push）"}

    if sync:
        log.append({"step": "同步服务器（staging 原子换台）", "out": "", "ok": None})
        res = do_sync_server()
        log[-1]["ok"] = bool(res["ok"])
        log[-1]["out"] = "\n".join(x.get("out", "") for x in res["log"])
        if not res["ok"]:
            return {"ok": True, "log": log,
                    "hint": "GitHub 已同步；服务器同步失败（见日志）——通常是本机公钥还没加到服务器的 authorized_keys，"
                            "也可以手动在 Git Bash 里 cd site && scp -r ./* root@47.98.133.104:/var/www/macrobiodiv/"}

    return {"ok": True, "log": log}


def do_push() -> dict:
    log: list[dict] = []
    ok, out = step(log, "git push（推送到 GitHub）", ["git", "push"], timeout=300)
    if not ok:
        return {"ok": False, "log": log, "hint": "推送失败：检查 origin 与凭据（也可能是网络波动，稍后重试）"}
    return {"ok": True, "log": log}


def do_init(remote: str) -> dict:
    log: list[dict] = []
    if not remote.strip():
        remote = DEFAULT_REMOTE

    ok, _ = step(log, "git init", ["git", "init"])
    if not ok:
        return {"ok": False, "log": log, "hint": "git init 失败"}

    ok, out = step(log, f"git remote add origin {remote}", ["git", "remote", "add", "origin", remote])
    if not ok:
        ok, out = step(log, "remote 已存在，改为 set-url", ["git", "remote", "set-url", "origin", remote])
        if not ok:
            return {"ok": False, "log": log, "hint": "设置 origin 失败"}

    step(log, "git add -A", ["git", "add", "-A"])
    code, out = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = out if code == 0 and out else "main"
    ok, out = step(log, "git commit（首提）", ["git", "commit", "-m", "init: 初始提交"])
    if not (ok or "nothing to commit" in out):
        return {"ok": False, "log": log, "hint": "首次提交失败（检查 git user.name / user.email）"}

    ok, out = step(log, f"git push -u origin {branch}", ["git", "push", "-u", "origin", branch], timeout=300)
    if not ok:
        return {"ok": False, "log": log, "hint": "首次推送失败：检查远端仓库与凭据"}
    return {"ok": True, "log": log}


def do_update_and_finish(updates: list, message: str, push: bool, sync: bool) -> dict:
    res = do_update(updates)
    if not res.get("ok"):
        return res
    log = res.get("log", [])
    return pipeline_after_content(log, message or "update: 文献元信息变更", push, sync)


def do_refresh_and_finish(ids: list, message: str, push: bool, sync: bool) -> dict:
    res = do_refresh(ids)
    if not res.get("ok") and not res.get("log"):
        return res
    log = res.get("log", [])
    out = pipeline_after_content(log, message or "update: 重新抓取文献元数据", push, sync)
    if res.get("hint"):
        out["hint"] = res["hint"]
    return out


def do_translate_and_finish(ids: list, only_missing: bool, message: str, push: bool, sync: bool) -> dict:
    res = do_translate(ids, only_missing=only_missing)
    if not res.get("changed"):
        return res          # 没有条目被实际翻译（全跳过/全失败），不必构建站点
    log = res.get("log", [])
    out = pipeline_after_content(log, message or "update: 大模型翻译中文化", push, sync)
    if res.get("hint"):
        out["hint"] = res["hint"]
    return out


def do_delete_and_finish(ids, message: str, push: bool, sync: bool) -> dict:
    res = do_delete(ids)
    if not res.get("ok"):
        return res
    log = res.get("log", [])
    removed = res.get("removed") or []
    if not message.strip():
        if len(removed) == 1:
            message = f"delete: 文献 {removed[0]}"          # 与既有单条删除的提交信息同格式
        else:
            preview = " ".join(removed[:5]) + (" 等" if len(removed) > 5 else "")
            message = f"delete: 文献 {len(removed)} 篇（{preview}）"
    return pipeline_after_content(log, message, push, sync)


# ────────────────────────── 每周速递 ──────────────────────────
# 上传周报 md → ① 落盘 content/weekly/（周报页面随构建自动收录）
#               ② 解析「# 文献N」小节：DOI → Crossref/OpenAlex 抓元数据入库
#                  （周报自带的中文标题/摘要/体裁注记直接预填，省大模型预算）
#               ③ 图表图下载为封面；图表为无 / 下载失败 → 站点 logo 兜底
# 生成的文献条目进 papers.json，与手动添加的一起在「文献管理」里统一管理。

def weekly_safe_name(name: str) -> str:
    """上传文件名清洗：去路径成分与非法字符，缺 .md 补上。"""
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "", name).strip()
    if not name:
        return ""
    if not name.lower().endswith(".md"):
        name += ".md"
    return name


def weekly_slug(title: str, stem: str) -> str:
    """期号 slug，与 build_site.py 同规则：标题里的「精选N」优先。"""
    m = re.search(r"精选(\d+)", title or "")
    return f"weekly-{m.group(1) if m else (stem or '')[:8]}"


def weekly_cover(fid: str, log: list, label: str) -> None:
    """图表为无：复制站点 logo 作封面兜底（assets_src/logo.png → covers/{fid}.png）。
    有图表时封面直接走外链直显（cover 字段存完整 URL），不再下载落盘。"""
    if LOGO_SRC.exists():
        COVERS.mkdir(parents=True, exist_ok=True)
        (COVERS / f"{fid}.png").write_bytes(LOGO_SRC.read_bytes())
        log.append({"step": f"封面 {label}", "ok": True, "out": "图表为无 → 用站点 logo 兜底"})
    else:
        log.append({"step": f"封面 {label}", "ok": False, "out": "assets_src/logo.png 不存在，该篇无封面"})


def _is_logo_cover(fid: str) -> bool:
    """封面是否为「logo 兜底」（字节与站点 logo 相同的 png）—— 图表为无时的落盘标记；
    周报重新上传同 DOI 时若解析到图表图，会把封面升级为外链直显。"""
    f = COVERS / f"{fid}.png"
    return LOGO_SRC.exists() and f.exists() and f.read_bytes() == LOGO_SRC.read_bytes()


def weekly_issues() -> dict:
    """content/weekly/ 全部期次概览：期号、日期、DOI 及其入库状态（管理列表用）。"""
    known = {str(i.get("doi") or "").lower() for i in load_papers().get("items", [])}
    base = ""
    cfg_path = ROOT / "site.config.json"
    if cfg_path.exists():
        try:
            base = (json.loads(cfg_path.read_text(encoding="utf-8")).get("site_url") or "").rstrip("/")
        except (json.JSONDecodeError, OSError):
            pass
    issues = []
    if WEEKLY_SRC.is_dir():
        for md in sorted(WEEKLY_SRC.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _ = parse_front_matter(text)
            parsed = parse_weekly_papers(text)
            title = str(meta.get("title") or md.stem).strip()
            dois = []
            n_logo = 0
            for p in parsed:
                doi = normalize_doi(p["doi_line"] or "")
                in_lib = bool(doi) and doi.lower() in known
                dois.append({"doi": doi, "inLib": in_lib,
                             "hasImg": bool(p["images"])})
                # 已入库但封面还是 logo 兜底的篇数（重传本文件即可自动补真图）
                if in_lib and _is_logo_cover(paper_id(doi)):
                    n_logo += 1
            issues.append({
                "file": md.name, "title": title,
                "date": str(meta.get("date") or "")[:10],
                "slug": weekly_slug(title, md.stem),
                "n": len(parsed),
                "nDoi": sum(1 for d in dois if d["doi"]),
                "inLib": sum(1 for d in dois if d["inLib"]),
                "nLogo": n_logo,
                "dois": dois,
            })
    issues.sort(key=lambda x: (x["date"], x["file"]), reverse=True)
    return {"issues": issues, "siteUrl": base}


def weekly_preview(name: str, content: str) -> dict:
    """上传前预解析（无副作用）：给确认框展示将收录哪些文献。"""
    fname = weekly_safe_name(name)
    if not fname:
        return {"ok": False, "error": "文件名不合法"}
    if not (content or "").strip():
        return {"ok": False, "error": "文件内容为空"}
    meta, _ = parse_front_matter(content)
    parsed = parse_weekly_papers(content)
    known = {str(i.get("doi") or "").lower() for i in load_papers().get("items", [])}
    items = []
    for p in parsed:
        doi = normalize_doi(p["doi_line"] or "")
        items.append({"no": p["no"], "doi": doi,
                      "title": p["title_en"] or p["title_zh"] or "?",
                      "title_zh": p["title_zh"], "journal": p["journal"],
                      "genre": p["genre"], "hasImg": bool(p["images"]),
                      "inLib": bool(doi) and doi.lower() in known})
    warnings = []
    if not meta:
        warnings.append("未识别到 front matter（title/date），周报页将回退用文件名作标题")
    n_nodoi = sum(1 for it in items if not it["doi"])
    if n_nodoi:
        warnings.append(f"{n_nodoi} 篇未解析到 DOI，这些篇目不会生成文献卡片")
    title = str(meta.get("title") or "").strip()
    slug = weekly_slug(title, Path(fname).stem)
    if WEEKLY_SRC.is_dir():
        for md in WEEKLY_SRC.glob("*.md"):
            if md.name == fname:
                continue
            try:
                m2, _ = parse_front_matter(md.read_text(encoding="utf-8"))
            except OSError:
                continue
            if weekly_slug(str(m2.get("title") or md.stem), md.stem) == slug:
                warnings.append(f"期号与已有《{md.name}」相同（slug 均为 {slug}），构建时两条目的页面会互相覆盖")
                break
    return {"ok": True, "file": fname, "title": title or fname,
            "date": str(meta.get("date") or "")[:10], "slug": slug,
            "overwrite": (WEEKLY_SRC / fname).exists(),
            "n": len(items), "items": items, "warnings": warnings}


def do_weekly_import(name: str, content: str, log: list) -> dict:
    """落盘周报 md + 解析入库（不动站点构建，由调用方接发布流水线）。"""
    fname = weekly_safe_name(name)
    if not fname or not (content or "").strip():
        log.append({"step": "校验文件", "ok": False, "out": "文件名或内容不合法"})
        return {"ok": False}
    meta, _ = parse_front_matter(content)
    parsed = parse_weekly_papers(content)
    title = str(meta.get("title") or "").strip() or fname
    log.append({"step": "解析周报", "ok": True, "out": f"《{title}》识别到 {len(parsed)} 篇文献"})

    WEEKLY_SRC.mkdir(parents=True, exist_ok=True)
    (WEEKLY_SRC / fname).write_text(content, encoding="utf-8", newline="\n")
    log.append({"step": "落盘内容源", "ok": True,
                "out": f"content/weekly/{fname}（周报页随本次构建自动收录）"})

    added, skipped, failed = 0, 0, 0
    dirty = False
    with DATA_LOCK:
        papers = load_papers()
        by_doi = {str(i.get("doi") or "").lower(): i for i in papers["items"]}
        for idx, p in enumerate(parsed):
            label = f"文献{p['no']}"
            doi = normalize_doi(p["doi_line"] or "")
            if not doi:
                log.append({"step": label, "ok": False, "out": "未解析到 DOI，跳过（不生成卡片）"})
                failed += 1
                continue
            if doi.lower() in by_doi:
                # 已在库也要看一眼封面：上次导入图表为无会落 logo 兜底，这次重传
                # 同一期且解析到图表 → 封面升级为外链直显（日志标「补封面」）
                ex = by_doi[doi.lower()]
                cur = str(ex.get("cover") or "")
                if p["images"] and (not cur or _is_logo_cover(str(ex.get("id") or ""))):
                    ex["cover"] = p["images"][0]
                    dirty = True
                    log.append({"step": f"{label} 补封面", "ok": True,
                                "out": f"封面 ← 外链（{p['images'][0].rsplit('/', 1)[-1]}）"})
                log.append({"step": label, "ok": True, "out": f"DOI 已在文献库，跳过（{doi}）"})
                skipped += 1
                continue
            if idx:
                time.sleep(0.3)      # 礼貌限速，与手动抓取同口径
            try:
                record = fetch_paper(doi)
            except Exception as e:
                log.append({"step": label, "ok": False, "out": f"抓取失败（{doi}）：{e}"})
                failed += 1
                continue
            record["id"] = paper_id(doi)
            # 周报自带中文 → 直接预填（标题行中文段 / 摘要节 / 体裁注记）；
            # 个别空缺事后用「翻译缺中文的」批量回补
            record["title_zh"] = p["title_zh"]
            record["abstract_zh"] = p["abstract_zh"]
            record["article_type"] = p["genre"]
            record["note"] = ""
            record["tags"] = []
            record.setdefault("added", today())
            if p["images"]:
                # 封面走外链直显：不下载不落盘，前端 __imgFallback 多源降级兜底
                record["cover"] = p["images"][0]
                log.append({"step": label, "ok": True,
                            "out": f"封面 ← 外链（{p['images'][0].rsplit('/', 1)[-1]}）"})
            else:
                weekly_cover(record["id"], log, label)      # 图表为无 → logo 兜底
                cov = detect_cover(record["id"])
                if cov:
                    record["cover"] = cov
            by_doi[doi.lower()] = record
            added += 1
            log.append({"step": label, "ok": True,
                        "out": f"已入库：{str(record['title_zh'] or record['title'])[:48]}（{record['id']}）"})
        if added or dirty:
            papers["items"] = list(by_doi.values())
            save_papers(papers)
    summary = f"收录完成：新增 {added} 篇"
    if skipped:
        summary += f"，已入库跳过 {skipped} 篇"
    if failed:
        summary += f"，失败 {failed} 篇"
    log.append({"step": "收录汇总", "ok": True, "out": summary})
    return {"ok": True, "added": added, "skipped": skipped, "failed": failed, "title": title}


def do_weekly_and_finish(name: str, content: str, message: str, push: bool, sync: bool) -> dict:
    log: list[dict] = []
    res = do_weekly_import(name, content, log)
    if not res.get("ok"):
        return {"ok": False, "log": log, "hint": "周报处理失败（见日志），未构建站点"}
    # 全部重复也要构建：周报页本身是新增/更新的内容
    return pipeline_after_content(log, message or f"weekly: 收录《{res.get('title', name)}》", push, sync)


def do_weekly_delete(names) -> dict:
    """批量删除周报期次：names（列表；兼容单个文件名字符串）逐个删除落盘文件。
    只删周报页（content/weekly/*.md），已生成的文献卡片保留（在文献管理里单独删）。
    部分成功也算成功 —— 逐条记日志，返回 removed 供默认提交信息使用。"""
    want: list[str] = []
    for raw in (names if isinstance(names, list) else [names]):
        fname = weekly_safe_name(str(raw or ""))
        if fname and fname not in want:
            want.append(fname)
    if not want:
        return {"ok": False,
                "log": [{"step": "删除周报", "ok": False, "out": "没有指定要删除的期次"}]}

    log: list[dict] = []
    removed: list[str] = []
    for fname in want:
        fp = WEEKLY_SRC / fname
        if fp.exists():
            fp.unlink()
            removed.append(fname)
            log.append({"step": f"删除周报 {fname}", "ok": True,
                        "out": "周报页随构建消失；已生成的文献卡片保留，在文献管理里单独删除"})
        else:
            log.append({"step": f"删除周报 {fname}", "ok": False, "out": "文件不存在（可能已删除）"})
    if not removed:
        return {"ok": False, "log": log}
    return {"ok": True, "removed": removed, "log": log}


def do_weekly_delete_and_finish(names, message: str, push: bool, sync: bool) -> dict:
    res = do_weekly_delete(names)
    if not res.get("ok"):
        return res
    log = res.get("log", [])
    removed = res.get("removed") or []
    if not message.strip():
        if len(removed) == 1:
            message = f"weekly: 删除 {removed[0]}"           # 与既有单期删除的提交信息同格式
        else:
            preview = "、".join(removed[:3]) + (" 等" if len(removed) > 3 else "")
            message = f"weekly: 删除 {len(removed)} 期（{preview}）"
    return pipeline_after_content(log, message, push, sync)


# ────────────────────────── HTTP ──────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "MacroBiodivAdmin/1.0"

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stderr.write(f"  {self.command} {self.path}\n")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = (self.path or "/").split("?")[0]
        if path in ("/", "/index.html"):
            if UI_HTML.exists():
                self._send(200, UI_HTML.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(500, b"admin_ui.html not found", "text/plain; charset=utf-8")
        elif path == "/favicon.png":
            fp = ROOT / "assets_src" / "favicon.png"
            if fp.exists():
                self._send(200, fp.read_bytes(), "image/png")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        elif path == "/api/status":
            self._json(repo_state())
        elif path == "/api/items":
            data = load_papers()
            for it in data.get("items", []):
                # 封面是否为 logo 兜底：文献管理列表据此挂警示角标与补图指引
                it["coverLogo"] = _is_logo_cover(str(it.get("id") or ""))
            self._json(data)
        elif path == "/api/weekly":
            self._json(weekly_issues())
        elif path.startswith("/api/job/"):
            self._json(job_snapshot(path[len("/api/job/"):]))
        elif path.startswith("/api/cover/"):
            # 封面预览：管理界面不直接暴露 assets_src 目录，经此路由读文件
            fid = path[len("/api/cover/"):].split("?")[0]
            sent = False
            if re.fullmatch(r"[0-9a-f]{10}", fid):
                for e in COVER_EXTS:
                    fp = COVERS / f"{fid}.{e}"
                    if fp.exists():
                        self._send(200, fp.read_bytes(), COVER_MIME[e])
                        sent = True
                        break
            if not sent:
                # 外链封面本地无文件：302 到原始 URL，管理界面的缩略图/预览共用此路由
                target = ""
                if re.fullmatch(r"[0-9a-f]{10}", fid):
                    for it in load_papers().get("items", []):
                        if it.get("id") == fid:
                            cov = str(it.get("cover") or "")
                            if cov.startswith(("http://", "https://")):
                                target = cov
                            break
                if target:
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.end_headers()
                else:
                    self._send(404, b"no cover", "text/plain; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = (self.path or "").split("?")[0]
        body = self._read_json()
        if path == "/api/fetch":
            # 纯抓取（网络往返），同步返回 —— 前端逐条发送，保持逐条反馈
            self._json(do_fetch(body.get("dois") or []))
        elif path == "/api/weekly-parse":
            # 上传前预解析（本地纯解析，无副作用），同步返回
            self._json(weekly_preview(body.get("name") or "", body.get("content") or ""))
        elif path == "/api/weekly":
            # 长操作：落盘 + 逐篇抓取入库 + 封面 + 构建，后台任务执行
            self._json({"job": start_job("weekly", body)})
        elif path == "/api/weekly-delete":
            self._json({"job": start_job("weekly-delete", body)})
        elif path == "/api/cover":
            fid = (body.get("id") or "").strip()
            if body.get("remove"):
                remove_cover(fid)
                clear_cover_url(fid)     # 外链封面：字段一并清掉，否则保存后又回来了
                self._json({"ok": True, "cover": ""})
            elif str(body.get("url") or "").strip():
                ok, val = set_cover_url(fid, body.get("url") or "")
                self._json({"ok": ok, "cover": val if ok else "",
                            "error": "" if ok else val})
            else:
                ok, val = save_cover(fid, body.get("dataUrl") or "")
                self._json({"ok": ok, "cover": val if ok else "",
                            "error": "" if ok else val})
        elif path in ("/api/publish", "/api/update", "/api/refresh",
                      "/api/translate", "/api/delete", "/api/sync-server", "/api/init", "/api/push"):
            # 长操作：立即返回任务号，后台线程执行，前端轮询 /api/job/<id>
            self._json({"job": start_job(path[len("/api/"):], body)})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="MacroBiodiv 本地管理界面")
    ap.add_argument("--port", type=int, default=5201)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    META_DIR.mkdir(parents=True, exist_ok=True)

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print("MacroBiodiv 管理界面")
    print(f"  → {url}")
    print("  只监听 127.0.0.1：仅本机可访问，无需登录")
    print(f"  仓库：{ROOT}")
    print("  Ctrl+C 退出\n")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
