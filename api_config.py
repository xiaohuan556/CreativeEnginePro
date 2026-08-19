# -*- coding: utf-8 -*-
"""
CreativeEnginePro —— 统一 API 容器（Single Source of Truth）
============================================================

本文件集中声明产品用到的【所有外部 API】。以后要管理 / 切换 / 新增 API，
只改这一个文件即可：

  * 把 DeepSeek 换成 ChatGPT
      → 填 OPENAI_API_KEY（或在下方 llm 条目的 default_base_url / default_model 改默认）
        （LLM_MODE=openai 走 OpenAI；LLM_MODE=deepseek 走 DeepSeek）
  * 新增一个图片 / 视频模型 API
      → 在对应 CATEGORY 下加一个 APIEntry，UI 与配置自动识别
  * 改默认模型 / 默认 endpoint
      → 只动对应条目的 default_model / default_base_url

分类（CATEGORY）：
  llm     大模型 / 文本生成（脚本、润色、翻译）
  tts     语音合成 TTS（配音）
  image   图像生成 / AI 绘画
  video   视频生成 / AI 视频
  music   音乐素材下载
  hotspot 热点数据聚合

约定：
  * env_key 是 .env 里的变量名，保持向后兼容（旧代码 os.getenv("DEEPGRAM_KEY") 仍可用，
    因为 config.py 在加载本容器后会把全部值写回 os.environ）。
  * const_name 是 config.py 导出的模块级常量名（供其他模块 `from config import X`）。
  * 不要把真实 key 写进 default_value，只放占位提示。
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# ── 项目根 & .env 路径 ──
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    PROJECT_ROOT = Path(sys.executable).parent
else:
    PROJECT_ROOT = Path(__file__).parent
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass
class APIEntry:
    name: str                 # 内部唯一 id
    category: str             # llm / tts / image / video / music / hotspot
    label: str                # 中文显示名
    env_key: str              # .env 变量名（向后兼容）
    default_base_url: str = ""
    default_model: str = ""
    default_value: str = ""   # 占位 / 默认值（不要写真 key）
    requires_key: bool = True
    notes: str = ""
    const_name: str = ""      # config.py 导出的模块级常量名（空 = 不导出）
    tts_engine: str = ""      # 若为付费 TTS，则填对应引擎 id（siliconflow/deepgram/...）
    placeholder: str = "sk-..."  # 设置面板里的占位提示

    def value(self) -> str:
        """当前生效的 key / 值（实时从环境变量读取）"""
        return os.environ.get(self.env_key, self.default_value)

    # 注：各功能的 Base URL / 模型名 不在此处统一解析。
    # LLM 的端点由 config.py 读取 .env 的 LLM_BASE_URL（设置面板写入）决定；
    # 其余付费 API 的调用端点由各自功能代码管理。
    # （早期曾用 env_key+"_BASE_URL" 的通用写法，但对 LLM 会误生成
    #  LLM_API_KEY_BASE_URL 这一从未被写入/读取的幽灵变量，现已移除。）


# ── 分类说明 ──
CATEGORIES = {
    "llm": "大模型 / 文本生成（脚本、润色、翻译）",
    "tts": "语音合成 TTS（配音）",
    "image": "图像生成 / AI 绘画",
    "video": "视频生成 / AI 视频",
    "music": "音乐素材下载",
    "hotspot": "热点数据聚合",
}


def _e(name, category, label, env_key, **kw):
    return APIEntry(name=name, category=category, label=label, env_key=env_key, **kw)


# ───────────────────────── LLM ─────────────────────────
# 说明：LLM_API_KEY 是「当前生效的大模型 Key」单一变量，DeepSeek / OpenAI / 自定义
# 都写这里（靠 LLM_MODE 区分用哪家）。OPENAI_API_KEY 仅作为 OpenAI 模式 / 图像视频
# 复用的可选变量，纯 DeepSeek 用户无需填写。
LLM_APIS = [
    _e("llm", "llm", "大模型 Key（ModelHub 统一代理）", "LLM_API_KEY",
       default_base_url="https://modelhub.ailemac.com/api/v1",
       default_model="gpt-5.5",
       const_name="LLM_API_KEY",
       notes="ModelHub 统一代理 Key；LLM / GPT-Image / Veo 通用。"),
    _e("llm_mode", "llm", "LLM 模式", "LLM_MODE",
       default_value="openai", requires_key=False,
       const_name="LLM_MODE",
       notes="deepseek / openai（当前默认）/ custom_llm"),
    _e("openai_key", "llm", "OpenAI Key（ChatGPT / GPT-Image / Veo 复用）", "OPENAI_API_KEY",
       default_base_url="https://api.openai.com/v1",
       default_model="gpt-5.5",
       const_name="OPENAI_API_KEY", requires_key=False,
       notes="LLM_MODE=openai 时图像/视频/LLM 可复用此 key；"
             "若使用统一代理网关，请同时设置 OPENAI_BASE_URL。"),
    _e("openai_base", "llm", "OpenAI Base URL（可选项）", "OPENAI_BASE_URL",
       default_value="https://api.openai.com/v1",
       requires_key=False,
       const_name="OPENAI_BASE_URL"),
    _e("custom_llm_key", "llm", "自定义 LLM Key（可选项）", "CUSTOM_LLM_KEY",
       requires_key=False, const_name="CUSTOM_LLM_KEY"),
    _e("custom_llm_url", "llm", "自定义 LLM BaseURL（可选项）", "CUSTOM_LLM_URL",
       requires_key=False, const_name="CUSTOM_LLM_URL"),
    _e("custom_llm_model", "llm", "自定义 LLM 模型（可选项）", "CUSTOM_LLM_MODEL",
       default_value="gpt-3.5-turbo", requires_key=False,
       const_name="CUSTOM_LLM_MODEL"),
]

# ───────────────────────── TTS ─────────────────────────
TTS_APIS = [
    _e("siliconflow", "tts", "硅基流动 CosyVoice2", "SILICONFLOW_KEY",
       default_base_url="https://api.siliconflow.cn/v1",
       default_model="CosyVoice2",
       const_name="SILICONFLOW_KEY", tts_engine="siliconflow",
       placeholder="sk-..."),
    _e("deepgram", "tts", "Deepgram Aura", "DEEPGRAM_KEY",
       default_base_url="https://api.deepgram.com/v1",
       default_model="aura-asteria-en",
       const_name="DEEPGRAM_KEY", tts_engine="deepgram",
       placeholder="Deepgram API key"),
    _e("elevenlabs", "tts", "ElevenLabs", "ELEVENLABS_API_KEY",
       default_base_url="https://api.elevenlabs.io/v1",
       default_model="eleven_multilingual_v2",
       const_name="ELEVENLABS_API_KEY", tts_engine="elevenlabs",
       placeholder="sk-..."),
    _e("fish_audio", "tts", "Fish Audio", "FISH_AUDIO_KEY",
       default_base_url="https://api.fish.audio/v1",
       default_model="",
       const_name="FISH_AUDIO_KEY", tts_engine="fish_audio",
       placeholder="fish API key"),
    _e("custom_tts_key", "tts", "自定义 TTS Key", "CUSTOM_TTS_KEY",
       requires_key=False, const_name="CUSTOM_TTS_KEY"),
    _e("custom_tts_url", "tts", "自定义 TTS 音色列表 URL", "CUSTOM_TTS_VOICES_URL",
       requires_key=False, const_name="CUSTOM_TTS_VOICES_URL"),
]

# ─────────────────────── 图像生成 ───────────────────────
# 火山方舟（Ark）Seedream 接入说明：
#   * SEEDREAM_API_KEY（.env）是「真实 Ark API Key（Bearer Token）」，用户给的
#     `ark-...` 就是它，已写入 .env。豆包模型共用同一个 Key，无需为每个模型单独申请。
#   * 模型用「发布版模型 ID」即可，无需自建推理接入点（ep- 端点）：
#       Seedream 5.0 Pro → doubao-seedream-5-0-pro-260628（default_model 已内置）
#     若你的账号模型 ID 不同，可在 .env 用 SEEDREAM_MODEL 覆盖，或改本条目 default_model。
IMAGE_APIS = [
    _e("seedream", "image", "Seedream 5.0 Pro (火山方舟)", "SEEDREAM_API_KEY",
       default_base_url="https://ark.cn-beijing.volces.com/api/v3",
       default_model="doubao-seedream-5-0-pro-260628",
       const_name="SEEDREAM_API_KEY", placeholder="Ark API Key (Bearer)",
       notes="SEEDREAM_API_KEY=Ark 密钥(已写入.env)；模型用发布版 ID 无需端点。"),
    _e("openai_image", "image", "OpenAI 图像 (GPT-Image-2)", "OPENAI_API_KEY",
       default_base_url="https://modelhub.ailemac.com/api/v1",
       default_model="gpt-image-2",
       const_name="",  # 复用 OPENAI_API_KEY
       notes="复用 OPENAI_API_KEY；默认模型 gpt-image-2。"),
    _e("flux", "image", "FLUX (待接入)", "FLUX_API_KEY",
       default_base_url="https://api.bfl.ml/v1",
       default_model="flux-pro",
       const_name="FLUX_API_KEY", placeholder="sk-..."),
]

# ─────────────────────── 视频生成 ───────────────────────
# Seedance 与 Seedream 共用 SEEDREAM_API_KEY。Provider 会按 Key 类型自动选路：
# ark-* → 火山方舟官方；其他/ModelHub Key → ModelHub 豆包兼容入口。
VIDEO_APIS = [
    _e("veo", "video", "Google Veo 3.1 (ModelHub 代理)", "OPENAI_API_KEY",
       default_base_url="https://modelhub.ailemac.com/api/v1",
       default_model="veo-3.1-generate-preview",
       const_name="",  # 复用 OPENAI_API_KEY
       notes="ModelHub 统一代理，与 ChatGPT/GPT-Image 共用一个 sk- key。"),
    _e("sora", "video", "OpenAI Sora (待接入)", "OPENAI_API_KEY",
       default_base_url="https://api.openai.com/v1",
       default_model="sora",
       const_name=""),  # 复用 OPENAI_API_KEY
    _e("seedance", "video", "Seedance 2.0 (豆包)", "SEEDREAM_API_KEY",
       default_base_url="https://ark.cn-beijing.volces.com/api/v3",
       default_model="doubao-seedance-2-0-260128",
       const_name="",  # 复用 SEEDREAM_API_KEY
       notes="与 Seedream 共用 Key；ark-* 自动直连方舟，ModelHub Key 自动走 /doubao/v3。"),
]

# ─────────────────────── 音乐素材 ───────────────────────
MUSIC_APIS = [
    _e("tribe_of_noise", "music", "Tribe of Noise (CC)", "TON_COOKIE",
       requires_key=False, const_name="TON_COOKIE",
       notes="用免费账户 Cookie 下载 CC-BY 音乐。"),
]

# ─────────────────────── 热点数据 ───────────────────────
HOTSPOT_APIS = [
    _e("youtube", "hotspot", "YouTube 热榜", "YOUTUBE_API_KEY",
       const_name="YOUTUBE_KEY", placeholder="AIza..."),
    _e("tmdb", "hotspot", "TMDB 影视趋势", "TMDB_API_KEY",
       const_name="TMDB_KEY", placeholder="your-tmdb-key"),
    _e("newsapi", "hotspot", "NewsAPI", "NEWSAPI_KEY",
       const_name="NEWSAPI_KEY", placeholder="your-newsapi-key"),
    _e("trendmcp", "hotspot", "TrendMCP", "TRENDMCP_KEY",
       const_name="TRENDMCP_KEY", placeholder=""),
]


ALL_APIS = LLM_APIS + TTS_APIS + IMAGE_APIS + VIDEO_APIS + MUSIC_APIS + HOTSPOT_APIS
REGISTRY = {a.name: a for a in ALL_APIS}


# ── 确保 .env 已载入 os.environ ──
# config.py 在导入时也会做同样的事；这里补一次，保证本模块被单独引用
# （例如设置面板）时，APIEntry.value() 仍能读到最新 .env 配置，而不是空值。
def _ensure_env():
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
_ensure_env()


# ── 便捷访问 ──
def get(name: str) -> APIEntry:
    return REGISTRY[name]


def value(name: str) -> str:
    return REGISTRY[name].value()


def by_category(cat: str):
    return [a for a in ALL_APIS if a.category == cat]


def all_entries():
    return list(ALL_APIS)


# ── .env 读写（供设置面板 / 配音面板调用）──
def read_env() -> dict:
    """读取 .env 为 {KEY: VALUE} 字典。"""
    d = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def write_env(updates: dict):
    """更新 .env：updates = {ENV_KEY: value}。value 为空字符串表示删除该键。

    同时把变更写回 os.environ，使运行时立即生效。
    """
    cur = read_env()
    for k, v in updates.items():
        if v == "":
            cur.pop(k, None)
        else:
            cur[k] = v
    lines = [f"{k}={v}" for k, v in cur.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in updates.items():
        if v == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def summary() -> str:
    """返回可读的全部 API 清单，便于管理时核对。"""
    lines = ["CreativeEnginePro —— API 容器清单", "=" * 36]
    for cat, desc in CATEGORIES.items():
        lines.append(f"\n[{cat}] {desc}")
        for a in by_category(cat):
            cur = a.value() or "(未配置)"
            if a.requires_key and cur not in ("(未配置)",):
                cur = cur[:6] + "..." if len(cur) > 6 else cur
            lines.append(f"  - {a.label}  env={a.env_key}  "
                         f"model={a.default_model or '-'}  value={cur}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
