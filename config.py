"""
CreativeEnginePro - 统一配置管理
融合: 图片/视频/轮播 + TTS语音/译制/脚本生成
"""
import os
import sys
from pathlib import Path

# ── 项目根目录 ──
# 打包后 sys.executable 是 exe 位置，__file__ 是临时解压目录
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    PROJECT_ROOT = Path(sys.executable).parent
else:
    PROJECT_ROOT = Path(__file__).parent

# ── 加载 .env ──
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _value = _line.partition("=")
            _key = _key.strip()
            _value = _value.strip().strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _value

# ── 临时工作目录 ──
WORK_DIR = PROJECT_ROOT / "work_temp"
OUTPUT_DIR = PROJECT_ROOT / "work_output"


def ensure_work_dir():
    """确保工作目录存在"""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── FFmpeg 路径 ──
def _get_ffmpeg_bin() -> str:
    """统一 FFmpeg 路径解析，委托 utils.ffmpeg_utils"""
    from utils.ffmpeg_utils import get_ffmpeg_path
    return get_ffmpeg_path()

def _get_ffprobe_bin() -> str:
    ffmpeg = _get_ffmpeg_bin()
    if ffmpeg.endswith(".exe"):
        return ffmpeg.replace("ffmpeg.exe", "ffprobe.exe")
    return ffmpeg.replace("ffmpeg", "ffprobe")

FFMPEG_BIN = _get_ffmpeg_bin()
FFPROBE_BIN = _get_ffprobe_bin()

# ── API 配置（统一容器：见 api_config.py，单一来源）──
from api_config import ALL_APIS as _ALL_APIS

# 自动导出所有纯 env 常量（保持向后兼容：其他模块仍 `from config import X`）
for _api in _ALL_APIS:
    if _api.const_name:
        globals()[_api.const_name] = _api.value()

# ── LLM 派生配置 ──
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL_OVERRIDE = os.getenv("LLM_BASE_URL", "")  # 自定义 base url（如 deepseek 官方地址）

# 当前生效的「大模型 Key」单一变量：优先读 LLM_API_KEY，
# 兼容旧配置里把 DeepSeek key 写在 OPENAI_API_KEY 的情况。
LLM_API_KEY = os.getenv("LLM_API_KEY") or OPENAI_API_KEY

if LLM_MODE == "custom_llm" and CUSTOM_LLM_KEY:
    LLM_API_KEY = CUSTOM_LLM_KEY
    LLM_BASE_URL = CUSTOM_LLM_URL or "https://api.openai.com/v1"
    LLM_MODEL_NAME = CUSTOM_LLM_MODEL or "gpt-3.5-turbo"
elif LLM_MODE == "openai":
    LLM_BASE_URL = LLM_BASE_URL_OVERRIDE or OPENAI_BASE_URL
    LLM_MODEL_NAME = LLM_MODEL
else:  # deepseek（默认）
    LLM_BASE_URL = LLM_BASE_URL_OVERRIDE or "https://api.deepseek.com/v1"
    LLM_MODEL_NAME = LLM_MODEL

# ── Whisper ASR ──
WHISPER_MODE = os.getenv("WHISPER_MODE", "local")
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "zh")

# ── 翻译 ──
TARGET_LANGUAGE = os.getenv("TARGET_LANGUAGE", "en")

# ── TTS 引擎 ──
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge")
EDGE_TTS_DEFAULT_VOICE = os.getenv("EDGE_TTS_DEFAULT_VOICE", "zh-CN-XiaoxiaoNeural")
EDGE_TTS_DEFAULT_RATE = os.getenv("EDGE_TTS_DEFAULT_RATE", "+0%")

TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "")
TTS_MODEL_ID = os.getenv("TTS_MODEL_ID", "eleven_multilingual_v2")
TTS_STABILITY = float(os.getenv("TTS_STABILITY", "0.55"))
TTS_SIMILARITY_BOOST = float(os.getenv("TTS_SIMILARITY_BOOST", "0.75"))

# ── 音画对齐 ──
MAX_TEMPO_RATIO = 1.25
BGM_VOICE_RATIO = (0.7, 0.3)

# ── 混剪 ──
OUTPUT_WIDTH = int(os.getenv("OUTPUT_WIDTH", "0"))
OUTPUT_HEIGHT = int(os.getenv("OUTPUT_HEIGHT", "0"))
OUTPUT_ASPECT = os.getenv("OUTPUT_ASPECT", "original")

# ── 音量 ──
BGM_VOLUME = float(os.getenv("BGM_VOLUME", "0.3"))
VOICE_VOLUME = float(os.getenv("VOICE_VOLUME", "0.7"))

# ── 去重 ──
DEDUP_THRESHOLD = float(os.getenv("DEDUP_THRESHOLD", "0.95"))

# ── 性能与缓存 ──
DECODER_BUFFER = int(os.getenv("DECODER_BUFFER", "24"))   # 解码器 RingBuffer 窗口帧数（越大越流畅越占内存）
THUMB_SIZE = int(os.getenv("THUMB_SIZE", "320"))          # 素材库缩略图宽度(px)，高 = 宽 * 9/16
CACHE_MAX_GB = float(os.getenv("CACHE_MAX_GB", "0"))      # 缓存自动清理阈值(GB)，0 = 关闭自动清理

# ── 预设声线 ──
VOICE_PRESETS = {
    "passionate_female": {
        "name": "激情带货女", "voice_id": "", "stability": 0.4, "similarity_boost": 0.8,
    },
    "mature_male": {
        "name": "成熟霸总男", "voice_id": "", "stability": 0.6, "similarity_boost": 0.75,
    },
    "villain": {
        "name": "反派阴险音", "voice_id": "", "stability": 0.5, "similarity_boost": 0.7,
    },
    "narrator_young": {
        "name": "欧美年轻解说", "voice_id": "", "stability": 0.45, "similarity_boost": 0.8,
    },
}
