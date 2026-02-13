# AIBase to Feishu

每天抓取 [AIBase 新闻页](https://news.aibase.com/zh/news) 最新内容，自动推送到飞书群自定义机器人。

## 文件说明

- `push_news.py`: 抓取 + 去重 + 推送逻辑
- `state.json`: 已推送新闻 ID 去重状态（会被 GitHub Actions 自动更新）
- `.github/workflows/push.yml`: 定时任务（默认每小时第 7 分钟）
- `requirements.txt`: Python 依赖

## 一次性配置

1. 在飞书群添加「自定义机器人」，拿到 Webhook。
2. 在 GitHub 仓库设置 Secret:
   - 名称: `FEISHU_WEBHOOK`
   - 值: 你的飞书 Webhook
3. 推送本仓库代码到 GitHub。
4. 在 Actions 页面手动执行一次 `AIBase to Feishu` 验证。

## 定时说明

- 工作流当前配置: `7 * * * *`（UTC，每小时触发一次）
- 按北京时间（UTC+8）理解，就是每小时第 7 分钟跑一次。
- 这个自动触发来自 `.github/workflows/push.yml` 里的 `on.schedule`，不需要你手动点。
- `workflow_dispatch` 只是保留给你“手动测试”用的入口。
- 定时任务会跑在仓库默认分支（通常是 `main`）上的 workflow 文件。

## 可选环境变量

- `TOP_N`（默认 `5`）: 每次最多推送条数
- `LIST_URL`（默认 `https://news.aibase.com/zh/news`）: 抓取地址
- `STATE_FILE`（默认 `state.json`）: 状态文件路径
- `MAX_SEEN_IDS`（默认 `5000`）: 去重集合最大保留数量
- `FEISHU_MSG_STYLE`（默认 `post`）: `post` 为富文本样式，`text` 为纯文本样式
- `TITLE_MAX_LEN`（默认 `46`）: 标题最大长度（超长自动截断）
