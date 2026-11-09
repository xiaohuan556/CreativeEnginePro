"""
内置翻译模块 - 基于 deep-translator (Google Translate) 的免费翻译
替代所有 LLM/AI 翻译功能，无需 API Key
"""
import re
from typing import List, Optional
from deep_translator import GoogleTranslator, MyMemoryTranslator


# 语言代码映射
LANG_MAP = {
    "zh": "chinese (simplified)",
    "zh-cn": "chinese (simplified)",
    "zh-tw": "chinese (traditional)",
    "en": "english",
    "ja": "japanese",
    "ko": "korean",
    "th": "thai",
    "vi": "vietnamese",
    "es": "spanish",
    "pt": "portuguese",
    "ar": "arabic",
    "id": "indonesian",
    "ms": "malay",
    "fil": "filipino",
    "fr": "french",
    "de": "german",
    "it": "italian",
    "ru": "russian",
    "hi": "hindi",
    "tr": "turkish",
    "nl": "dutch",
    "pl": "polish",
}

# 中文语种名 → 代码（自定义翻译输入用，如输入"法语"→"fr"）
CN_NAME_MAP = {
    "中文": "zh", "英语": "en", "英文": "en",
    "日语": "ja", "日文": "ja",
    "韩语": "ko", "韩文": "ko", "朝鲜语": "ko",
    "泰语": "th", "泰文": "th",
    "越南语": "vi", "越语": "vi",
    "西班牙语": "es", "西语": "es",
    "葡萄牙语": "pt", "葡语": "pt",
    "阿拉伯语": "ar", "阿语": "ar",
    "印尼语": "id", "印度尼西亚语": "id",
    "马来语": "ms", "马来西亚语": "ms",
    "菲律宾语": "fil", "法语": "fr",
    "德语": "de", "意大利语": "it", "意语": "it",
    "俄语": "ru", "俄罗斯语": "ru",
    "印地语": "hi", "土耳其语": "tr",
    "荷兰语": "nl", "波兰语": "pl",
}


def _normalize_lang(lang: str) -> str:
    """标准化语言代码为 deep-translator 接受的格式"""
    lang = lang.strip()
    # 先查中文名称映射
    if lang in CN_NAME_MAP:
        lang = CN_NAME_MAP[lang]
    lang = lang.lower()
    if lang in LANG_MAP:
        return LANG_MAP[lang]
    prefix = lang.split("-")[0]
    if prefix in LANG_MAP:
        return LANG_MAP[prefix]
    return lang


def translate_text(text: str, target_lang: str, source_lang: str = "auto") -> str:
    """
    翻译单段文本（带重试）

    Args:
        text: 待翻译文本
        target_lang: 目标语言代码 (en, ja, ko, th...)
        source_lang: 源语言 (auto 自动检测)

    Returns:
        翻译后的文本
    """
    if not text or not text.strip():
        return text
    
    target = _normalize_lang(target_lang)
    
    last_error = None
    for attempt in range(3):
        try:
            translator = GoogleTranslator(source=source_lang, target=target)
            if len(text) <= 4500:
                return translator.translate(text)
            paragraphs = text.split("\n")
            results = []
            chunk = ""
            for p in paragraphs:
                if len(chunk) + len(p) < 4500:
                    chunk = chunk + "\n" + p if chunk else p
                else:
                    if chunk:
                        results.append(translator.translate(chunk))
                    chunk = p
            if chunk:
                results.append(translator.translate(chunk))
            return "\n".join(results)
        except Exception as e:
            last_error = e
            if attempt < 2:
                import time; time.sleep(1.0)
            continue
    raise RuntimeError(f"翻译失败（已重试3次）: {last_error}")


def translate_batch(texts: List[str], target_lang: str, source_lang: str = "auto") -> List[str]:
    """批量翻译多段文本"""
    return [translate_text(t, target_lang, source_lang) for t in texts]


_MEMORY_CODES = {
    "zh": "zh-CN", "en": "en-US", "ja": "ja-JP", "ko": "ko-KR",
    "th": "th-TH", "vi": "vi-VN", "es": "es-ES", "pt": "pt-PT",
    "ar": "ar-SA", "id": "id-ID", "fr": "fr-FR", "de": "de-DE",
    "it": "it-IT", "ru": "ru-RU", "hi": "hi-IN", "tr": "tr-TR",
    "nl": "nl-NL", "pl": "pl-PL", "ms": "ms-MY", "fil": "fil-PH",
}
_SRT_MARKER = re.compile(r"\[\[\s*SRT\s*0*(\d+)\s*\]\]", re.IGNORECASE)


def _memory_code(language: str) -> str:
    language = CN_NAME_MAP.get(language, language).lower()
    return _MEMORY_CODES.get(language, _MEMORY_CODES.get(
        language.split("-")[0], language))


def _srt_chunks(entries: list, *, max_chars: int = 3500,
                max_items: int = 20) -> list:
    """Pack sentences into requests below Google's 5000-character limit."""
    chunks = []
    chunk = []
    size = 0
    for index, entry in enumerate(entries):
        line = f"[[SRT{index:06d}]] {entry.text.strip()}"
        if chunk and (size + len(line) + 1 > max_chars
                      or len(chunk) >= max_items):
            chunks.append(chunk)
            chunk = []
            size = 0
        chunk.append((index, entry, line))
        size += len(line) + 1
    if chunk:
        chunks.append(chunk)
    return chunks


def _split_srt_translation(text: str, chunk: list) -> list:
    markers = list(_SRT_MARKER.finditer(text or ""))
    if len(markers) != len(chunk):
        raise RuntimeError("批量翻译未保留句子编号")
    translated = []
    for position, (index, _entry, _line) in enumerate(chunk):
        if int(markers[position].group(1)) != index:
            raise RuntimeError("批量翻译的句子顺序不一致")
        end = markers[position + 1].start() if position + 1 < len(markers) else len(text)
        value = text[markers[position].end():end].strip()
        if not value:
            raise RuntimeError("翻译服务返回空文本")
        translated.append(value)
    return translated


def _memory_translate_sentence(translator, value: str, target_lang: str) -> str:
    """Keep individual fallback requests within MyMemory's text limit."""
    import textwrap

    pieces = textwrap.wrap(value.strip(), width=400,
                           break_long_words=True, break_on_hyphens=False)
    if not pieces:
        return ""
    translated = [translator.translate(piece) for piece in pieces]
    if any(not part or not part.strip() for part in translated):
        raise RuntimeError("备用翻译服务返回空文本")
    joiner = "" if target_lang.lower().split("-")[0] in {"zh", "ja", "ko"} else " "
    return joiner.join(part.strip() for part in translated)


def translate_srt_entries(entries: list, target_lang: str,
                          *, strict: bool = False) -> list:
    """
    翻译 SRT 条目列表（保持时间戳结构）

    Args:
        entries: SRTEntry 列表 (from core.transcriber)
        target_lang: 目标语言代码

    Returns:
        翻译后的 SRTEntry 列表（文本已翻译，时间戳不变）
    """
    from core.transcriber import SRTEntry
    
    if not entries:
        return []
    target = _normalize_lang(target_lang)
    google = GoogleTranslator(source="auto", target=target)
    memory = None
    google_failed = False
    translated_texts = [None] * len(entries)
    for chunk in _srt_chunks(entries):
        if google_failed:
            break
        try:
            if len(chunk) == 1:
                translated = [google.translate(chunk[0][1].text.strip())]
                if not translated[0] or not translated[0].strip():
                    raise RuntimeError("翻译服务返回空文本")
            else:
                translated = _split_srt_translation(
                    google.translate("\n".join(item[2] for item in chunk)), chunk)
            for (index, _entry, _line), text in zip(chunk, translated):
                translated_texts[index] = text
        except Exception:
            # One rate-limit response means later Google requests are
            # unlikely to succeed; use the independent fallback instead.
            google_failed = True
    if google_failed:
        from config import WHISPER_LANGUAGE
        import time
        try:
            memory = MyMemoryTranslator(
                source=_memory_code(WHISPER_LANGUAGE),
                target=_memory_code(target_lang))
            requests_sent = 0
            for chunk in _srt_chunks(entries, max_chars=400, max_items=5):
                pending = [item for item in chunk
                           if translated_texts[item[0]] is None]
                if not pending:
                    continue
                if requests_sent:
                    time.sleep(0.25)
                if len(pending) == 1:
                    translated = [_memory_translate_sentence(
                        memory, pending[0][1].text, target_lang)]
                else:
                    translated = _split_srt_translation(
                        memory.translate("\n".join(item[2] for item in pending)),
                        pending)
                for (index, _entry, _line), text in zip(pending, translated):
                    translated_texts[index] = text
                requests_sent += 1
        except Exception as exc:
            if strict:
                raise RuntimeError(f"翻译服务暂时不可用：{exc}") from exc
            for index, entry in enumerate(entries):
                if translated_texts[index] is None:
                    translated_texts[index] = entry.text
    return [SRTEntry(entry.index, entry.start, entry.end, text)
            for entry, text in zip(entries, translated_texts)]
