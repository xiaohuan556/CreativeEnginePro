"""
内置翻译模块 - 基于 deep-translator (Google Translate) 的免费翻译
替代所有 LLM/AI 翻译功能，无需 API Key
"""
from typing import List, Optional
from deep_translator import GoogleTranslator


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


def translate_srt_entries(entries: list, target_lang: str) -> list:
    """
    翻译 SRT 条目列表（保持时间戳结构）

    Args:
        entries: SRTEntry 列表 (from core.transcriber)
        target_lang: 目标语言代码

    Returns:
        翻译后的 SRTEntry 列表（文本已翻译，时间戳不变）
    """
    from core.transcriber import SRTEntry
    
    target = _normalize_lang(target_lang)
    translator = GoogleTranslator(source="auto", target=target)
    
    result = []
    for entry in entries:
        try:
            translated_text = translator.translate(entry.text.strip())
        except Exception:
            translated_text = entry.text  # 翻译失败保留原文
        
        result.append(SRTEntry(
            index=entry.index,
            start=entry.start,
            end=entry.end,
            text=translated_text
        ))
    
    return result
