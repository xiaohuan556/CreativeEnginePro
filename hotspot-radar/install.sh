#!/bin/bash
# 热点雷达 Web 版 — 一键安装（macOS / Linux）
# 用法：cd 到本目录后执行  bash install.sh
echo "📡 热点雷达 Web 版 - 安装中..."

# 安装 Python3（如缺失）
if ! command -v python3 &>/dev/null; then
  echo ">>> 未检测到 python3，请先安装 Python 3.10+"
  exit 1
fi

# 安装依赖（仅需 Flask，无需任何 API Key）
echo ">>> 安装依赖 (flask) ..."
python3 -m pip install --upgrade pip
python3 -m pip install flask

echo ""
echo "✅ 安装完成！"
echo "   启动：  python3 app.py"
echo "   打开：  http://localhost:5000"
echo ""
echo "💡 绝大多数数据源（X热搜 / TikTok / B站 / 豆瓣 / Google Trends / Meme）均免 Key 直采。"
echo "   如需 YouTube / TMDB / NewsAPI 等增值源，在 .env 中填入对应 Key 即可自动启用。"
