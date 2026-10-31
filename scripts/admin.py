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
       已收录期次可再编辑（mystation admin-server 同款）：正文浮层编辑器
       （frontmatter 分离保留 + .bak 备份 + mtime 冲突检测）/ 行内改元数据 /
       删除进本机回收站（content/.trash/weekly/）与恢复
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
import urllib.parse
import uuid
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_doi import fetch_paper, normalize_doi, paper_id  # noqa: E402
from render_md import parse_front_matter, parse_weekly_papers, CJK_RE  # noqa: E402
from fslock import exclusive as papers_file_lock, replace as papers_replace  # noqa: E402
import llm  # noqa: E402


def _has_cjk(s: str) -> bool:
    """译文质量廉价校验：中文译文里必须真有 CJK 字符（防模型英文回显）。"""
    return bool(CJK_RE.search(s or ""))

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
# prepare_thumbs 本地化产物（与 scripts/prepare_thumbs.py 的 FULL_DIR/THUMB_DIR 同址）：
# 按入库年份分目录 —— images/full/{年}/{id}.{ext}、images/thumb/{年}/{id}.webp
# （year = 条目 added 前 4 位；存量已于 2026-10 迁入 2025/，清理由 do_delete 的 rglob 跨年匹配）。
# 随 git 入库且经 jsDelivr 公网可达，删除文献时必须一并清掉
IMAGES_FULL = ROOT / "images" / "full"
IMAGES_THUMB = ROOT / "images" / "thumb"
IMAGES_DISP = ROOT / "images" / "disp"   # 详情页展示档（prepare_thumbs 产出，随 git 公网可达）
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
WEEKLY_TRASH = ROOT / "content" / ".trash" / "weekly"   # 本机回收站（gitignore，不进仓库）
WEEKLY_FILE_MAX = 2 * 1024 * 1024          # 单期读写上限（与 mystation admin-server 一致）
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
    """papers.json 是全站元数据的唯一真源：损坏时备份现场并抛错，绝不静默归零
    ——静默返回空库后，下一次任意写操作就把空库写回，文献连同中文译文/标签
    整批丢失。主站 loadConfig 的同款教训。"""
    if PAPERS_JSON.exists():
        try:
            data = json.loads(PAPERS_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
            raise ValueError("形状不符（缺 items 数组）")
        except (json.JSONDecodeError, ValueError) as e:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = PAPERS_JSON.parent / (PAPERS_JSON.name + f".corrupt-{stamp}")
            backup.write_bytes(PAPERS_JSON.read_bytes())
            raise RuntimeError(
                f"papers.json 损坏（{e}），已备份为 {backup.name}"
                "——修复或恢复该文件后重试，拒绝以空库覆盖"
            ) from e
    return {"generated": today(), "count": 0, "items": []}


def save_papers(papers: dict) -> None:
    papers["generated"] = today()
    papers["count"] = len(papers["items"])
    PAPERS_JSON.parent.mkdir(parents=True, exist_ok=True)
    # 原子写（tmp + replace）：写一半崩溃不留半截 JSON——半截 JSON 会触发上面的
    # 损坏防护抛错，管理台与发布全部停摆。
    # 文件锁：发布流水线以子进程跑 prepare_thumbs，两边对 papers.json 的
    # 读-改-写必须跨进程互斥（DATA_LOCK 只管本进程线程），否则慢的一方
    # 整快照回写会吞掉先落的变更。
    tmp = PAPERS_JSON.parent / (PAPERS_JSON.name + ".tmp")
    with papers_file_lock():
        tmp.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        papers_replace(tmp, PAPERS_JSON)


# ────────────────────────── 体裁归一 ──────────────────────────
# article_type 有三个来源（周报注记 / 大模型判定 / 手动填写），同一体裁常以大小写、
# 单复数、markdown 粗体等变体出现（实测：Comment 与 Comments、Perspective 与
# Perspectives、**Review**）——统计环形图与搜索按原串分组，变体会被拆成多块。
# 白名单归一：casefold 命中 → 规范名；未命中仅去粗体标记与首尾空白（新体裁不误伤）。
_AT_CANON = {
    "comment": "Comment", "comments": "Comment",
    "commentary": "Comment", "commentaries": "Comment",   # Commentary 属评论类，并入 Comment
    "perspective": "Perspective", "perspectives": "Perspective",
    "editorial": "Editorial", "editorials": "Editorial",
    "review": "Review", "reviews": "Review",
    "letter": "Letter", "letters": "Letter",
    "opinion": "Opinion", "opinions": "Opinion",
    "spotlight": "Spotlight", "spotlights": "Spotlight",
    "matters arising": "Matters Arising",
    "in depth": "In Depth",
    # 研究论文不特殊标注：与 Crossref journal-article 的回落名「Article」归并同一类
    "research article": "Article", "article": "Article", "articles": "Article",
    "policy article": "Policy Article",
    "expert voice": "Expert Voice",
}


def normalize_article_type(v) -> str:
    """体裁归一：去 markdown 粗体/下划线与首尾空白 → casefold 查白名单 → 规范名
    （未命中原样保留，规整内部连续空白）。空值返回空串。"""
    s = str(v or "").replace("*", "").replace("_", "").strip()
    if not s:
        return ""
    return _AT_CANON.get(" ".join(s.casefold().split()), s)


def apply_manual(item: dict, upd: dict) -> None:
    """把手动字段（tags / title_zh / note）合并进条目，自动字段不动。"""
    for k in MANUAL_TEXT:
        if k in upd:
            val = str(upd[k] or "").strip()
            item[k] = normalize_article_type(val) if k == "article_type" else val
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
            ok, err = unlink_retry(f)   # 杀软句柄抖动重试；失败记日志不留静默残影
            if not ok:
                print(f"! 封面文件删除失败 {f.name}: {err}")


def detect_cover(fid: str) -> str:
    """按 id 探测封面文件 → papers.json 的 cover 字段值；无则空串。"""
    if not re.fullmatch(r"[0-9a-f]{10}", fid or ""):
        return ""
    for e in COVER_EXTS:
        if (COVERS / f"{fid}.{e}").exists():
            return f"covers/{fid}.{e}"
    return ""


def prune_stale_thumb(item: dict) -> None:
    """cover 与 cover_thumb 同源校验。thumb 由 prepare_thumbs 按封面 fid 生成
    （仅外链封面 → images/thumb/{年}/{fid}.webp；本地封面无 thumb）。重新抓取/
    更新把 cover 换成本地文件后，遗留的旧外链 thumb 会让卡片仍显示旧图
    （card_html 缩略图优先于原图）——不同源即清除，卡片自动回退封面原图。"""
    cov = str(item.get("cover") or "")
    ctb = str(item.get("cover_thumb") or "")
    if not ctb:
        return
    stem = lambda u: re.sub(r"\.\w+$", "", u.rsplit("/", 1)[-1])
    if not cov.startswith(("http://", "https://")) or stem(cov) != stem(ctb):
        for k in ("cover_thumb", "cover_thumb_w", "cover_thumb_h",
                  "cover_disp", "cover_disp_w", "cover_disp_h"):
            item.pop(k, None)


def set_cover_url(fid: str, url: str) -> tuple[bool, str]:
    """外链封面：cover 字段直接存完整 URL（与周报管线「外链直显」同模式，不落盘）。
    本地旧封面文件一并清掉 —— 编辑保存以磁盘文件为准，留着文件外链永远不会生效。
    返回 (ok, cover路径或错误信息)。"""
    if not re.fullmatch(r"[0-9a-f]{10}", fid or ""):
        return False, "无效的文献 id"
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False, "外链必须以 http:// 或 https:// 开头"
    # cover 通道此前绕过 DATA_LOCK：与发布/更新并发时用旧快照覆盖保存，
    # 刚入库的条目整批丢失（lost update）。读-改-写必须持锁。
    with DATA_LOCK:
        papers = load_papers()
        hit = [it for it in papers.get("items", []) if it.get("id") == fid]
        if not hit:
            return False, "该文献尚未入库（新文献的外链会随条目在发布时写入）"
        remove_cover(fid)
        hit[0]["cover"] = u
        prune_stale_thumb(hit[0])
        save_papers(papers)
    return True, u


def clear_cover_url(fid: str) -> None:
    """外链封面的「移除」：磁盘上没有文件可删，把 papers.json 里的外链字段清掉。
    只动外链值；本地文件封面照旧走 remove_cover + 编辑保存时按磁盘探测。"""
    if not re.fullmatch(r"[0-9a-f]{10}", fid or ""):
        return
    with DATA_LOCK:
        papers = load_papers()
        hit = [it for it in papers.get("items", [])
               if it.get("id") == fid and str(it.get("cover") or "").startswith(("http://", "https://"))]
        if hit:
            for it in hit:
                it.pop("cover", None)
                prune_stale_thumb(it)   # cover 没了，thumb/disp 必成旧图残影
            save_papers(papers)


def repo_state() -> dict:
    papers = load_papers()
    items = papers.get("items", [])
    is_repo = (ROOT / ".git").exists()   # exists()：worktree 检出时 .git 是文件，is_dir 会误判
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
# 流水线互斥（GeoSciPlot 同款）：构建/提交/推送/删除这类「会动 site/、git、papers.json」
# 的任务同一时间只允许一个——否则两条流水线并发 build（同写 site/）、并发 git push
# （撞 index.lock）、并发 scp 换台（后完成者覆盖先完成者）。忙碌时秒败而不是排队
PIPELINE_KINDS = {"publish", "update", "refresh", "translate", "delete",
                  "weekly", "weekly-delete", "weekly-restore", "sync-server", "init", "push"}
PIPELINE_LOCK = threading.Lock()
# step() 镜像目标（线程局部）：worker 线程把共享日志挂上来，step 写入时同步一份，
# 前端轮询 job["log"] 才能在流水线运行期间看到实时进度
_JOB_TLS = threading.local()
# papers.json 的读写锁：发布/更新/刷新/补译/删除都是「读→改→写回」，
# 并发执行（如补译未完又点发布）会互相覆盖丢数据，统一在此串行化
DATA_LOCK = threading.Lock()


def step(log: list[dict], name: str, cmd: list[str] | None, timeout: int = 300) -> tuple[bool, str]:
    """带「运行中」标记的步骤：先写入 ok=None 条目（前端显示 …），命令结束后回填结果。
    写入的同时镜像进当前线程的共享任务日志（_JOB_TLS）：do_* 各自持有局部 log、
    收尾才整包赋给 job["log"]——不镜像的话，前端轮询在整个分钟级流水线期间
    拿到的恒为空列表，「实时进度」从未真正生效（GeoSciPlot 同款教训）。"""
    log.append({"step": name, "out": "", "ok": None})
    mirror = getattr(_JOB_TLS, "log", None)
    if mirror is not None and mirror is not log:
        mirror.append(log[-1])
    if cmd is None:
        return True, ""
    code, out = run(cmd, timeout)
    entry = log[-1]
    entry["ok"] = code == 0
    entry["out"] = out
    return entry["ok"], out


def start_job(kind: str, body: dict) -> str:
    """启动后台任务，立即返回任务号。流水线类任务互斥：忙时直接返回「秒败」任务。"""
    jid = uuid.uuid4().hex[:8]
    if kind in PIPELINE_KINDS and not PIPELINE_LOCK.acquire(blocking=False):
        with JOBS_LOCK:
            JOBS[jid] = {"kind": kind, "done": True, "ok": False,
                         "hint": "已有发布/收录/删除任务在运行——等下方日志显示结束后再操作",
                         "log": [{"step": "忙碌", "ok": False,
                                  "out": "同一时间只允许一条流水线"}]}
        return jid
    shared_log: list[dict] = []
    job = {"kind": kind, "log": shared_log, "done": False, "ok": None, "hint": ""}
    with JOBS_LOCK:
        JOBS[jid] = job
        done_ids = [k for k, v in JOBS.items() if v["done"]]
        for k in done_ids[:-20]:          # 只保留最近 20 个已完成的任务
            JOBS.pop(k, None)

    def worker():
        # step() 的镜像目标：共享日志随任务实时可见（见 step docstring）
        _JOB_TLS.log = shared_log
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
            elif kind == "weekly-restore":
                names = body.get("names")
                if not names and body.get("name"):
                    names = [body.get("name")]
                res = do_weekly_restore_and_finish(names or [], body.get("message") or "",
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
            # 追加进共享日志而不是整表替换：前面步骤的实时记录要保留
            shared_log.append({"step": "后台任务异常", "ok": False, "out": repr(e)})
            res = {"ok": False, "hint": "", "log": shared_log}
        finally:
            if kind in PIPELINE_KINDS:
                PIPELINE_LOCK.release()
        with JOBS_LOCK:
            job["log"] = res.get("log", job["log"])
            job["done"] = True
            job["ok"] = bool(res.get("ok"))
            job["hint"] = res.get("hint", "")

    try:
        threading.Thread(target=worker, daemon=True, name=f"job-{kind}-{jid}").start()
    except Exception as e:
        # 线程启动失败时 worker 不会运行、finally 里的锁释放不会发生——不补放的话
        # 所有流水线任务从此永远「忙碌」直到重启进程
        if kind in PIPELINE_KINDS:
            PIPELINE_LOCK.release()
        with JOBS_LOCK:
            job.update(done=True, ok=False, hint="后台线程启动失败，请重试",
                       log=[{"step": "启动后台任务", "ok": False, "out": repr(e)}])
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
                    # budget 是重试规划口径而非硬时限：已在飞的请求最坏可再跑
                    # TIMEOUT(180s)，弱网下总耗时可能超过浏览器 300s 超时——该条
                    # 预览丢失但无数据损失，重试即可。真硬限需可中断在飞请求。
                    # 覆写保护：模型偶尔丢字段，空结果不落盘，保留初始空值等待周报回填
                    if res.get("title_zh"):
                        record["title_zh"] = res["title_zh"]
                    if res.get("abstract_zh"):
                        record["abstract_zh"] = res["abstract_zh"]
                    if res.get("article_type"):
                        record["article_type"] = normalize_article_type(res["article_type"])
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
            # 封面：按 id 探测 assets_src/covers/ 里的实际文件（上传接口早已落盘）；
            # 与 do_update 同款双向逻辑 —— 客户端伪造的本地路径（磁盘无此文件）
            # 不入库，否则卡片/详情引用 404 图
            cov = detect_cover(rec["id"])
            if cov:
                rec["cover"] = cov
            elif not str(rec.get("cover") or "").startswith(("http://", "https://")):
                rec.pop("cover", None)
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
                prune_stale_thumb(it)
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
                keep = {k: it[k] for k in ("id", "added", "title_zh", "abstract_zh", "article_type", "note", "tags",
                                           "cover", "cover_thumb", "cover_thumb_w", "cover_thumb_h",
                                           "cover_disp", "cover_disp_w", "cover_disp_h") if k in it}
                if it.get("keywords"):
                    keep["keywords"] = it["keywords"]   # 手动填过才保留；空缺由重新抓取回填
                record.update(keep)
                record["id"] = it.get("id")     # id 由入库时的 DOI 算出，保持不变
                cov = detect_cover(record["id"])
                if cov:
                    record["cover"] = cov
                elif not str(record.get("cover") or "").startswith(("http://", "https://")):
                    record.pop("cover", None)   # 外链封面不落盘，本地探测不到也不清除
                prune_stale_thumb(record)
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
            # 只补空缺口径（先于调用记录缺什么）：标题已有中文（哪怕因摘要缺译被
            # 选中重译）也绝不动用户既有译文 —— docstring/DESIGN.md 四处承诺的实现
            fill_title = not str(it.get("title_zh") or "").strip()
            fill_abs = bool(str(it.get("abstract") or "").strip()) \
                and not str(it.get("abstract_zh") or "").strip()
            try:
                # budget=600：后台任务没有浏览器在等，预算放宽
                hint = str(it.get("oa_type") or it.get("type") or "")
                res = llm.translate_paper(title, str(it.get("abstract") or ""), budget=600,
                                          type_hint=hint)
                # 覆写保护 + 内容校验：空结果不落盘；译文须含 CJK（防英文回显）、
                # 摘要≠标题（防标题灌进摘要字段）；only_missing 下非缺失字段不动
                nt = str(res.get("title_zh") or "").strip()
                na = str(res.get("abstract_zh") or "").strip()
                rej: list[str] = []
                if nt and _has_cjk(nt) and (fill_title or not only_missing):
                    it["title_zh"] = nt
                elif not only_missing and nt and not _has_cjk(nt):
                    rej.append("标题非中文")     # 显式重写被守卫拒收，必须让用户知道
                if na and _has_cjk(na) and na != str(it.get("title_zh") or "").strip() \
                        and (fill_abs or not only_missing):
                    it["abstract_zh"] = na
                elif not only_missing and na and (
                        not _has_cjk(na) or na == str(it.get("title_zh") or "").strip()):
                    rej.append("摘要非中文或与标题同文")
                if res.get("article_type"):
                    it["article_type"] = normalize_article_type(res["article_type"])
                ok_n += 1
                tag = "初译+审校" if res.get("passes", 2) >= 2 else "仅初译"
                note = res.get("note") or ""
                rej_note = ("；译文校验未过已保留原值：" + "、".join(rej)) if rej else ""
                log.append({"step": f"翻译 {it.get('id')}", "ok": True,
                            "out": f"{it.get('title_zh', '')[:40]}（{tag}{'：' + note if note else ''}）{rej_note}"})
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


def unlink_retry(path: Path, tries: int = 5, delay: float = 0.3) -> tuple[bool, str]:
    """带短退避的删除（GeoSciPlot 同款移植）。Windows 上刚写入的文件常被杀软/
    索引服务短暂占用，立即 unlink 会偶发 PermissionError。"""
    for i in range(tries):
        try:
            path.unlink()
            return True, ""
        except FileNotFoundError:
            return True, ""                      # 已经没了 = 目的达成
        except OSError:
            if i < tries - 1:
                time.sleep(delay * (i + 1))
        except Exception as e:                   # 沙箱等非系统拦截：不重试
            return False, repr(e)
    return False, "文件仍被占用"


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
        fid = str(it.get("id") or "")
        remove_cover(fid)   # 封面文件随条目一起删（站点重建后不再引用）
        # prepare_thumbs 落在 images/{full,thumb,disp}/{年}/ 的原图/缩略/展示档一并清掉——
        # 它们随 git 入库且经 jsDelivr 公网可达，不删则已删文献的封面图仍可下载
        #（GSP 的 do_delete 有同款清理；disp 档此前单侧漂移漏了本仓）；rglob 跨年份目录。
        # unlink_retry：Windows 杀软句柄抖动下裸 unlink 会偶发失败且被吞（静默残留）
        if re.fullmatch(r"[0-9a-f]{10}", fid):
            stale_files = (list(IMAGES_FULL.rglob(fid + ".*")) + list(IMAGES_THUMB.rglob(fid + ".*"))
                           + list(IMAGES_DISP.rglob(fid + ".*")))
            for stale in stale_files:
                ok, err = unlink_retry(stale)
                if not ok:
                    log.append({"step": f"清理 {fid}", "ok": False, "out": f"{stale.name}：{err}"})
        log.append({"step": f"删除 {fid}", "ok": True,
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
    # 增量本地化封面（下载原图入本仓库 images/{full,thumb}/{入库年}/ → 压缩缩略图 →
    # cover/cover_thumb 改写为本仓库 jsDelivr 链接）。必须在 build 之前：新引用要进本次构建产物。
    # 失败不阻断发布 —— 缺缩略图的卡片自动回退原图外链，下次发布自动重试
    ok_t, out_t = step(log, "封面图片本地化（增量 → images/{full,thumb}/{入库年}/）",
                       [PYTHON, "scripts/prepare_thumbs.py", "--quiet"], timeout=580)
    if not ok_t:
        log.append({"step": "封面缩略图（未阻断发布）", "ok": False,
                    "out": "缺缩略图的卡片回退原图外链；下次发布会自动重试\n" + out_t[:300]})

    ok, out = step(log, "生成静态站 (build_site.py)", [PYTHON, "scripts/build_site.py"])
    if not ok:
        return {"ok": False, "log": log, "hint": "build_site.py 失败，未提交"}

    if not (ROOT / ".git").exists():   # exists()：worktree 兼容（口径同 repo_state）
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


def weekly_slug_of(text: str, stem: str) -> str:
    """从 md 原文推期号 slug（与 build_weekly 同口径）：正文首个一级标题优先
    —— build 只认 doc-1（第一个 H1）且剥粗体星号、非「文献N」才用作标题；
    frontmatter title 兜底，最后回退文件名（weekly_slug 对 stem 仍会搜「精选N」，
    与 build 的 `or md.stem` 一致）。H1 扫描限 front matter 之后的正文。"""
    _, body = parse_front_matter(text or "")
    m = re.search(r"(?m)^#(?!#)\s+(.+?)\s*$", body or "")
    if m:
        txt = re.sub(r"\*+", "", m.group(1)).strip()
        if txt and not re.match(r"^文献\d+", txt):
            return weekly_slug(txt, stem)
    meta, _ = parse_front_matter(text or "")
    title = str(meta.get("title") or "").strip() or stem
    return weekly_slug(title, stem)


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
                text = md.read_text(encoding="utf-8", errors="replace")
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
                "slug": weekly_slug_of(text, md.stem),
                "n": len(parsed),
                "nDoi": sum(1 for d in dois if d["doi"]),
                "inLib": sum(1 for d in dois if d["inLib"]),
                "nLogo": n_logo,
                "dois": dois,
            })
    issues.sort(key=lambda x: (x["date"], x["file"]), reverse=True)
    return {"issues": issues, "siteUrl": base, "trash": weekly_trash_list()}


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
    slug = weekly_slug_of(content, Path(fname).stem)
    if WEEKLY_SRC.is_dir():
        for md in WEEKLY_SRC.glob("*.md"):
            # NTFS 不区分大小写：仅大小写不同的重传实为同名覆盖更新，不作撞车
            if md.name.lower() == fname.lower():
                continue
            try:
                otext = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if weekly_slug_of(otext, md.stem) == slug:
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
    # 期号撞车防护：不同文件名解析出同一 slug（标题「精选N」重复，常见于同一期
    # 换名重传）会让构建期两篇文章页互相覆盖 —— build 侧有硬校验兜底，这里提前
    # 到落盘前拦截并给出明确指引。同名文件（fname 相同）= 覆盖更新，放行。
    new_slug = weekly_slug_of(content, Path(fname).stem)
    for other in WEEKLY_SRC.glob("*.md"):
        # NTFS 不区分大小写：仅大小写不同的「重传」实为同一文件，放行走覆盖更新
        if other.name.lower() == fname.lower():
            continue
        if weekly_slug_of(other.read_text(encoding="utf-8", errors="replace"),
                          other.stem) == new_slug:
            log.append({"step": "校验期号", "ok": False,
                        "out": f"与已收录的《{other.name}》同为期号 {new_slug}（标题「精选N」重复）。"
                               f"同一期更新请使用原文件名上传（覆盖式），或修正标题期号后重试；本次未落盘"})
            return {"ok": False}
    dest = WEEKLY_SRC / fname
    if dest.exists():
        # 同名覆盖更新前备份上一版：上一稿尚未随发布进 git 时这是唯一回退点
        # （口径同 weekly_file_save 的 _backup_md）
        dest.with_name(dest.name + ".bak").write_bytes(dest.read_bytes())
    dest.write_text(content, encoding="utf-8", newline="\n")
    log.append({"step": "落盘内容源", "ok": True,
                "out": f"content/weekly/{fname}（周报页随本次构建自动收录）"})

    added, skipped, failed = 0, 0, 0
    dirty = False
    with DATA_LOCK:
        papers = load_papers()
        by_doi = {str(i.get("doi") or "").lower(): i for i in papers["items"]}
        md_seen: set = set()
        for idx, p in enumerate(parsed):
            label = f"文献{p['no']}"
            doi = normalize_doi(p["doi_line"] or "")
            if not doi:
                log.append({"step": label, "ok": False, "out": "未解析到 DOI，跳过（不生成卡片）"})
                failed += 1
                continue
            if doi.lower() in md_seen:
                # 防歧义：同一期同 DOI 出现多条，九成是笔误（后一条抄成前一条的
                # DOI）。只按首条处理，重复条目整条跳过 —— 封面/中文回填/抓取
                # 一律不做，防止错条内容污染首条对应的库内条目
                log.append({"step": label, "ok": False,
                            "out": f"DOI 与本期前文重复（{doi}），疑似笔误，仅按首条处理，本条跳过"})
                skipped += 1
                continue
            md_seen.add(doi.lower())
            if doi.lower() in by_doi:
                # 已在库也要看一眼封面：上次导入图表为无会落 logo 兜底，这次重传
                # 同一期且解析到图表 → 封面升级为外链直显（日志标「补封面」）
                ex = by_doi[doi.lower()]
                cur = str(ex.get("cover") or "")
                if p["images"] and (not cur or _is_logo_cover(str(ex.get("id") or ""))):
                    ex["cover"] = p["images"][0]
                    prune_stale_thumb(ex)
                    dirty = True
                    log.append({"step": f"{label} 补封面", "ok": True,
                                "out": f"封面 ← 外链（{p['images'][0].rsplit('/', 1)[-1]}）"})
                # 已在库但中文空缺（可能此前经「添加文献」入库、LLM 未跑成）→
                # 用周报自带中文回填标题/摘要。幂等：只补空缺，绝不覆盖已有译文
                got = []
                if str(p.get("title_zh") or "").strip() and not str(ex.get("title_zh") or "").strip():
                    ex["title_zh"] = p["title_zh"]
                    got.append("标题")
                if str(p.get("abstract_zh") or "").strip() and not str(ex.get("abstract_zh") or "").strip():
                    ex["abstract_zh"] = p["abstract_zh"]
                    got.append("摘要")
                if got:
                    dirty = True
                    log.append({"step": f"{label} 补中文", "ok": True,
                                "out": "从周报回填：" + "、".join(got)})
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
            record["article_type"] = normalize_article_type(p["genre"])
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
    # 上传即全库对账：从所有周报 md 回填缺失中文（幂等只补空缺，不覆盖已有译文）。
    # 导入时 md 可能还是缺摘要节的早期版本，事后在本地完善即可——下次上传任意一期
    # 都会自动补齐，无需重传旧期或手动点「从周报补全中文」
    bf = do_weekly_backfill_chinese()
    log.extend(bf.get("log", []))
    # 全部重复也要构建：周报页本身是新增/更新的内容
    return pipeline_after_content(log, message or f"weekly: 收录《{res.get('title', name)}》", push, sync)


def do_weekly_backfill_chinese() -> dict:
    """从所有周报 md 补全库里缺失的中文（title_zh / abstract_zh）。
    幂等：只填空缺，绝不覆盖已有译文。返回回填成功的条目数及日志。"""
    log: list[dict] = []
    if not WEEKLY_SRC.is_dir():
        return {"ok": False, "log": [{"step": "扫描周报", "ok": False, "out": "content/weekly/ 目录不存在"}]}

    # 收集所有周报里的文献信息：{doi_lower: {title_zh, abstract_zh}}
    weekly_map: dict[str, dict] = {}
    for p in sorted(WEEKLY_SRC.glob("*.md")):
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            parsed = parse_weekly_papers(txt)
        except Exception as e:
            log.append({"step": f"解析 {p.name}", "ok": False, "out": str(e)[:120]})
            continue
        # 防歧义：同一期同 DOI 出现多条 → 疑似笔误（一条抄成另一条的 DOI），
        # 该 DOI 在本期整体弃用，防止错条的中文灌进首条对应的库内条目
        count: dict[str, int] = {}
        for it in parsed:
            d = normalize_doi(it["doi_line"] or "").lower()
            if d:
                count[d] = count.get(d, 0) + 1
        for it in parsed:
            doi = normalize_doi(it["doi_line"] or "").lower()
            if not doi or count.get(doi, 0) > 1:
                continue
            entry = weekly_map.setdefault(doi, {})
            if str(it.get("title_zh") or "").strip() and not entry.get("title_zh"):
                entry["title_zh"] = it["title_zh"]
            if str(it.get("abstract_zh") or "").strip() and not entry.get("abstract_zh"):
                entry["abstract_zh"] = it["abstract_zh"]

    if not weekly_map:
        return {"ok": True, "log": [{"step": "扫描周报", "ok": True, "out": "未解析到任何文献"}], "filled": 0}

    filled_n = 0
    with DATA_LOCK:
        papers = load_papers()
        by_id = {str(i.get("id") or ""): i for i in papers["items"]}
        for it in papers["items"]:
            doi = str(it.get("doi") or "").lower()
            we = weekly_map.get(doi)
            if not we:
                continue
            got = []
            if not str(it.get("title_zh") or "").strip() and we.get("title_zh"):
                it["title_zh"] = we["title_zh"]
                got.append("标题")
            if not str(it.get("abstract_zh") or "").strip() and we.get("abstract_zh"):
                it["abstract_zh"] = we["abstract_zh"]
                got.append("摘要")
            if got:
                filled_n += 1
                log.append({"step": f"补中文 {it.get('id')}", "ok": True,
                            "out": f"{doi[:36]} ← {'、'.join(got)}"})
        save_papers(papers)

    log.append({"step": "汇总", "ok": True, "out": f"共回填 {filled_n} 篇条目的中文"})
    return {"ok": True, "log": log, "filled": filled_n}


def do_weekly_delete(names) -> dict:
    """批量删除周报期次：names（列表；兼容单个文件名字符串）逐个移入本机回收站
    content/.trash/weekly/（mystation 同款软删除，git 不跟踪；可从「每周速递」页恢复）。
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
            try:
                WEEKLY_TRASH.mkdir(parents=True, exist_ok=True)
                target = WEEKLY_TRASH / fname
                if target.exists():
                    target = WEEKLY_TRASH / f"{Path(fname).stem}.deleted-{int(time.time())}.md"
                fp.rename(target)
                bak = fp.with_name(fp.name + ".bak")     # .bak 随正文一起进回收站
                if bak.exists():
                    bak.rename(WEEKLY_TRASH / bak.name)
            except OSError as e:
                log.append({"step": f"删除周报 {fname}", "ok": False, "out": f"移动失败：{e}"})
                continue
            removed.append(fname)
            log.append({"step": f"删除周报 {fname}", "ok": True,
                        "out": "md 移入 content/.trash/weekly/（可恢复）；周报页随构建消失，"
                               "已生成的文献卡片保留，在文献管理里单独删除"})
        else:
            log.append({"step": f"删除周报 {fname}", "ok": False, "out": "文件不存在（可能已删除）"})
    if not removed:
        return {"ok": False, "log": log}
    return {"ok": True, "removed": removed, "log": log}


# ── 周报期次文件编辑（mystation admin-server 同款）──────────────
# 列表页已有 /api/weekly（weekly_issues）；这里补单期正文/元数据的读取与写回。
# 与图库同节奏：编辑只写本地文件（毫秒级、不碰 git），线上更新随发布流水线生效。

FM_OPEN_RE = re.compile(r"\A[\s\uFEFF]*---[ \t]*\r?\n")


def _split_fm(text: str) -> tuple[str, str, str]:
    """拆 frontmatter → (fm 块含首尾 ---（无尾换行）, --- 行尾换行, 正文)。
    分隔换行必须与正文分开保真：存量周报 closing --- 后有一个空行，重写元数据
    按 `fm + sep + body` 拼回才能逐字节还原；无脑拼 `---\n` + 正文会把空行吃掉，
    平白多出一行 diff。无 frontmatter 时前两项为空串。"""
    m = FM_OPEN_RE.match(text)
    if not m:
        return "", "", text
    rest = text[m.end():]
    # 捕获 closing --- 前的换行并原样复用：CRLF 文件少一次无谓改写，且修复
    # after[1:] 对 CRLF 分隔换行只剥 1 字节的兄弟 bug（GeoSciPlot 同款修复）
    close = re.search(r"(\r?\n)---[ \t]*(?=\r?\n)", rest)    # closing --- 行（后面还有内容）
    if close is None:
        close = re.search(r"(\r?\n)---[ \t]*\Z", rest)        # 文件恰在 --- 结束
        if close is None:
            return "", "", text
        return "---\n" + rest[:close.start()] + close.group(1) + "---", "", ""
    after = rest[close.end():]
    nl = re.match(r"\r?\n", after)
    return ("---\n" + rest[:close.start()] + close.group(1) + "---",
            nl.group(0) if nl else "",
            after[len(nl.group(0)):] if nl else after)


def _parse_fm_fields(fm_block: str) -> dict:
    """frontmatter → {title, date, categoryTags: [...], subCategoryTags: [...]}。
    极简 YAML 子集（数组优先按 JSON 解析，退回逗号拆分），与 render_md 同口径。"""
    out: dict = {"title": "", "date": "", "categoryTags": [], "subCategoryTags": []}
    for line in fm_block.splitlines():
        if ":" not in line or line.strip().startswith("---"):
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key not in out:
            continue
        if isinstance(out[key], list):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    out[key] = [str(x).strip() for x in parsed if str(x).strip()]
                    continue
            except ValueError:
                pass
            out[key] = [x.strip().strip('"').strip("'")
                        for x in val.strip().strip("[]").split(",") if x.strip()]
        else:
            out[key] = val.strip().strip('"').strip("'")
    return out


def _build_fm(meta: dict) -> str:
    """元数据 → 规范 frontmatter 块（双引号标量 + JSON 数组，与存量周报同款）。"""
    def q(s: str) -> str:
        # fm 标量内不允许换行（会击穿 frontmatter 块结构）；引号降为单引号
        return '"' + str(s or "").replace('"', "'").replace("\r", " ").replace("\n", " ") + '"'
    lines = ["---",
             f"title: {q(meta.get('title', ''))}",
             f"date: {q(meta.get('date', ''))}"]
    for k in ("categoryTags", "subCategoryTags"):
        tags = meta.get(k) or []
        lines.append(f"{k}: [" + ", ".join(q(t) for t in tags) + "]")
    lines.append("---")
    return "\n".join(lines)          # 不带尾换行 —— 拼回时用 _split_fm 的 sep 保真


def _weekly_path(name: str) -> tuple[Path | None, str]:
    """清洗并解析周报文件路径；返回 (路径, 错误信息)。防目录穿越。"""
    fname = weekly_safe_name(name)
    if not fname:
        return None, "文件名不合法"
    p = WEEKLY_SRC / fname
    if not p.resolve().is_relative_to(WEEKLY_SRC.resolve()):
        return None, "非法路径"
    return p, ""


def _mtime_conflict(p: Path, expected) -> str:
    """乐观锁（mystation 同款）：expected 带了且与当前 mtime 对不上 → 提示冲突，
    拒绝落盘 —— 防两处编辑互相静默覆盖。"""
    if expected in (None, ""):
        return ""
    try:
        cur = round(p.stat().st_mtime * 1000)
    except OSError:
        return "文件已不存在（可能被删除或移入回收站）——请刷新列表"
    if abs(cur - float(expected)) > 1:
        return ("期次已在后台被更新（其他标签页或外部编辑器），当前编辑已过期——"
                "刷新列表取最新内容后重做，避免覆盖他人改动")
    return ""


def _backup_md(p: Path) -> None:
    """写回前备份上一版为同名 .bak（覆盖式单份）：手滑改坏时有一步回退。"""
    if p.exists():
        p.with_name(p.name + ".bak").write_bytes(p.read_bytes())


def weekly_file_read(name: str) -> dict:
    """单期正文读取：frontmatter 与正文分体返回（编辑器只编辑正文）。"""
    p, err = _weekly_path(name)
    if err:
        return {"ok": False, "error": err}
    if not p.is_file():
        return {"ok": False, "error": "文件不存在（可能已删除）"}
    try:
        st = p.stat()
        if st.st_size > WEEKLY_FILE_MAX:
            return {"ok": False, "error": "文件超过 2MB，不支持在线编辑"}
        raw = p.read_text(encoding="utf-8").replace("\uFEFF", "")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"读取失败：{e}"}
    fm, sep, body = _split_fm(raw)
    meta = _parse_fm_fields(fm)
    title = str(meta.get("title") or "").strip()
    if not title:
        h1 = re.search(r"(?m)^#(?!#)\s*(.+)$", body)
        title = (h1.group(1).strip() if h1 else "") or p.stem
    return {"ok": True, "file": p.name, "title": title, "meta": meta,
            "frontmatter": fm, "body": body,
            "sizeBytes": st.st_size, "mtimeMs": round(st.st_mtime * 1000)}


def weekly_file_save(name: str, body: str, expected_mtime) -> dict:
    """保存正文：只写正文，文件自己的 frontmatter 原样保留。"""
    p, err = _weekly_path(name)
    if err:
        return {"ok": False, "error": err}
    if not p.is_file():
        return {"ok": False, "error": "文件不存在（可能已删除）"}
    if conflict := _mtime_conflict(p, expected_mtime):
        return {"ok": False, "conflict": True, "error": conflict}
    if len(body or "") > WEEKLY_FILE_MAX:
        return {"ok": False, "error": "正文超过 2MB"}
    try:
        raw = p.read_text(encoding="utf-8").replace("\uFEFF", "")
        fm, sep, _ = _split_fm(raw)
        _backup_md(p)
        # 正文里的 BOM 一并剥掉（Windows 剪贴板常带，卡在行首会让 ^ 锚点失配）
        p.write_text(fm + sep + (body or "").replace("\uFEFF", ""),
                     encoding="utf-8", newline="\n")
    except OSError as e:
        return {"ok": False, "error": f"写入失败：{e}"}
    return {"ok": True, "mtimeMs": round(p.stat().st_mtime * 1000)}


def weekly_file_meta(name: str, meta: dict, expected_mtime) -> dict:
    """重写 frontmatter（title/date/两级标签），正文原样保留。

    标题里的「精选N」期号决定 slug（build_site 与 weekly_slug 同规则），
    改标题若动了期号，slug 与线上 URL 会随之变化 —— 由界面提示，不在这里拦。"""
    p, err = _weekly_path(name)
    if err:
        return {"ok": False, "error": err}
    if not p.is_file():
        return {"ok": False, "error": "文件不存在（可能已删除）"}
    if conflict := _mtime_conflict(p, expected_mtime):
        return {"ok": False, "conflict": True, "error": conflict}
    new_meta = {
        "title": str((meta or {}).get("title") or "").strip(),
        "date": str((meta or {}).get("date") or "").strip()[:10],
        "categoryTags": [str(t).strip() for t in ((meta or {}).get("categoryTags") or []) if str(t).strip()],
        "subCategoryTags": [str(t).strip() for t in ((meta or {}).get("subCategoryTags") or []) if str(t).strip()],
    }
    if not new_meta["title"]:
        return {"ok": False, "error": "标题不能为空"}
    if new_meta["date"] and not re.match(r"^\d{4}-\d{2}-\d{2}$", new_meta["date"]):
        return {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}
    try:
        raw = p.read_text(encoding="utf-8").replace("\uFEFF", "")
        _, sep, body = _split_fm(raw)
        _backup_md(p)
        p.write_text(_build_fm(new_meta) + sep + body, encoding="utf-8", newline="\n")
    except OSError as e:
        return {"ok": False, "error": f"写入失败：{e}"}
    return {"ok": True, "mtimeMs": round(p.stat().st_mtime * 1000)}


def weekly_trash_list() -> list[dict]:
    """content/.trash/weekly/ 下的已删期次。"""
    out: list[dict] = []
    if WEEKLY_TRASH.is_dir():
        for md in WEEKLY_TRASH.glob("*.md"):
            try:
                st = md.stat()
                text = md.read_text(encoding="utf-8").replace("\uFEFF", "")
            except (OSError, UnicodeDecodeError):
                continue
            fm, sep, body = _split_fm(text)
            meta = _parse_fm_fields(fm)
            title = str(meta.get("title") or "").strip()
            if not title:
                h1 = re.search(r"(?m)^#(?!#)\s*(.+)$", body)
                title = (h1.group(1).strip() if h1 else "") or md.stem
            out.append({"name": md.name, "title": title, "date": str(meta.get("date") or "")[:10],
                        "sizeBytes": st.st_size, "mtimeMs": round(st.st_mtime * 1000)})
    out.sort(key=lambda r: r["mtimeMs"], reverse=True)
    return out


def jobs_running() -> bool:
    """后台任务（发布 / 收录 / 删除 / 补译等）是否还有在跑的。
    周报文件编辑与流水线都会碰 content/weekly 与 papers.json，
    忙时直接拒绝而不是排队 —— 与 GeoSciPlot /api/save 的 PIPELINE_LOCK 同一防御。"""
    with JOBS_LOCK:
        return any(not j["done"] for j in JOBS.values())


def do_weekly_restore(names) -> dict:
    """从回收站恢复期次（逐个；改名冲突时用 .restored-<时间戳> 兜底）。
    只移文件，不构建 —— 由调用方接发布流水线让周报页重新上线。"""
    want: list[str] = []
    for raw in (names if isinstance(names, list) else [names]):
        fname = weekly_safe_name(str(raw or ""))
        if fname and fname not in want:
            want.append(fname)
    if not want:
        return {"ok": False,
                "log": [{"step": "恢复周报", "ok": False, "out": "没有指定要恢复的期次"}]}
    log: list[dict] = []
    restored: list[str] = []
    for fname in want:
        src = WEEKLY_TRASH / fname
        if not src.resolve().is_relative_to(WEEKLY_TRASH.resolve()) or not src.is_file():
            log.append({"step": f"恢复周报 {fname}", "ok": False, "out": "回收站中不存在"})
            continue
        dest = WEEKLY_SRC / fname
        if dest.exists():
            dest = WEEKLY_SRC / f"{Path(fname).stem}.restored-{int(time.time())}.md"
        try:
            WEEKLY_SRC.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
            bak = src.with_name(src.name + ".bak")   # .bak 跟着恢复，名字对齐恢复后的文件名
            if bak.exists():
                bak.rename(dest.with_name(dest.name + ".bak"))
        except OSError as e:
            log.append({"step": f"恢复周报 {fname}", "ok": False, "out": f"移动失败：{e}"})
            continue
        restored.append(dest.name)
        log.append({"step": f"恢复周报 {dest.name}", "ok": True,
                    "out": "md 已回到 content/weekly/，随本次构建重新上线"
                           + ("（原名被占，已改名）" if dest.name != fname else "")})
    if not restored:
        return {"ok": False, "log": log}
    return {"ok": True, "restored": restored, "log": log}


def do_weekly_restore_and_finish(names, message: str, push: bool, sync: bool) -> dict:
    res = do_weekly_restore(names)
    if not res.get("ok"):
        return res
    log = res.get("log", [])
    restored = res.get("restored") or []
    if not message.strip():
        preview = "、".join(restored[:3]) + (" 等" if len(restored) > 3 else "")
        message = f"weekly: 恢复 {len(restored)} 期（{preview}）"
    return pipeline_after_content(log, message, push, sync)


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

    def _trusted(self) -> bool:
        """本机信任校验（与主站 admin-server.ts 的 trusted() 同一防御）。

        rebinding：攻击者域名 A 记录指到 127.0.0.1 后浏览器视为同源，但 Host 头
        仍是攻击者域名 → Host 白名单直接挡死。
        CSRF：本服务的 POST 全部是简单请求，免预检即可跨站发出；跨站时浏览器必带
        Origin 头 → 非本机 Origin 一律拒绝；curl 等本机工具不带 Origin → 放行。
        没有这层，任意网页可打 /api/delete、/api/fetch（烧 LLM 预算）、甚至
        /api/init 用本机 SSH key 把仓库 push 到攻击者远端。"""
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}",
                        "127.0.0.1", "localhost"):
            return False
        if (self.command or "").upper() not in ("GET", "HEAD"):
            origin = (self.headers.get("Origin") or "").strip().lower()
            if origin and origin not in (f"http://127.0.0.1:{port}",
                                         f"http://localhost:{port}"):
                return False
        return True

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

    def _safe_500(self, e: BaseException) -> None:
        """任何未捕获异常都转成 500 + JSON，而不是让线程带着异常死掉、
        客户端只看到「连接被无声关闭」（GSP 同款，实测教训：load_papers 的
        损坏防护抛错、文件被杀软/沙箱占用等异常穿透时就是这种症状）。"""
        try:
            self._json({"ok": False, "error": "服务器内部错误：" + repr(e)}, 500)
        except Exception:
            pass

    def do_GET(self):
        try:
            if not self._trusted():
                self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return
            self._handle_get()
        except Exception as e:
            self._safe_500(e)

    def _handle_get(self):
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
        elif path == "/api/weekly/file":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self._json(weekly_file_read((q.get("name") or [""])[0]))
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
        try:
            if not self._trusted():
                self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return
            self._handle_post()
        except Exception as e:
            self._safe_500(e)

    def _handle_post(self):
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
        elif path in ("/api/weekly/save", "/api/weekly/meta"):
            # 期次文件编辑：本地纯文件写（毫秒级），同步返回。
            # 有后台任务在跑时拒绝 —— 构建 / 收录可能正读写 content/weekly 与 papers.json
            if jobs_running():
                self._json({"ok": False, "error": "已有发布/收录/删除任务在运行，等下方日志结束后再编辑"})
            elif path == "/api/weekly/save":
                self._json(weekly_file_save(body.get("name") or "", body.get("body") or "",
                                            body.get("expectedMtimeMs")))
            else:
                self._json(weekly_file_meta(body.get("name") or "", body.get("meta") or {},
                                            body.get("expectedMtimeMs")))
        elif path == "/api/weekly-restore":
            # 恢复 = 移回文件 + 重新构建上线，长操作走后台任务
            self._json({"job": start_job("weekly-restore", body)})
        elif path == "/api/weekly-backfill-zh":
            res = do_weekly_backfill_chinese()
            self._json(res)
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
