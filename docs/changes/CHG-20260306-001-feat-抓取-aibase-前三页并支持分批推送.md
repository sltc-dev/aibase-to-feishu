---
id: 'CHG-20260306-001'
title: '抓取 AIBase 前三页并支持分批推送'
type: 'feat'
level: 'lite'
status: planned
review_required: false
created_at: '2026-03-06'
related_issue: 'N/A'
---

# 背景
当前脚本仅抓取新闻列表第一页，且默认每次仅发送最多 5 条。  
当站点在两个调度周期之间更新过快时，第一页之外（第二、第三页）的新内容会被遗漏。  
现有 `state.json` 去重逻辑可用，需要在扩大抓取范围后继续保留并生效。

# 目标
- 支持每次抓取新闻列表前 3 页（可配置），并统一参与“新内容”判断。
- 保留现有 `state.json` 去重机制，避免重复推送。
- 支持单次汇总发送或分批发送（可配置），避免消息过长且提升送达稳定性。

# 非目标
- 不改造飞书消息样式（`text/post`）本身。
- 不做历史全量回填（仅按配置页数抓取近期列表）。
- 不新增数据库或外部存储，继续使用 `state.json`。

# 流程 / 行为摘要
- 调度触发后，脚本按页调用 AIBase 新闻接口（默认 pageNo=1~3）并合并新闻条目。
- 合并结果按新闻 ID 倒序，基于 `state.json` 过滤已发送内容。
- 过滤后的新内容按 `TOP_N` 截断后发送：可一次性发送，或按配置分批发送。
- 发送成功后更新 `state.json`，写入本次成功发送的新闻 ID。

# 文件计划
| 状态 | 层级 | 文件 | 计划变更 |
| --- | --- | --- | --- |
| confirmed | core | `push_news.py` | 新增多页抓取、分页 URL 组装、分批发送逻辑；保持去重与状态写回逻辑兼容。 |
| confirmed | config | `.github/workflows/push.yml` | 调整运行时环境变量，默认启用“抓前三页 + 不限条数 + 分批发送”。 |
| confirmed | docs | `README.md` | 更新环境变量说明（如抓取页数、分批发送参数）与行为描述。 |
| suspected | test | `tests/test_push_news.py` | 若补充单元测试，则新增覆盖分页合并、去重筛选与批次拆分。 |

# 实现说明
- Approach: 将默认抓取源切换到 API（`FETCH_SOURCE=api`），按 `pageNo` 分页拉取并合并；新增 API 相关可配置项（`API_NEWS_URL`、`API_LANG_TYPE`、`API_T_VALUE`、`NEWS_URL_PREFIX`），并保留 HTML 模式兼容。`MAX_PAGES`（默认 `3`）和 `PUSH_BATCH_SIZE`（默认 `0`）继续生效。`TOP_N` 保持“本次最多推送条数”语义，并新增 `TOP_N=0` 表示不限制。
- Constraints: 兼容当前环境变量与 `state.json` 结构；默认行为应覆盖用户诉求（不额外配置即抓前 3 页）；失败时保持非 0 退出码语义不变。
- Reuse: 复用当前 `normalize_title`、链接提取规则、飞书发送函数与状态读写函数。

# 验证计划（固定三项）
| 项目 | 命令 | 预期结果 |
| --- | --- | --- |
| eslint | `echo "skip: python repo, no eslint config"` | skip（有明确原因） |
| ts/typecheck | `echo "skip: python repo, no ts config"` | skip（有明确原因） |
| unit test | `python3 -m unittest discover -s tests -q` | pass |

# 风险 / 回滚
- Risk: 接口响应结构若发生变更，可能导致解析失败或字段缺失；分批发送若未正确处理可能导致条目重复推送。
- Rollback: 回退本次提交；或通过 `FETCH_SOURCE=html` 临时退回 HTML 抓取模式。

# 未决问题 / 假设
- 假设接口 `https://mcpapi.aibase.cn/api/aiInfo/aiNews` 维持现有字段结构（`code/data.list/oid/title`）。
- 假设默认仍保留 `TOP_N=5`；如需“同批抓到尽量多”，可设置 `TOP_N=0`（不限制）或设为更大值。
- 假设分批策略默认关闭（`PUSH_BATCH_SIZE=0`），需要时由环境变量开启。

# 执行确认
- [x] 若 level=lite：用户已明确确认，可开始实现
- [ ] 若 level=risky：人工评审已通过
- [ ] 若 level=risky：用户已明确确认，可开始实现

# 实施结果
- 已完成：默认改为 API 多页抓取（默认 3 页），并新增可选分批推送能力；保留现有去重与状态写回机制。
- 已补充降级策略：当第 2/3 页抓取失败时，使用已成功抓取页面的结果继续执行，避免整次任务失败。

# 实际变更文件
| 文件 | 实际变更 |
| --- | --- |
| `push_news.py` | 新增 API 抓取与解析逻辑（`pageNo` 分页）、多源抓取模式（API/HTML）与批次发送逻辑。 |
| `.github/workflows/push.yml` | 设置 `FETCH_SOURCE=api`、`API_LANG_TYPE=zh_cn`、`TOP_N=0`、`MAX_PAGES=3`、`PUSH_BATCH_SIZE=10` 作为默认运行参数。 |
| `README.md` | 更新默认行为与新增环境变量说明（API 分页、多源抓取、分批发送）。 |
| `tests/test_push_news.py` | 新增核心逻辑单测（API payload 解析、批次拆分、后续分页失败降级）。 |
| `docs/changes/CHG-20260306-001-feat-抓取-aibase-前三页并支持分批推送.md` | 回填实施结果与验证信息。 |

# 偏离计划说明
- 补充了 `push_news.py` 的依赖缺失友好报错，以便在本地未安装依赖时仍可执行部分单元测试。
- 补充了“后续分页失败时降级为已抓取页面结果”的容错行为，降低分页 URL 不兼容导致的任务中断风险。
- 额外修改了 workflow 默认参数，使功能在不额外配置时即可生效。
- 根据线上实际行为，默认抓取方案从 HTML 分页切换为 API 分页，以保证第 2/3 页数据可用。

# 验证结果
- eslint: skip（python 项目，仓库未配置 eslint）
- ts/typecheck: skip（python 项目，仓库未配置 ts/typecheck）
- unit test: pass（`python3 -m unittest discover -s tests -q`，6 个测试通过）
