# 全球资讯定时推送 (News Digest Bot)

每日自动抓取 **全球军事 / AI / Cloudflare 教程** 三类资讯，翻译为中文，去重后推送到 Telegram 群。

通过 **GitHub Actions** 定时运行（无需自建服务器，关机照推）。

## 推送时间

北京时间 **08:00 / 12:00 / 20:00**（UTC `0 0` / `0 4` / `0 12`）。

## 资讯源（15 个，自动更新）

| 分类 | 来源 |
|------|------|
| 🛡️ 全球军事 | 美国防务新闻、战区 TWZ、防务博客、陆军技术（英文→翻译） |
| 🤖 AI 前沿 | The Verge、Ars、OpenAI、Import AI、MIT 科技评论、Wired、量子位、极客公园、InfoQ、IT之家 |
| ☁️ Cloudflare | 官方 Blog（每日仅保留最热 1 条） |

## 工作机制

1. 并发抓取 15 个 RSS 源
2. 标题 + 摘要经 Google 翻译转中文（带缓存，二次运行秒出）
3. 跨运行去重（`seen.json` 提交回仓库，避免重复推送）
4. 三类均衡截断，单条消息 ≤ 3900 字符（Telegram 限制）
5. 统计脚注：`📊 本期共 N 条（…）· 来源 X 个`
6. 推送到 Telegram 群

## 安全说明（公开仓库）

- Telegram **Bot Token 与 chat_id 仅存于 GitHub Secrets**，不进任何代码/配置文件
- 公开内容仅为：本说明、抓取代码、`feeds.json`、空 `bot_config.json`、`seen.json`（文章去重 GUID，无敏感信息）
- 任何人可 fork 代码，但运行需自备 Secrets，无法触碰你的 Bot

## 部署到自己的账号

1. Fork 或新建公开仓库，推送本目录
2. 仓库 `Settings → Secrets and variables → Actions → New repository secret` 添加：
   - `TG_TOKEN` = 你的 Bot Token（`数字:字母`，来自 @BotFather）
   - `TG_CHAT` = 目标群 chat_id（`-100xxxx`）
3. `Actions` 标签页启用 workflow，手动点一次 `Run workflow` 测试
4. 之后每天 3 个时段自动推送；也可在 Actions 页面手动触发

## 本地运行

```bash
pip install -r requirements.txt
export TG_TOKEN="你的token"
export TG_CHAT="你的群id"
python news_bot.py          # 正常运行
python news_bot.py --dry-run # 只打印不推送
python news_bot.py --bootstrap # 仅写入 seen 基线（冷启动用）
```

## 文件结构

- `news_bot.py` — 主程序（抓取/翻译/去重/排版/推送）
- `translate.py` — 翻译模块（Google gtx，带缓存）
- `feeds.json` — 源配置（改这里增删源）
- `bot_config.json` — 运行参数（token 留空，运行时读环境变量）
- `seen.json` — 去重状态（自动提交回仓库）
- `.github/workflows/news-digest.yml` — GitHub Actions 定时任务
