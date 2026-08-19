"""文字粗剪的纯逻辑：口癖识别、重点选择与剪辑区间规划。"""
from __future__ import annotations

import re
from typing import Iterable


_FILLER_EXACT = {
    "嗯", "呃", "额", "啊", "哦", "唉", "这个", "那个", "然后", "然后呢",
    "就是说", "怎么说呢", "你知道吧", "对吧", "好吧", "ok", "okay",
    "um", "uh", "erm", "er", "ah", "hmm", "you know",
}
_FILLER_PARTS = (
    "嗯嗯", "呃呃", "额额", "那个那个", "就是就是", "然后然后",
    "um um", "uh uh", "you know you know",
)
_IMPORTANT_WORDS = (
    "重点", "关键", "结论", "原因", "结果", "但是", "所以", "必须", "千万",
    "注意", "记住", "第一", "最后", "最重要", "方法", "技巧", "真相", "不要",
    "为什么", "如何", "竟然", "居然", "免费", "省钱", "提升", "解决",
    "important", "key", "result", "because", "therefore", "however", "how", "why",
)


def _plain_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()\[\]{}…—-]+", "", text)
    return text


def is_filler_sentence(text: str) -> bool:
    """只移除独立口癖/极短废句，不误删含有效信息的长句。"""
    raw = (text or "").strip().lower()
    plain = _plain_text(raw)
    if not plain:
        return True
    if plain in {_plain_text(item) for item in _FILLER_EXACT}:
        return True
    if len(plain) <= 10 and any(_plain_text(item) in plain for item in _FILLER_PARTS):
        return True
    # 连续语气词，例如“嗯啊呃哦”。
    if len(plain) <= 6 and re.fullmatch(r"[嗯呃额啊哦唉哈]+", plain):
        return True
    return False


def choose_highlight_indices(segments: list[dict], target_seconds: float) -> list[int]:
    """无 API 时的本地重点选择；返回原列表索引并保持叙事顺序。"""
    if not segments:
        return []
    target_seconds = max(1.0, float(target_seconds or 0))
    scored = []
    first_valid = next((i for i, s in enumerate(segments)
                        if not is_filler_sentence(s.get("text", ""))), 0)
    for index, segment in enumerate(segments):
        text = (segment.get("text") or "").strip()
        if is_filler_sentence(text):
            continue
        duration = max(0.1, float(segment.get("end", 0)) - float(segment.get("start", 0)))
        score = min(len(_plain_text(text)), 50) / 30.0
        if index == first_valid:
            score += 2.4  # 保留开场钩子
        if any(word in text.lower() for word in _IMPORTANT_WORDS):
            score += 1.8
        if re.search(r"\d", text):
            score += 0.8
        if any(mark in text for mark in "!?！？"):
            score += 0.7
        # 信息密度高、不过长的句子更适合作为短视频片段。
        density = min(len(_plain_text(text)) / duration, 12.0) / 12.0
        score += density
        scored.append((score, index, duration))

    selected = []
    total = 0.0
    for _score, index, duration in sorted(scored, reverse=True):
        selected.append(index)
        total += duration
        if total >= target_seconds:
            break
    return sorted(selected)


def build_cut_plan(
    selected: Iterable[dict], *, source_offset: float, trim_start: float,
    trim_end: float, timeline_start: float, speed: float = 1.0,
    padding: float = 0.15, compact: bool = True,
) -> tuple[list[dict], list[dict]]:
    """把勾选的时间线字幕转换为源视频裁剪区间和压缩后的字幕时间。

    source_offset 满足 ``timeline_time = source_time + source_offset``。
    返回 ``(ranges, subtitles)``，ranges 含 source_start/source_end/timeline_start。
    """
    speed = max(0.01, float(speed or 1.0))
    padding = max(0.0, float(padding or 0.0))
    rows = []
    for sub in selected:
        tl_start = float(sub.get("start", sub.get("timeline_start", 0.0)))
        tl_end = float(sub.get("end", sub.get("timeline_end", tl_start)))
        source_start = sub.get("source_start")
        source_end = sub.get("source_end")
        raw_start = (float(source_start) if source_start is not None else
                     trim_start + (tl_start - timeline_start) * speed)
        raw_end = (float(source_end) if source_end is not None else
                   trim_start + (tl_end - timeline_start) * speed)
        raw_start = max(trim_start, min(trim_end, raw_start))
        raw_end = max(raw_start, min(trim_end, raw_end))
        if raw_end - raw_start < 0.03:
            continue
        rows.append({
            "source_start": raw_start,
            "source_end": raw_end,
            "text": sub.get("text", ""),
            "original": sub,
        })
    rows.sort(key=lambda item: item["source_start"])
    if not rows:
        return [], []

    # 先给每句增加安全边距，再合并重叠区间；句子间较长静音自然被删除。
    ranges: list[dict] = []
    for row in rows:
        start = max(trim_start, row["source_start"] - padding)
        end = min(trim_end, row["source_end"] + padding)
        if ranges and start <= ranges[-1]["source_end"] + 0.02:
            ranges[-1]["source_end"] = max(ranges[-1]["source_end"], end)
        else:
            ranges.append({"source_start": start, "source_end": end})

    cursor = float(timeline_start)
    for item in ranges:
        if compact:
            item["timeline_start"] = cursor
            cursor += (item["source_end"] - item["source_start"]) / speed
        else:
            item["timeline_start"] = (
                timeline_start + (item["source_start"] - trim_start) / speed
            )

    remapped = []
    for row in rows:
        host = next((item for item in ranges
                     if item["source_start"] <= row["source_start"] + 1e-6
                     and item["source_end"] >= row["source_end"] - 1e-6), None)
        if host is None:
            continue
        start = host["timeline_start"] + (row["source_start"] - host["source_start"]) / speed
        end = host["timeline_start"] + (row["source_end"] - host["source_start"]) / speed
        clean = {key: value for key, value in row["original"].items()
                 if not key.startswith("_") and key not in {"source_start", "source_end"}}
        clean.update({"start": start, "end": max(start + 0.03, end), "text": row["text"]})
        remapped.append(clean)
    return ranges, remapped
