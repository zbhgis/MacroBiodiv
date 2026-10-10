# MacroBiodiv · 维护约定（对本项目工作的所有会话生效）

## 文档同步（最重要）

设计文档在 **`DESIGN.md`**。凡发生以下改动，必须同步更新 `DESIGN.md` 对应小节，
并检查 `usage.md`（维护者手册）与 `README.md`（用户向简介）的功能/使用描述是否需要跟改：

- 视觉样式（CSS token、组件、布局、断点）
- 页面结构与交互（首页 / 详情页 / 搜索页 / 管理后台 admin_ui.html）
- 数据模型（`meta/papers.json` 的字段增删改、来源与覆盖规则）
- 抓取管线（fetch_doi.py 的来源、时间口径、规范化规则）
- LLM 行为（llm.py 的 prompt、两重翻译流程、限流预案、字段产出）

未同步更新文档视为任务未完成。

## 设计基线

- 样式/交互移植自 GeoSciPlot（github.com/zbhgis/GeoSciPlot）：token、右侧悬浮队列、
  分页条、搜索页版式为共享语言，修改前先与原站比对，保持同源
- 固定 prompt 集中在 `scripts/llm.py`（`_TRANSLATOR_SYSTEM` / `_REVIEWER_SYSTEM`），
  改 prompt 必须用真实 DOI 实测一遍再提交

## 工程约定

- 零第三方依赖：只用 Python 标准库；新增能力优先标准库方案
- `site/`、`site_trash/` 为构建产物，不入库；临时测试脚本（`_*.py`）用完即删
- 改 CSS/JS 后必须重跑 `python scripts/build_site.py`（资源带 `?v=` 时间戳防缓存）
- 端口：管理后台 5201（5200 被 MultiColor 占用），本地预览 7332
- LLM 网络调用必须走 `llm.py` 的重试/断路/预算框架，不在别处裸调接口
