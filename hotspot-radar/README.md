# 热点雷达 · Web 版 (Hotspot Radar)

把桌面剪辑工具 **CreativeEnginePro** 里的「热点雷达」聚合功能做成的 **网页版**。
打开网页即看全球海外热点，点击卡片跳转原文，一键翻译，支持数据源配置与定时自动刷新。

> 项目地址：https://github.com/xiaohuan556/hotspot-radar

---

## ✨ 特性

- **8 大数据源，绝大多数「免 API Key」即可运行**：
  | 分类 | 数据源 | 是否需要 Key |
  |------|--------|--------------|
  | 🔥 热门Meme | Know Your Meme / Google News 兜底 | ❌ 免 |
  | 🌟 娱乐新闻 | RSS（Variety / Deadline / Billboard…） | ❌ 免 |
  | 🔥 热映资讯 | 豆瓣实时榜单 / TMDB | 豆瓣免，TMDB 可选 |
  | 🏈 体育热点 | RSS（ESPN / BBC Sport / Yahoo） | ❌ 免 |
  | 📹 视频热点 | B站热门 + TikTok（YouTube 可选） | B站/TikTok 免 |
  | 🔍 搜索趋势 | Google Trends RSS | ❌ 免 |
  | 𝕏 X热搜 | GetDayTrends 真实趋势（美区+全球） | ❌ 免 |
  | 🎵 TikTok | tikwm 跨区聚合 hashtag（按播放量排名） | ❌ 免 |
- **并发抓取**：`ThreadPoolExecutor` 并发拉取各平台，最坏耗时 ≈ 单源最慢（而非串行求和）。
- **复合热度评分**：Top3 金银铜徽章 + 🔥 高热标记，零数值源也能按排名拉开 0–100 分。
- **缓存优先秒开**：打开即渲染磁盘缓存，后台静默刷新，并显示「更新于 X 分钟前」。
- **定时自动刷新**：按配置间隔（默认 15 分钟）后台刷新缓存。
- **数据源配置**：开关 / 条数 / 排除关键词 / 刷新间隔 / 最低热度分，前端弹窗即时生效。
- **免费谷歌翻译**：单条 / 批量翻译标题，无需 API Key。

---

## 🚀 运行

```bash
pip install flask
python app.py
# 浏览器打开 http://localhost:5000
```

无需任何配置即可使用全部免 Key 数据源。

---

## 🌐 在线访问（部署）

GitHub 仓库只存代码、不能直接运行 Flask 后端，想要「浏览器直接打开的网页」有三种方式：

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/xiaohuan556/hotspot-radar)

👆 点击上方按钮即可 **一键部署到 Render**（用 GitHub 账号登录后自动按 `render.yaml` 创建服务，约 1 分钟得到 `https://hotspot-radar-xxx.onrender.com`）。

### 方式 A：一键部署到 Render（推荐，永久在线、免费）
1. 打开 https://render.com 用 GitHub 登录
2. New → Blueprints → 选中本仓库（已含 `render.yaml`）
3. 点击 Create → 约 1 分钟生成 `https://hotspot-radar-xxx.onrender.com`，这就是可直接打开的网页链接

### 方式 B：GitHub Pages 静态镜像（无需服务器，但数据走公共代理）
本仓库 `docs/index.html` 是纯前端版，已由 GitHub Pages 托管：
**https://xiaohuan556.github.io/hotspot-radar/**
> 说明：静态页在浏览器里直接抓取各数据源，需经公共 CORS 代理。
> 若你的网络访问代理较慢/被拦，请用方式 A 或方式 C 获得更稳定的体验。

### 方式 C：本机运行（最稳，数据直采）
见上方「运行」一节，`python app.py` 后打开 http://localhost:5000 即可。

### 可选增值源（在 `.env` 中填写）
```env
YOUTUBE_API_KEY=xxx     # 启用 YouTube 官方热榜
TMDB_API_KEY=xxx        # 启用 TMDB 本周电影趋势榜（替代豆瓣兜底）
NEWSAPI_KEY=xxx         # 启用 NewsAPI 娱乐/体育新闻
```
不填则对应分类自动回落到免 Key 的兜底源。

---

## 🔌 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/data` | 返回缓存 `{updated_at, data, summaries}`（秒开） |
| POST | `/api/refresh` | 触发一次刷新，返回最新数据（带防重入） |
| GET  | `/api/config` | 获取当前配置 |
| POST | `/api/config` | 保存配置（JSON） |
| POST | `/api/translate` | 翻译 `{text}` 或 `{texts:[...]}` |

---

## 📁 结构

```
hotspot-radar/
├── app.py              # Flask 后端（数据抓取 / 评分 / 缓存 / 配置 / 翻译）
├── templates/
│   └── index.html     # 前端仪表盘（Tab / 卡片 / 设置弹窗）
├── requirements.txt   # 仅依赖 flask
├── install.sh         # 一键安装
├── hotspot_config.json# 数据源配置（首次运行自动生成）
└── hotspot_cache.json # 热点缓存（自动生成）
```

---

## 📝 说明

本仓库的 Web 版（`app.py` + `templates`）是持续维护的版本，与桌面端 CreativeEnginePro 的热点雷达逻辑保持一致。
`docs/index.html` 为早期静态原型，仅供留档。
