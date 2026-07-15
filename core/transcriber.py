"""
GlobalFlux AI - AI 台词提取与洗稿翻译模块
负责：
  1. 使用 OpenAI Whisper 对中文人声进行 ASR 转写
  2. 使用 LLM 进行本土化洗稿翻译，输出标准 SRT 格式
"""
import json
import re
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from config import (
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME,
    TARGET_LANGUAGE, WORK_DIR, ensure_work_dir,
    WHISPER_MODEL_SIZE, WHISPER_LANGUAGE,
)


# ── SRT 时间格式工具 ──
def seconds_to_srt_time(seconds: float) -> str:
    """将秒数转为 SRT 时间格式 HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_time_to_seconds(time_str: str) -> float:
    """将 SRT 时间格式转为秒数"""
    time_str = time_str.strip()
    match = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", time_str)
    if not match:
        raise ValueError(f"无效的 SRT 时间格式: {time_str}")
    h, m, s, ms = match.groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


# ── SRT 数据结构 ──
class SRTEntry:
    """单条 SRT 字幕"""
    def __init__(self, index: int, start: float, end: float, text: str, words: list = None):
        self.index = index
        self.start = start
        self.end = end
        self.text = text
        self.words = words or []  # [{word, start, end}, ...] from Whisper
    
    @property
    def duration(self) -> float:
        return self.end - self.start
    
    def to_srt_block(self) -> str:
        return (
            f"{self.index}\n"
            f"{seconds_to_srt_time(self.start)} --> {seconds_to_srt_time(self.end)}\n"
            f"{self.text}\n"
        )
    
    @classmethod
    def from_srt_block(cls, block: str) -> "SRTEntry":
        lines = block.strip().split("\n")
        index = int(lines[0].strip())
        times = lines[1].strip()
        start_str, end_str = times.split("-->")
        start = srt_time_to_seconds(start_str)
        end = srt_time_to_seconds(end_str)
        text = "\n".join(lines[2:]).strip()
        return cls(index, start, end, text)


def parse_srt(srt_text: str) -> List[SRTEntry]:
    """解析 SRT 文本为条目列表"""
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    entries = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        try:
            entries.append(SRTEntry.from_srt_block(block))
        except (ValueError, IndexError):
            continue
    return entries


def entries_to_srt(entries: List[SRTEntry]) -> str:
    """将条目列表转为 SRT 文本"""
    return "\n".join(e.to_srt_block() for e in entries)


# ── ASR 听写器 ──
class Transcriber:
    """Whisper ASR 听写器"""
    
    def __init__(self, model_size: Optional[str] = None, language: Optional[str] = None):
        self.model_size = model_size or WHISPER_MODEL_SIZE
        self.language = language or WHISPER_LANGUAGE
    
    def transcribe(
        self,
        audio_path: Path,
        output_srt: Optional[Path] = None
    ) -> List[SRTEntry]:
        """
        对音频进行 ASR 转写，返回带时间戳的 SRT 条目
        
        Args:
            audio_path: 输入音频文件路径（纯人声轨）
            output_srt: SRT 文件输出路径
        
        Returns:
            SRT 条目列表
        """
        try:
            import whisper
        except ImportError:
            raise RuntimeError(
                "Whisper 未安装。请执行:\n"
                "  pip install openai-whisper"
            )
        
        print(f"  加载 Whisper 模型: {self.model_size}")
        model = whisper.load_model(self.model_size)
        
        print(f"  开始转写（语言: {self.language}）...")
        result = model.transcribe(
            str(audio_path),
            language=self.language,
            verbose=False,
            word_timestamps=True
        )
        
        # 将 Whisper 片段转为 SRT 条目
        entries = []
        for i, segment in enumerate(result["segments"], 1):
            # 提取词级时间戳
            words_data = []
            for w in segment.get("words", []):
                w_text = w.get("word", "").strip()
                if w_text:
                    words_data.append({"word": w_text, "start": w.get("start", 0), "end": w.get("end", 0)})
            entry = SRTEntry(
                index=i,
                start=segment["start"],
                end=segment["end"],
                text=segment["text"].strip(),
                words=words_data
            )
            # 过滤过短或空白条目
            if entry.text and entry.duration >= 0.3:
                entries.append(entry)
        
        # 重新编号
        for i, entry in enumerate(entries, 1):
            entry.index = i
        
        # 保存 SRT
        if output_srt is None:
            output_srt = WORK_DIR / "source_zh.srt"
        
        srt_text = entries_to_srt(entries)
        output_srt.write_text(srt_text, encoding="utf-8")
        
        print(f"  ✓ 转写完成，共 {len(entries)} 条字幕 -> {output_srt.name}")
        return entries


# ── 内置翻译器（基于 Google Translate，无需 API Key）──
class Translator:
    """内置翻译器 - 使用 deep-translator (Google Translate)"""

    def __init__(
        self,
        api_key = None,
        base_url = None,
        model = None,
        target_lang = None
    ):
        self.target_lang = target_lang or TARGET_LANGUAGE

    def translate(
        self,
        source_entries: List[SRTEntry],
        output_srt: Optional[Path] = None,
        batch_size: int = 30
    ) -> List[SRTEntry]:
        """
        使用 Google Translate 翻译 SRT 条目

        Args:
            source_entries: 源语言 SRT 条目列表
            output_srt: 翻译后 SRT 输出路径
            batch_size: 保留参数（兼容旧接口），翻译引擎内部自行批处理

        Returns:
            翻译后的 SRT 条目列表
        """
        from core.builtin_translator import translate_srt_entries
        
        lang_name = {
            "en": "English", "th": "Thai", "vi": "Vietnamese",
            "pt": "Portuguese", "es": "Spanish", "id": "Indonesian",
            "ms": "Malay", "fil": "Filipino", "ar": "Arabic",
            "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
        }.get(self.target_lang, self.target_lang)
        
        total = len(source_entries)
        for i, entry in enumerate(source_entries, 1):
            print(f"  翻译 {i}/{total}...", end="\r")
        
        print(f"\n  翻译 {total} 条字幕到 {lang_name}...")
        
        try:
            translated = translate_srt_entries(source_entries, self.target_lang)
        except Exception as e:
            print(f"    ✗ 翻译失败: {e}")
            translated = source_entries  # 失败保留原文
        
        # 保存
        if output_srt is None:
            output_srt = WORK_DIR / f"target_{self.target_lang}.srt"
        
        srt_text = entries_to_srt(translated)
        output_srt.write_text(srt_text, encoding="utf-8")
        
        print(f"  ✓ 翻译完成，共 {len(translated)} 条字幕 -> {output_srt.name}")
        return translated


# ── 便捷函数 ──
def transcribe_and_translate(
    vocals_path: Path,
    target_lang: Optional[str] = None
) -> Tuple[List[SRTEntry], List[SRTEntry]]:
    """
    完整流程：听写中文 -> 翻译为目标语言
    
    Args:
        vocals_path: 纯人声轨音频路径
        target_lang: 目标语言代码
    
    Returns:
        (source_entries, translated_entries)
    """
    ensure_work_dir()
    
    # ASR 听写
    print("[1/2] AI 听写中文台词...")
    transcriber = Transcriber()
    source_entries = transcriber.transcribe(vocals_path)
    
    # LLM 翻译
    print("[2/2] AI 洗稿翻译...")
    translator = Translator(target_lang=target_lang)
    translated_entries = translator.translate(source_entries)
    
    return source_entries, translated_entries


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python transcriber.py <vocals_audio_path> [target_lang]")
        sys.exit(1)
    
    audio = Path(sys.argv[1])
    lang = sys.argv[2] if len(sys.argv) > 2 else "en"
    src, tgt = transcribe_and_translate(audio, lang)
    print(f"\n完成: 源 {len(src)} 条 -> 翻译 {len(tgt)} 条")
