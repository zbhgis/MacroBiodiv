#!/usr/bin/env python3
"""本地大模型翻译 —— 文献标题 / 摘要的中文化（初译 + 审校，两重处理）。

配置来源（优先级从高到低）：
    1. 环境变量  LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME
    2. Windows 注册表 HKCU\\Environment（用 [Environment]::SetEnvironmentVariable(…, "User")
       设置过但当前进程启动得早、还没继承到新变量的场景）

接口走 OpenAI 兼容的 /chat/completions，无第三方依赖（urllib）。

限流与回退预案（针对 429 / 超时 / 服务不稳定）：
    · 请求节流：全局最小请求间隔，令牌桶类限流友好
    · 429 专属退避：指数等待 2/4/8/16s，且优先遵守 Retry-After 响应头
    · 其他可重试错误（超时 / 5xx / 网络抖动 / JSON 截断）：有限次重试
    · 断路器：120s 内连续 2 次「重试耗尽仍失败」→ 冷却 60s，期间直接拒绝而不打 API
      （批量场景不会对着挂掉的服务逐篇硬撞）
    · 降级：审校（第二重）失败或预算不足时，回退交付初译稿，并带原因说明
    · 调用方预算分级：抓取同步路径预算短（浏览器在等），批量任务预算长

用法（自测）：
    python scripts/llm.py "Atmospheric blocking slows ocean-driven melting"
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

TIMEOUT = 180          # 单次请求超时（推理模型长摘要可达数分钟）
RETRY_OTHER = 2        # 非 429 可重试错误的重试次数
RETRY_429 = 4          # 429 专属：2/4/8/16s 指数退避 + Retry-After
MIN_INTERVAL = 2.0     # 全局两次 LLM 请求的最小间隔
TEMPERATURE = 0.3      # 翻译要稳，不要发散
COOL_SECONDS = 60      # 断路器冷却时长
COOL_TRIGGERS = 2      # 冷却触发：120s 内连续 2 次重试耗尽（≈连续 2 篇完全失败）
MAX_TOKENS = 16384     # flash-lite 是推理模型：思考链计入输出配额，须放宽


# ────────────────────────── 配置 ──────────────────────────

def llm_config() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model)；未配置时前两项为空串。"""
    key = os.environ.get("LLM_API_KEY") or ""
    base = os.environ.get("LLM_BASE_URL") or ""
    model = os.environ.get("LLM_MODEL_NAME") or ""
    if (not key or not base or not model) and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                def rd(name: str) -> str:
                    try:
                        v, _ = winreg.QueryValueEx(k, name)
                        return v or ""
                    except OSError:
                        return ""
                key = key or rd("LLM_API_KEY")
                base = base or rd("LLM_BASE_URL")
                model = model or rd("LLM_MODEL_NAME")
        except OSError:
            pass
    return key.strip(), base.strip().rstrip("/"), model.strip()


def llm_configured() -> bool:
    key, base, model = llm_config()
    return bool(key and base and model)


class LLMError(Exception):
    """翻译失败（未配置 / 网络 / 限流耗尽 / 断路冷却 / 输出异常），message 面向管理界面展示。"""

    def __init__(self, message: str, status: int | None = None, retry_after: str | None = None):
        super().__init__(message)
        self.status = status              # HTTP 状态码；0=网络/响应层错误；None=非接口错误
        self.retry_after = retry_after    # 服务端 Retry-After 头（若有）


# ────────────────────────── 断路器与节流（多线程安全） ──────────────────────────

_BR_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0        # 上次真正发出请求的时刻
_FAIL_TIMES: list[float] = [] # 最近耗尽重试的失败时刻
_COOL_UNTIL = 0.0             # 断路冷却截止时刻


def _throttle() -> None:
    """全局最小请求间隔：无论哪个线程发请求，两次发送之间至少隔 MIN_INTERVAL。"""
    global _LAST_REQUEST_AT
    with _BR_LOCK:
        wait = _LAST_REQUEST_AT + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST_AT = time.time()


def _note_hard_fail() -> None:
    """一次「重试耗尽仍失败」：记录；120s 内累计 COOL_TRIGGERS 次则进入冷却。"""
    global _COOL_UNTIL
    now = time.time()
    with _BR_LOCK:
        _FAIL_TIMES.append(now)
        while _FAIL_TIMES and now - _FAIL_TIMES[0] > 120:
            _FAIL_TIMES.pop(0)
        if len(_FAIL_TIMES) >= COOL_TRIGGERS:
            _COOL_UNTIL = now + COOL_SECONDS
            _FAIL_TIMES.clear()


def _reset_fails() -> None:
    with _BR_LOCK:
        _FAIL_TIMES.clear()


def cooling_down() -> int:
    """断路冷却剩余秒数；0 表示可用。"""
    with _BR_LOCK:
        return max(0, int(_COOL_UNTIL - time.time()))


# ────────────────────────── 接口 ──────────────────────────

def _post(payload: dict) -> tuple[str, dict]:
    """发一次 /chat/completions。返回 (content, headers)。
    独立成函数便于测试时打桩。"""
    key, base, model = llm_config()
    if not (key and base and model):
        raise LLMError("未配置大模型：请设置环境变量 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise LLMError(f"HTTP {e.code} {detail}", status=e.code,
                       retry_after=e.headers.get("Retry-After") if e.headers else None) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"网络错误：{e}", status=0) from None
    except json.JSONDecodeError as e:
        raise LLMError(f"响应不是合法 JSON：{e}", status=0) from None
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise LLMError(f"接口返回空内容：{json.dumps(data, ensure_ascii=False)[:200]}", status=0)
    return content, dict(getattr(data, "headers", {}) or {})


def _chat(messages: list[dict], temperature: float = TEMPERATURE,
          deadline: float | None = None, retries_429: int = RETRY_429,
          notes: list[str] | None = None) -> str:
    """带完整重试策略的单轮对话。

    429：指数退避（2s 起，×2 封顶 30s），优先遵守 Retry-After，最多 retries_429 次；
    其他可重试错误（超时/5xx/网络）：RETRY_OTHER 次，线性退避；
    401/403：鉴权问题立即失败（重试无意义）；
    每次重试前检查 deadline，超预算立即放弃；
    全部重试耗尽 → 记入断路器，抛 LLMError（消息里带重试摘要）。
    """
    payload = {"model": "", "messages": messages, "temperature": temperature,
               "max_tokens": MAX_TOKENS}
    last_err: LLMError | None = None
    seen_429 = 0
    attempt = 0
    while True:
        if deadline and time.time() > deadline:
            _note_hard_fail()
            raise LLMError(f"耗时预算已用尽（{'；'.join(notes[-2:]) if notes else '无重试记录'}）", status=0)
        _throttle()
        attempt += 1
        try:
            key, base, model = llm_config()
            payload["model"] = model
            content, _hdrs = _post(payload)
            _reset_fails()
            return content
        except LLMError as e:
            last_err = e
            if e.status in (401, 403):
                raise LLMError(f"鉴权失败（检查 LLM_API_KEY）：{e}", status=e.status) from None
            retryable = e.status in (0, 429) or (e.status is not None and 500 <= e.status < 600)
            if not retryable:
                raise
            if e.status == 429:
                seen_429 += 1
                if seen_429 > retries_429:
                    _note_hard_fail()
                    raise LLMError(f"限流 429：已退避重试 {retries_429} 次仍被拒（{e}）"
                                   f"{'；'.join(notes[-2:]) if notes else ''}", status=429) from None
                # 指数退避，但优先遵守服务端 Retry-After
                wait = min(2 ** seen_429, 30)
                ra = e.retry_after
                if ra is not None:
                    try:
                        wait = max(wait, min(int(float(ra)), 90))
                    except ValueError:
                        pass
                if deadline and time.time() + wait > deadline:
                    _note_hard_fail()
                    raise LLMError(f"限流 429：等待 {wait}s 会超出耗时预算，放弃（{e}）") from None
                if notes is not None:
                    notes.append(f"429 限流，等 {wait}s 后第 {seen_429}/{retries_429} 次重试")
                time.sleep(wait)
            else:
                if attempt > RETRY_OTHER:
                    _note_hard_fail()
                    raise LLMError(f"{e}（已重试 {RETRY_OTHER} 次）"
                                   f"{'；'.join(notes[-2:]) if notes else ''}", status=e.status) from None
                wait = 1.5 * attempt
                if deadline and time.time() + wait > deadline:
                    raise
                if notes is not None:
                    notes.append(f"{str(e)[:40]}，等 {wait:.0f}s 后重试")
                time.sleep(wait)


def _extract_json(text: str) -> dict:
    """从回复里抠出 JSON（容忍 ```json 围栏 / 前后缀话）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"回复中没有 JSON：{text[:200]}")
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 解析失败：{e}\n{text[:200]}") from None


def _chat_json(messages: list[dict], deadline: float | None = None,
               notes: list[str] | None = None) -> dict:
    """对话 + 抠 JSON。只对解析层错误（截断/格式漂移，status=None）补一次重试；
    接口层错误（429/网络/鉴权/超预算，status 非 None）在 _chat 里已有完整重试，直接上抛。"""
    last: Exception | None = None
    for attempt in range(2):
        try:
            return _extract_json(_chat(messages, deadline=deadline, notes=notes))
        except LLMError as e:
            if e.status is not None:
                raise
            last = e
    raise last if last else LLMError("未知错误")


# ────────────────────────── 两重翻译：初译 → 审校 ──────────────────────────
# 固定 prompt 集中在此维护。要点：
#   · 源文用 <title>/<abstract> 标签包裹，边界清晰，不受内容里引号/括号干扰
#   · 输出约束写死「只输出一个 JSON 对象、以 } 完整结尾」，配合 _extract_json 的容错解析
#   · 术语规范以《全国科学技术名词审定委员会》为准（术语在线 termonline.cn），
#     高频术语给少量示例锚定风格，避免清单过长过度约束

# 领域高频术语锚点（MacroBiodiv 覆盖宏观生态/生物多样性 + 地学交叉；模型本身都会，
# 这里主要是统一风格、防止同一术语前后译法不一致）
_GLOSSARY = (
    "biodiversity=生物多样性；species richness=物种丰富度；macroecology=宏观生态学；"
    "ecosystem functioning=生态系统功能；sea ice=海冰；ice sheet=冰盖；glacier=冰川；"
    "sea-level rise=海平面上升；ocean circulation=海洋环流；carbon cycle=碳循环；"
    "paleoclimate=古气候；proxy=替代指标；foraminifera=有孔虫；monsoon=季风；"
    "permafrost=永久冻土；Anthropocene=人类世"
)

_TRANSLATOR_SYSTEM = f"""你是一名资深学术文献翻译专家，长期为期刊与科研机构翻译
地球科学、海洋学、气候学、生态学（宏观生物多样性）领域的论文标题与摘要。

【翻译原则】
1. 忠实完整：覆盖原文全部信息，不增译、不漏译、不主观发挥；限定语（时间范围、
   程度词、比较对象）必须保留。
2. 学术表达：规范的书面学术中文，符合中文行文习惯；原文长句可按语义拆分重组，
   但不得改变逻辑关系与事实。
3. 术语规范：优先采用全国科学技术名词审定委员会（术语在线）公布的规范译名，
   无规范名的用领域通行译法，同一术语全文译法一致。参考译名：{_GLOSSARY}。
4. 专有名词：人名不翻译，保留原文；地名用通行中文译名（如 Drake Passage=德雷克海峡、
   Fram Strait=弗拉姆海峡）；机构名、计划名保留通行译法。
5. 保留原样（不翻译）：拉丁学名（如 Pinus tabuliformis）、基因/蛋白符号、
   化学式与同位素记号（如 CO2、δ18O、14C）、数值与计量单位（如 1.5 °C、ppm、ka BP）、
   原文中的文献引用标记（如上标数字）。
6. 缩写：通行的领域缩写可保留（如 ACC、SLR、OA），首次出现写成「中文全称（缩写）」；
   原文已定义过的缩写直接沿用。
7. 标题译成完整名词性短语，不加句号；冒号/副标题结构保留（「主标题：副标题」）。

【体裁判断】在输出中增加 article_type 字段（英文体裁标签，遵循出版社惯例）：
- Nature 系（Nature 及其子刊、Nature Communications 等）：Article、Letter、Perspective、
  Comment、News & Views、Review、Analysis、Matter、Tool、Brief Communication；
- Science 系：Research Article、Report、Perspective、Policy Forum、Review、Editorial、Letter；
- Cell / Trends 系：Article、Resource、Preview、Commentary、Spotlight、Opinion；
- 其他期刊或拿不准时，用通用体裁：Research Article（有数据与结论的研究）、Review（综述）、
  Perspective（观点）、Comment（评论）、Editorial（社论）、Erratum（勘误）；
- 用户消息会给出元数据提示（如 OpenAlex 体裁 review/editorial/letter），以其为基础
  结合内容判断；综述类几乎必有系统性结构（methodology/summarize existing studies），
  不要把带新数据的研究误判为 Review；不确定时选保守的通用体裁，不要编造出版社专属标签。

【输出格式】
只输出一个 JSON 对象，字段为 title_zh、abstract_zh、article_type：
- 不要 markdown 代码块，不要任何解释、注释或前后缀文字；
- 内容再长也必须输出完整 JSON，以 }} 结尾；
- 无摘要时 abstract_zh 为空字符串 ""。"""

_REVIEWER_SYSTEM = f"""你是一名严格的学术翻译审校，对文献标题/摘要的中译文做终审。
领域与术语规范同上（参考译名：{_GLOSSARY}）。

【审校清单】逐项对照英文原文核查：
1. 漏译/增译：原文每个事实、每个限定语都必须在译文中；不得出现原文没有的内容；
2. 错译：时态、否定、比较级、因果关系、量级与数字是否与原文一致；
3. 术语：是否符合规范译名且全文一致；缩写首次出现是否交代；
4. 表达：是否通顺、符合中文学术文风，消除生硬的翻译腔长句；
5. 保留项：拉丁学名、单位、同位素记号、引用标记等是否被误译或丢失；
6. 体裁 article_type：是否与内容相称（研究/综述/观点/评论…），遵循出版社惯例。

【裁定与输出】
- 有问题就修正；初译已达标时保持原样，不必为改而改；
- 无论是否修改，都输出完整 JSON（title_zh、abstract_zh、article_type 三个字段都要有，
  空摘要的 abstract_zh 为 ""）；
- 只输出一个 JSON 对象，不要 markdown 代码块与任何解释；内容再长也必须以 }} 完整结尾。"""


def _source_block(title: str, abstract: str, has_abs: bool) -> str:
    ab = abstract if has_abs else "（无摘要，只译标题）"
    return f"<title>{title}</title>\n<abstract>{ab}</abstract>"


def translate_paper(title: str, abstract: str = "", budget: int = 420,
                    type_hint: str = "") -> dict:
    """两重处理：① 初译 ② 审校。返回 {title_zh, abstract_zh, article_type, passes, note}。

    budget：总耗时预算（秒）。抓取同步路径传小值（浏览器在等），
    批量任务传大值。审校阶段若预算不足或失败，回退交付初译稿（passes=1 + note）；
    初译本身失败才抛 LLMError（此时没有任何可用中文）。
    type_hint：元数据体裁提示（OpenAlex type / Crossref type），随消息传给模型参考。
    """
    title = (title or "").strip()
    abstract = (abstract or "").strip()
    if not title:
        raise LLMError("没有可翻译的标题")
    cool = cooling_down()
    if cool:
        raise LLMError(f"LLM 断路冷却中（剩 {cool}s）——服务刚连续失败过，稍后用「重新翻译/翻译缺中文的」重试")

    deadline = time.time() + max(budget, 60)
    notes: list[str] = []
    has_abs = bool(abstract)

    # ── 第一重：初译 ──
    user1 = (
        "请把下面这篇文献的标题和摘要翻译成中文，并按系统提示判断文章体裁，"
        "遵循系统提示中的翻译原则与输出格式。\n"
        + f"<metadata_hint>{type_hint or '（无）'}</metadata_hint>\n"
        + _source_block(title, abstract, has_abs)
    )
    draft = _chat_json([
        {"role": "system", "content": _TRANSLATOR_SYSTEM},
        {"role": "user", "content": user1},
    ], deadline=deadline, notes=notes)
    title_zh = str(draft.get("title_zh") or "").strip()
    abstract_zh = str(draft.get("abstract_zh") or "").strip()
    article_type = str(draft.get("article_type") or "").strip()
    if not title_zh:
        raise LLMError("初译没有返回标题译文")

    # ── 第二重：审校（预算不足或失败 → 回退初译稿，不整体失败） ──
    if deadline - time.time() < 25:
        return {"title_zh": title_zh, "abstract_zh": abstract_zh if has_abs else "",
                "article_type": article_type,
                "passes": 1, "note": "审校因耗时预算跳过，建议稍后「重新翻译」补审校"}
    draft_json = json.dumps({"title_zh": title_zh, "abstract_zh": abstract_zh,
                             "article_type": article_type}, ensure_ascii=False)
    user2 = (
        "请按系统提示的审校清单逐项核查下面的译文，输出终稿 JSON。\n"
        f"<metadata_hint>{type_hint or '（无）'}</metadata_hint>\n"
        "<english>\n" + _source_block(title, abstract, has_abs) + "\n</english>\n"
        f"<初译>\n{draft_json}\n</初译>"
    )
    try:
        final = _chat_json([
            {"role": "system", "content": _REVIEWER_SYSTEM},
            {"role": "user", "content": user2},
        ], deadline=deadline, notes=notes)
    except LLMError as e:
        return {"title_zh": title_zh, "abstract_zh": abstract_zh if has_abs else "",
                "article_type": article_type,
                "passes": 1, "note": f"审校失败已回退初译：{str(e)[:80]}"}
    title_zh2 = str(final.get("title_zh") or "").strip() or title_zh
    abstract_zh2 = str(final.get("abstract_zh") or "").strip()
    if has_abs and not abstract_zh2:
        abstract_zh2 = abstract_zh      # 审校弄丢了摘要就回退初译稿
    article_type2 = str(final.get("article_type") or "").strip() or article_type

    return {"title_zh": title_zh2, "abstract_zh": abstract_zh2 if has_abs else "",
            "article_type": article_type2,
            "passes": 2, "note": ""}


def main() -> int:
    title = " ".join(sys.argv[1:]).strip()
    if not title:
        print('用法：python scripts/llm.py "英文标题"')
        return 1
    if not llm_configured():
        print("! 未配置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME")
        return 1
    key, base, model = llm_config()
    print(f"端点 {base}  模型 {model}")
    res = translate_paper(title)
    tag = "初译+审校" if res["passes"] >= 2 else f"仅初译（{res['note']}）"
    print(f"完成（{tag}）：")
    print("  title_zh   :", res["title_zh"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
