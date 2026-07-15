"""
GlobalFlux AI - AI 爆款黄金脚本生成器
核心：Hook 库随机组合 + LLM 润色 + 批量生成 + SRT 输出 → 可直接进 TTS 流水线
"""
import json
import re
import random
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List

HOOK_DB_PATH = Path(__file__).parent.parent / "hooks" / "hook_db.json"
TEMPLATE_DIR = Path(__file__).parent.parent / "hooks" / "templates"


@dataclass
class GeneratedScript:
    """单条生成的脚本"""
    text: str                          # 完整脚本文本
    srt_entries: list                  # SRT 条目列表，可直进 TTS
    hook_type: str                     # 使用的 Hook 类型
    template_name: str                 # 使用的模板名
    tone: str
    length_sec: int
    target_lang: str
    srt_path: Optional[Path] = None    # 保存后的 SRT 文件路径


class ScriptGenerator:
    """AI 爆款脚本生成器"""

    # 支持的目标语言
    LANG_MAP = {
        "en": "English (美式口语)",
        "th": "Thai (泰语)",
        "vi": "Vietnamese (越南语)",
        "pt": "Portuguese (巴西葡语)",
        "es": "Spanish (拉美西语)",
        "id": "Indonesian (印尼语)",
        "ms": "Malay (马来语)",
        "fil": "Filipino (菲律宾语)",
        "ar": "Arabic (阿拉伯语)",
        "ja": "Japanese (日语)",
        "ko": "Korean (韩语)",
    }

    def __init__(self, hook_db_path: Path = None):
        path = hook_db_path or HOOK_DB_PATH
        with open(path, encoding="utf-8") as f:
            self.db = json.load(f)
        self.hooks = self.db.get("hooks", {})
        self.ctas = self.db.get("ctas", [])
        # 加载脚本模板
        self.templates = self._load_templates()

    def _load_templates(self) -> dict:
        """加载脚本模板"""
        templates = {}
        if not TEMPLATE_DIR.exists():
            return templates
        for f in TEMPLATE_DIR.glob("*.json"):
            with open(f, encoding="utf-8") as fh:
                t = json.load(fh)
                templates[f.stem] = t
        return templates

    def list_hook_types(self) -> List[str]:
        """列出所有 Hook 类型"""
        return list(self.hooks.keys())

    def list_templates(self) -> List[str]:
        """列出所有脚本模板"""
        return list(self.templates.keys())

    def batch_generate(
        self,
        product_info: str,
        tone: str = "passionate",
        length_sec: int = 30,
        target_lang: str = "en",
        count: int = 1,
        hook_types: Optional[List[str]] = None,
        template_name: Optional[str] = None,
    ) -> List[GeneratedScript]:
        """
        批量生成脚本，每次随机组合 Hook + CTA，LLM 润色。

        Args:
            product_info: 产品描述
            tone: 风格 (passionate / calm / humorous / urgent / luxurious)
            length_sec: 脚本时长(秒)
            target_lang: 目标语言
            count: 生成数量
            hook_types: 指定 Hook 类型列表，None 则随机
            template_name: 指定脚本模板，None 则按时长自动选

        Returns:
            GeneratedScript 列表
        """
        # 选择模板
        tmpl = self._pick_template(template_name, length_sec)

        scripts = []
        for i in range(count):
            script = self._generate_one(
                product_info=product_info,
                tone=tone,
                length_sec=length_sec,
                target_lang=target_lang,
                hook_types=hook_types,
                template=tmpl,
            )
            scripts.append(script)
        return scripts

    def _pick_template(self, template_name: Optional[str], length_sec: int) -> Optional[dict]:
        """按名称或时长选模板"""
        if template_name and template_name in self.templates:
            return self.templates[template_name]
        # 按时长自动匹配
        if length_sec <= 15:
            return self.templates.get("15s_flash")
        elif length_sec <= 30:
            return self.templates.get("30s_standard")
        else:
            return self.templates.get("60s_story")

    def _generate_one(
        self,
        product_info: str,
        tone: str,
        length_sec: int,
        target_lang: str,
        hook_types: Optional[List[str]],
        template: Optional[dict],
    ) -> GeneratedScript:
        # 1. 随机选取 Hook
        pool = hook_types if hook_types else list(self.hooks.keys())
        hook_type = random.choice(pool)
        hook_template = random.choice(self.hooks[hook_type])
        cta = random.choice(self.ctas)

        # 2. 构建 LLM prompt
        prompt = self._build_prompt(
            product_info, hook_template, cta, tone, length_sec, target_lang, template
        )

        # 3. 调用 LLM
        script_text = self._call_llm(prompt, target_lang)

        # 4. 解析为 SRT 条目
        srt_entries = self._parse_to_srt(script_text, length_sec)

        return GeneratedScript(
            text=script_text,
            srt_entries=srt_entries,
            hook_type=hook_type,
            template_name=template.get("name", "") if template else "",
            tone=tone,
            length_sec=length_sec,
            target_lang=target_lang,
        )

    def _build_prompt(
        self,
        product_info: str,
        hook: str,
        cta: str,
        tone: str,
        length_sec: int,
        lang: str,
        template: Optional[dict],
    ) -> str:
        lang_name = self.LANG_MAP.get(lang, lang)

        # 构建结构约束
        structure_hint = ""
        if template and "structure" in template:
            parts = []
            for seg in template["structure"]:
                parts.append(f"  [{seg['start']:02d}s-{seg['end']:02d}s] {seg['phase'].upper()}: {seg['instruction']}")
            structure_hint = "\n结构要求（严格遵守时间段分配）：\n" + "\n".join(parts)

        tone_map = {
            "passionate": "激情带货，情绪饱满，语速偏快",
            "calm": "沉稳专业，理性说服，信任感",
            "humorous": "幽默搞笑，轻松有趣，降低防备",
            "urgent": "紧迫催促，限时限量，催转化",
            "luxurious": "高端奢华，品质感，稀缺性",
        }
        tone_desc = tone_map.get(tone, tone)

        return f"""你是海外短视频投流脚本专家。请按以下要求生成一段 {length_sec} 秒的配音脚本：

产品信息：{product_info}
开篇 Hook（必须使用，可微调适配产品）：{hook}
结尾 CTA（必须使用，可微调适配产品）：{cta}
风格：{tone_desc}
目标语言：{lang_name}
{structure_hint}
严格要求：
1. 严格控制在 {length_sec} 秒内（语速约 2.5 词/秒英文，3 字/秒中文）
2. 前 3-5 秒必须用 Hook 抓住注意力，不允许慢热
3. 每句话一行，标注时间戳 [MM:SS-MM:SS]
4. 纯配音文字，不含场景描述、镜头指示、音乐提示
5. 口语化，适合 TTS 朗读，避免书面语和长句
6. 如使用变量占位符(如{{pain_point}})，请替换为产品相关的具体内容"""

    def _call_llm(self, prompt: str, lang: str) -> str:
        """调用 LLM 生成脚本"""
        from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME

        if not LLM_API_KEY:
            return self._mock_script(lang)

        import openai
        client = openai.OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL or None,
            timeout=45.0,
        )
        resp = client.chat.completions.create(
            model=LLM_MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        return resp.choices[0].message.content

    def _parse_to_srt(self, script_text: str, total_sec: int) -> list:
        """
        将脚本文本解析为带时间戳的 SRT 条目列表。
        格式: [{"index": 1, "start": 0.0, "end": 3.0, "text": "..."}]
        """
        entries = []
        lines = [l.strip() for l in script_text.split("\n") if l.strip()]

        for line in lines:
            # 尝试提取 [MM:SS-MM:SS] 或 [M:SS-M:SS] 时间戳
            m = re.match(r'\[(\d{1,2}:\d{2})-(\d{1,2}:\d{2})\]\s*(.*)', line)
            if m:
                start = self._timestamp_to_sec(m.group(1))
                end = self._timestamp_to_sec(m.group(2))
                text = m.group(3).strip()
                if text:
                    entries.append({
                        "index": len(entries) + 1,
                        "start": start,
                        "end": end,
                        "text": text,
                    })
            else:
                # 去掉可能的其他时间标记
                clean = re.sub(r'\[.*?\]\s*', '', line).strip()
                if not clean:
                    continue
                # 无时间戳，均匀分配
                entries.append({
                    "index": len(entries) + 1,
                    "start": 0,
                    "end": 0,
                    "text": clean,
                })

        # 对没有时间戳的条目，均匀分配
        no_ts = [e for e in entries if e["start"] == 0 and e["end"] == 0]
        has_ts = [e for e in entries if e["start"] > 0 or e["end"] > 0]

        if no_ts and not has_ts:
            # 全部无时间戳，均匀分配
            per_line = total_sec / max(len(no_ts), 1)
            for i, e in enumerate(no_ts):
                e["start"] = round(i * per_line, 2)
                e["end"] = round((i + 1) * per_line, 2)
        elif no_ts:
            # 部分有时间戳，部分没有 → 插入到间隙
            has_ts.sort(key=lambda x: x["start"])
            # 简单策略：未标注的条目均分到已有条目之间的间隙
            idx = 0
            for e in entries:
                if e["start"] == 0 and e["end"] == 0:
                    # 找前后最近的有时间戳条目
                    prev_ts = next(
                        (h for h in reversed(has_ts) if h["index"] < e["index"]),
                        has_ts[0] if has_ts else None,
                    )
                    next_ts = next(
                        (h for h in has_ts if h["index"] > e["index"]),
                        has_ts[-1] if has_ts else None,
                    )
                    if prev_ts and next_ts:
                        gap = next_ts["start"] - prev_ts["end"]
                        e["start"] = round(prev_ts["end"] + idx * 0.5, 2)
                        e["end"] = round(e["start"] + max(gap / max(len(no_ts), 1), 1), 2)
                    idx += 1

        # 重新编号
        for i, e in enumerate(entries):
            e["index"] = i + 1

        return entries

    @staticmethod
    def _timestamp_to_sec(ts: str) -> float:
        """将 M:SS 或 MM:SS 转为秒数"""
        parts = ts.split(":")
        return int(parts[0]) * 60 + int(parts[1])

    def _mock_script(self, lang: str) -> str:
        """无 API Key 时的模拟输出"""
        if lang in ("zh", "cn"):
            return (
                "[00:00-00:03] 别划走！这个真的能改变一切。\n"
                "[00:03-00:08] 还在为效率低头疼？试过无数方法都没用？\n"
                "[00:08-00:16] 直到用了它，一天搞定一周的活。万人验证，好评如潮。\n"
                "[00:16-00:22] 操作简单，效果说话，第1天vs第30天你信吗？\n"
                "[00:22-00:27] 限时特惠，库存告急！\n"
                "[00:27-00:30] 小黄车，手慢无！"
            )
        return (
            "[00:00-00:03] Stop scrolling! This changed everything.\n"
            "[00:03-00:08] Still struggling with the same old problem? I was too.\n"
            "[00:08-00:16] Over 10,000 people already switched. Day 1 vs Day 30 — the results speak.\n"
            "[00:16-00:22] Simple, effective, and actually affordable.\n"
            "[00:22-00:27] Limited time offer — they're selling out fast.\n"
            "[00:27-00:30] Link in bio — get yours now!"
        )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python script_gen.py <产品描述> [语言] [时长] [数量]")
        print("  python script_gen.py '便携榨汁杯' en 30 3")
        sys.exit(1)

    product = sys.argv[1]
    lang = sys.argv[2] if len(sys.argv) > 2 else "en"
    duration = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    count = int(sys.argv[4]) if len(sys.argv) > 4 else 1

    gen = ScriptGenerator()
    scripts = gen.batch_generate(
        product_info=product,
        target_lang=lang,
        length_sec=duration,
        count=count,
    )
    for i, s in enumerate(scripts, 1):
        print(f"\n{'='*50}")
        print(f"脚本 #{i} | Hook: {s.hook_type} | 模板: {s.template_name}")
        print(f"{'='*50}")
        print(s.text)
        if s.srt_entries:
            print(f"\nSRT 条目数: {len(s.srt_entries)}")
