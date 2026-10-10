<p align="center">
  <img src="assets_src/logo.png" alt="MacroBiodiv" width="160">
</p>

<h1 align="center">MacroBiodiv</h1>

<p align="center">宏观生物多样性文献库 —— Nature / Science / Cell 系列大尺度研究，每周精选。</p>

<p align="center"><a href="https://macrobiodiv.zbhgis.com">macrobiodiv.zbhgis.com</a> · <a href="usage.md">维护文档</a> · <a href="DESIGN.md">设计文档</a> · <a href="https://github.com/zbhgis/GeoSciPlot">GeoSciPlot</a>（同作者姐妹项目）</p>

## 这是什么

一个可检索、可筛选、可订阅的宏观生态与生物多样性文献库：

- **文献卡片瀑布流**：封面顶图 + 文章类型 + 期刊徽章 + 标题（中文优先），4/3/2 列响应式
- **全站搜索与三维筛选**：标题 / 作者 / DOI / 摘要全文检索，标签 / 期刊 / 年份多选筛选，
  筛选与排序可经 URL 分享
- **每周速递**：每周一批精选文献的深度速递（按月归档、可翻页），全文可搜
- **详情页**：中文摘要主读 + 英文原文收合、GB/T 7714 引用条与 BibTeX 一键复制、
  图片灯箱、文章目录、上一篇 / 下一篇
- **全站统计**：收录总览、热读文献榜、发表动态、期刊与关键词分布
- 明暗双主题、字号调节、Open Graph 分享卡片、Atom 订阅

文献元数据来自 [Crossref](https://www.crossref.org/) 与 [OpenAlex](https://openalex.org/)，
中文摘要与文章类型由大模型辅助生成（初译 + 审校），部分摘要与标题人工校对。

## 本地预览

```bash
git clone https://github.com/zbhgis/MacroBiodiv.git
cd MacroBiodiv
python scripts/build_site.py            # 生成静态站（Python 3.9+，无第三方依赖）
cd site && python -m http.server 7332   # 打开 http://127.0.0.1:7332
```

## 维护

内容管理与发布走本地管理界面（粘贴 DOI / 上传周报 → 自动抓取与中文化 → 一键发布静态站），
发布流水线、踩坑清单、服务器部署与大模型配置见 **[usage.md](usage.md)**。

## 许可

代码 MIT；文献元数据来自 [Crossref](https://www.crossref.org/) 与 [OpenAlex](https://openalex.org/)，
版权归原出版方。站点架构与样式移植自 [GeoSciPlot](https://github.com/zbhgis/GeoSciPlot)（同作者姐妹项目），
DOI 抓取策略参考 [doi2md](https://github.com/zbhgis/doi2md)。
