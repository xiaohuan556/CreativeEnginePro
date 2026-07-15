"""
小欢语音 - 千语种自动识别 TTS 引擎
自动检测语言 → edge-tts优先 → gTTS兜底
启动时预热语言检测，避免首次卡顿
"""
import asyncio
import tempfile
import os
from pathlib import Path

# ── 延迟预热（不再模块级执行，避免阻塞主线程）──
_warmup_done = False

def _ensure_warmup():
    """首次使用时才执行预热，不在 import 时阻塞"""
    global _warmup_done
    if _warmup_done:
        return
    _warmup_done = True
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0
        detect("hello")
    except Exception:
        pass
    try:
        async def _warm_edge():
            import edge_tts
            await edge_tts.list_voices()
        asyncio.run(_warm_edge())
    except Exception:
        pass

# edge-tts 语言 → 语音缓存
_EDGE_CACHE: dict = {}

# 语言代码 → 中文名
LANG_NAMES = {
    "af":"南非语","am":"阿姆哈拉语","ar":"阿拉伯语","az":"阿塞拜疆语",
    "bg":"保加利亚语","bn":"孟加拉语","bs":"波斯尼亚语","ca":"加泰罗尼亚语",
    "cs":"捷克语","cy":"威尔士语","da":"丹麦语","de":"德语",
    "el":"希腊语","en":"英语","es":"西班牙语","et":"爱沙尼亚语",
    "eu":"巴斯克语","fa":"波斯语","fi":"芬兰语","fil":"菲律宾语",
    "fr":"法语","ga":"爱尔兰语","gl":"加利西亚语","gu":"古吉拉特语",
    "ha":"豪萨语","he":"希伯来语","hi":"印地语","hr":"克罗地亚语",
    "hu":"匈牙利语","id":"印尼语","is":"冰岛语","it":"意大利语",
    "iu":"因纽特语","ja":"日语","jv":"爪哇语","ka":"格鲁吉亚语",
    "kk":"哈萨克语","km":"高棉语","kn":"卡纳达语","ko":"韩语",
    "la":"拉丁语","lo":"老挝语","lt":"立陶宛语","lv":"拉脱维亚语",
    "mk":"马其顿语","ml":"马拉雅拉姆语","mn":"蒙古语","mr":"马拉地语",
    "ms":"马来语","mt":"马耳他语","my":"缅甸语","nb":"挪威语",
    "ne":"尼泊尔语","nl":"荷兰语","pa":"旁遮普语","pl":"波兰语",
    "ps":"普什图语","pt":"葡萄牙语","ro":"罗马尼亚语","ru":"俄语",
    "si":"僧伽罗语","sk":"斯洛伐克语","sl":"斯洛文尼亚语","so":"索马里语",
    "sq":"阿尔巴尼亚语","sr":"塞尔维亚语","su":"巽他语","sv":"瑞典语",
    "sw":"斯瓦希里语","ta":"泰米尔语","te":"泰卢固语","th":"泰语",
    "tr":"土耳其语","uk":"乌克兰语","ur":"乌尔都语","uz":"乌兹别克语",
    "vi":"越南语","zh":"中文","zh-cn":"中文","zh-tw":"中文(台湾)","zu":"祖鲁语",
    "yue":"粤语","iw":"希伯来语","jw":"爪哇语","no":"挪威语",
}  # {lang_code: voice_name}


class AutoLangTTSEngine:
    """自动语言识别 TTS — edge-tts(75语种) + gTTS(69语种)"""

    LANG_MAP = {
        "af":"af-ZA","am":"am-ET","ar":"ar-SA","az":"az-AZ",
        "bg":"bg-BG","bn":"bn-IN","bs":"bs-BA","ca":"ca-ES",
        "cs":"cs-CZ","cy":"cy-GB","da":"da-DK","de":"de-DE",
        "el":"el-GR","en":"en-US","es":"es-ES","et":"et-EE",
        "fa":"fa-IR","fi":"fi-FI","fil":"fil-PH","fr":"fr-FR",
        "ga":"ga-IE","gl":"gl-ES","gu":"gu-IN","he":"he-IL",
        "hi":"hi-IN","hr":"hr-HR","hu":"hu-HU","id":"id-ID",
        "is":"is-IS","it":"it-IT","iu":"iu-CA","ja":"ja-JP",
        "jv":"jv-ID","ka":"ka-GE","kk":"kk-KZ","km":"km-KH",
        "kn":"kn-IN","ko":"ko-KR","lo":"lo-LA","lt":"lt-LT",
        "lv":"lv-LV","mk":"mk-MK","ml":"ml-IN","mn":"mn-MN",
        "mr":"mr-IN","ms":"ms-MY","mt":"mt-MT","my":"my-MM",
        "nb":"nb-NO","ne":"ne-NP","nl":"nl-NL","pl":"pl-PL",
        "ps":"ps-AF","pt":"pt-PT","ro":"ro-RO","ru":"ru-RU",
        "si":"si-LK","sk":"sk-SK","sl":"sl-SI","so":"so-SO",
        "sq":"sq-AL","sr":"sr-RS","su":"su-ID","sv":"sv-SE",
        "sw":"sw-KE","ta":"ta-IN","te":"te-IN","th":"th-TH",
        "tr":"tr-TR","uk":"uk-UA","ur":"ur-PK","uz":"uz-UZ",
        "vi":"vi-VN","zh":"zh-CN","zu":"zu-ZA",
        "zh-cn":"zh-CN","zh-tw":"zh-TW",
        "iw":"he-IL","jw":"jv-ID","no":"nb-NO",
    }

    def __init__(self):
        pass

    def synthesize_segment(self, text: str, output_path, **kwargs) -> Path:
        _ensure_warmup()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lang = self._detect(text)

        # 文件名追加语种后缀（保留原始stem）
        lang_name = LANG_NAMES.get(lang, lang.upper())

        try:
            if lang in self.LANG_MAP:
                r = self._edge_tts(text, lang, output_path)
                # 加语种后缀
                final = output_path.with_stem(f"{lang_name}_{output_path.stem}")
                if final.exists(): final.unlink(missing_ok=True)
                r.rename(final)
                return final
        except Exception:
            pass

        try:
            r = self._gtts(text, lang, output_path)
            final = output_path.with_stem(f"{lang_name}_{output_path.stem}")
            if final.exists(): final.unlink(missing_ok=True)
            r.rename(final)
            return final
        except Exception as e:
            raise RuntimeError(f"千语种引擎失败({lang}): {e}")

    def _detect(self, text: str) -> str:
        try:
            from langdetect import detect, DetectorFactory
            DetectorFactory.seed = 0
            return detect(text)
        except Exception:
            return "en"

    def _edge_tts(self, text: str, lang: str, output_path: Path) -> Path:
        global _EDGE_CACHE
        locale = self.LANG_MAP[lang]

        if lang not in _EDGE_CACHE:
            async def _pick():
                import edge_tts
                voices = await edge_tts.list_voices()
                lang_prefix = locale.split("-")[0]
                matching = [v for v in voices if v["Locale"].startswith(lang_prefix)]
                if not matching:
                    raise ValueError(f"edge-tts 不支持 {lang}")
                _EDGE_CACHE[lang] = matching[0]["ShortName"]
            asyncio.run(_pick())

        voice = _EDGE_CACHE[lang]
        tmp = Path(tempfile.gettempdir()) / f"_auto_{lang}.mp3"
        try:
            async def _gen():
                import edge_tts
                await edge_tts.Communicate(text, voice).save(str(tmp))
            asyncio.run(_gen())
            if output_path.exists(): output_path.unlink(missing_ok=True)
            tmp.rename(output_path)
            return output_path
        finally:
            tmp.unlink(missing_ok=True)

    def _gtts(self, text: str, lang: str, output_path: Path) -> Path:
        from gtts import gTTS
        tts = gTTS(text=text, lang=lang, slow=False)
        tts.save(str(output_path))
        return output_path
