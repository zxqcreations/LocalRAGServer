# 解析质量抽检报告（真实数据）

生成时间：2026-08-14 18:01 · 抽样：每类 5 篇

| 文件 | 类型 | 大小MB | 耗时s | 页数 | 字符 | 文本层 | chunks | MinerU s | MinerU MB | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|
| el%3A19970234.pdf | pdf | 0.1 | 0.12 | 2 | 9637 | 有 | 0 | - | - |  |
| prj-8-2-127.pdf | pdf | 1.2 | 0.12 | 8 | 39614 | 有 | 0 | - | - |  |
| ol-48-2-391.pdf | pdf | 2.8 | 0.36 | 4 | 23214 | 有 | 0 | - | - |  |
| 2511.22820v1.pdf | pdf | 5.5 | 0.25 | 13 | 40369 | 有 | 0 | - | - |  |
| Rare-Earth-Doped Fiber Lasers and Amplifiers (Mi | pdf | 533.6 | 20.02 | 765 | 2349445 | 有 | 0 | - | - |  |
| Rare Earth Elements (Basudeb Basu, Bubun Banerje | epub | 6.0 | 1.06 | 506 | 650347 | 有 | 0 | - | - |  |
| Diode lasers and photonic integrated circuits (  | epub | 30.7 | 3.00 | 805 | 1349664 | 有 | 0 | - | - |  |
| audit-round-04-depth-balance.md | md | 0.0 | 0.04 | 0 | 1168 | - | 2 | - | - |  |
| audit-round-01-comprehensive.md | md | 0.0 | 0.02 | 0 | 3464 | - | 7 | - | - |  |
| COMSOL_GUI_构建指南.md | md | 0.0 | 0.02 | 0 | 7899 | - | 15 | - | - |  |
| consolidated-reference-list.md | md | 0.0 | 0.03 | 0 | 27944 | - | 53 | - | - |  |
| 00-MASTER-RESEARCH-OVERVIEW.md | md | 0.4 | 0.08 | 0 | 236008 | - | 471 | - | - |  |

## MinerU 深度解析（补充结论）

- **小文献（2 篇，1-3MB）**：解析成功，质量优异——标题/作者/LaTeX 公式/图表引用/DOI 完整保留
- **大教材（534MB PDF，765 页）**：单机 CPU 解析 >1.5h 仍未完成 → **结论：大文档深度解析必须分批/并行 worker（架构 §8.4 判断实锤）**；策略：按页区间切分任务或限制单文档页数走 pymupdf 快速通道
- 全样本有文本层（无扫描件）→ 本数据集暂不需要 OCR；PaddleOCR 保持 Phase 1 可选路径
