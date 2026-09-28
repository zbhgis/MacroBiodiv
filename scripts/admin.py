#!/usr/bin/env python3
"""MacroBiodiv 本地管理界面 —— 只有本机能访问（服务只绑定 127.0.0.1）。

用法：
    python scripts/admin.py                 # 自动打开 http://127.0.0.1:5201
    python scripts/admin.py --port 5201     # 换端口
    python scripts/admin.py --no-open       # 不自动打开浏览器

功能（流程照搬 GeoSciPlot：上传内容 → 填手动字段 → 点发布）：
    1. 粘贴 DOI（单条 / 多行批量 / 含 DOI 的任意文本）→ 按 DOI 自动抓取
       基本信息（Crossref + OpenAlex，见 fetch_doi.py）
    2. 逐条补 标签 / 中文标题 / 备注
    3. 文献管理：编辑手动字段、重新抓取、刷新被引、删除
    4. 点「发布」→ 自动执行：写 meta/papers.json → build_site.py
       → git add / commit / push →（可选）同步到服务器

为什么不需要登录：服务只监听 127.0.0.1，物理上只有本机能连；推送用你本机已配置的
git 凭据（SSH key 或凭据管理器），**不需要在服务器上存任何 token**。

数据源说明：GeoSciPlot 的手动字段在 titles.csv 里留底，本站手动字段
（tags / title_zh / note）直接存 meta/papers.json —— 条目少、结构稳定，
单一数据源更不容易出不同步。
"""
from __future__ import annotations

import argparse
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
from fetch_doi import fetch_paper, fetch_openalex, normalize_doi, paper_id  # noqa: E402
import llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
META_DIR = ROOT / "meta"
PAPERS_JSON = META_DIR / "papers.json"
SITE = ROOT / "site"                     # build_site.py 的产物目录（「同步服务器」用）
UI_HTML = Path(__file__).resolve().parent / "admin_ui.html"

PYTHON = sys.executable
DEFAULT_REMOTE = "https://github.com/zbhgis/MacroBiodiv.git"
MAX_BODY = 20 * 1024 * 1024
# 手动字段：自动抓取不会覆盖；abstract_zh 由大模型初填，之后视同手动字段（可在界面修改，
# 重新抓取 / 刷新被引都会保留，只有显式「重新翻译」才重写）
MANUAL_TEXT = ("title_zh", "abstract_zh", "article_type", "note")
# keywords 手动维护（按文章原文填写；API 层拿不到作者关键词，不从正文推断）
MANUAL_LIST = ("tags", "keywords")


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
    }


# ────────────────────────── 后台任务系统 ──────────────────────────
# 发布/删除/刷新/推送都是「生成站点 + git push + scp 服务器」或网络批量操作，
# 同步执行会让界面停滞。POST 立即返回任务号，后台线程跑流水线，
# 前端轮询 /api/job/<id> 看实时进度。

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
# papers.json 的读写锁：发布/更新/刷新/补译/删除都是「读→改→写回」，
# 并发执行（如刷新被引未完又点发布）会互相覆盖丢数据，统一在此串行化
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
            elif kind == "refresh-cited":
                res = do_refresh_cited_and_finish(body.get("message") or "",
                                                  bool(body.get("push", True)), bool(body.get("sync", True)))
            elif kind == "translate":
                res = do_translate_and_finish(body.get("ids") or [], bool(body.get("only_missing")),
                                              body.get("message") or "",
                                              bool(body.get("push", True)), bool(body.get("sync", True)))
            elif kind == "delete":
                res = do_delete_and_finish((body.get("id") or "").strip(), body.get("message") or "",
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
                keep = {k: it[k] for k in ("id", "added", "title_zh", "abstract_zh", "article_type", "note", "tags", "keywords") if k in it}
                record.update(keep)
                record["id"] = it.get("id")     # id 由入库时的 DOI 算出，保持不变
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


def do_refresh_cited() -> dict:
    """批量刷新全部文献的被引数（只查 OpenAlex，单字段轻量）。"""
    log = [{"step": "刷新被引（OpenAlex）", "out": "", "ok": None}]
    n, err = 0, 0
    with DATA_LOCK:
        papers = load_papers()
        for idx, it in enumerate(papers["items"]):
            if idx:
                time.sleep(0.25)
            try:
                oa = fetch_openalex(str(it.get("doi") or ""))
                it["cited_by"] = oa.get("cited_by", it.get("cited_by", 0))
                n += 1
            except Exception:
                err += 1
        save_papers(papers)
    log[0]["ok"] = err == 0
    log[0]["out"] = f"更新 {n} 条" + (f"，失败 {err} 条" if err else "")
    return {"ok": err == 0, "log": log,
            "hint": "" if err == 0 else "部分条目刷新失败（网络波动），可重试"}


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


def do_delete(fid: str) -> dict:
    with DATA_LOCK:
        papers = load_papers()
        items = papers.get("items", [])
        it = next((x for x in items if x.get("id") == fid), None)
        if not it:
            return {"ok": False, "log": [{"step": "删除", "ok": False, "out": f"id 不存在：{fid}"}]}

        papers["items"] = [x for x in items if x.get("id") != fid]
        save_papers(papers)
    return {"ok": True, "item": it,
            "log": [{"step": f"删除 {fid}", "ok": True,
                     "out": f"已移除「{str(it.get('title') or '')[:50]}」（git 提交后详情页随构建自动消失）"}]}


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
    code, out = run(ssh_base + [f"mkdir -p {webroot} && find {webroot} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +"],
                    timeout=120)
    if code != 0:
        return {"ok": False, "log": [{"step": "同步服务器", "ok": False,
                                      "out": "SSH 连接失败（需先把本机公钥加入服务器 authorized_keys，"
                                             "命令见 README「服务器部署」一节）\n" + out}]}

    code, out = run(["scp", "-r", "-o", "BatchMode=yes", str(SITE) + "/.", f"{host}:{webroot}/"], timeout=600)
    if code != 0:
        return {"ok": False, "log": [{"step": "同步服务器", "ok": False, "out": "scp 失败\n" + out}]}
    return {"ok": True, "log": [{"step": "同步服务器", "ok": True, "out": f"site/ → {host}:{webroot}"}]}


def pipeline_after_content(log: list[dict], message: str, push: bool, sync: bool) -> dict:
    """内容变更后的公共收尾：生成站点 → git 提交推送 →（可选）同步服务器。
    所有步骤用 step() 执行，前端能看到每一步的实时状态。"""
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
        log.append({"step": "同步服务器（ssh 清理 + scp 上传）", "out": "", "ok": None})
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


def do_refresh_cited_and_finish(message: str, push: bool, sync: bool) -> dict:
    res = do_refresh_cited()
    log = res.get("log", [])
    out = pipeline_after_content(log, message or "update: 刷新被引数", push, sync)
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


def do_delete_and_finish(fid: str, message: str, push: bool, sync: bool) -> dict:
    res = do_delete(fid)
    if not res.get("ok"):
        return res
    log = res.get("log", [])
    return pipeline_after_content(log, message or f"delete: 文献 {fid}", push, sync)


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
            self._json(load_papers())
        elif path.startswith("/api/job/"):
            self._json(job_snapshot(path[len("/api/job/"):]))
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = (self.path or "").split("?")[0]
        body = self._read_json()
        if path == "/api/fetch":
            # 纯抓取（网络往返），同步返回 —— 前端逐条发送，保持逐条反馈
            self._json(do_fetch(body.get("dois") or []))
        elif path in ("/api/publish", "/api/update", "/api/refresh", "/api/refresh-cited",
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
