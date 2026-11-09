"""Shared, deterministic effect evaluation for preview and interaction paths.

The editor has several render entry points (normal playback, overlay refresh,
and direct-manipulation feedback).  Keeping the time-dependent part here means
all of them agree on the exact transform/opacity/subtitle state for a frame.
This module deliberately has no Qt dependency so it is cheap to exercise in
unit tests and safe to call from the decoder worker.
"""
from __future__ import annotations

import math
import re
from typing import Any


_CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]|"
    r"[^\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\s]+|\S"
)


def _number(value: Any, default: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) else float(default)


def _base_values(obj: Any, names: tuple[str, ...], defaults: dict[str, float]) -> dict[str, float]:
    return {name: _number(getattr(obj, name, defaults[name]), defaults[name])
            for name in names}


def evaluate_keyframed_values(obj: Any, rel_time: float,
                              base: dict[str, float],
                              *, skip: bool = False) -> dict[str, float]:
    """Evaluate an object's keyframes without mutating the model.

    ``interpolate_keyframes`` remains the canonical interpolation algorithm;
    this wrapper adds input sanitation and a stable fallback for old projects.
    """
    values = dict(base)
    if skip or not getattr(obj, "keyframes", None):
        return values
    try:
        from core.edit_engine import interpolate_keyframes
        result = interpolate_keyframes(
            obj, getattr(obj, "keyframes", {}) or {},
            _number(rel_time, 0.0), values)
        if isinstance(result, dict):
            for key in values:
                values[key] = _number(result.get(key, values[key]), values[key])
    except Exception:
        # Preview must never fail just because an old hand-edited project has
        # malformed keyframes.
        return values
    return values


def evaluate_video_effects(clip: Any, timeline_sec: float,
                           *, interactive: bool = False) -> dict[str, float]:
    """Return the complete time-dependent state used by video rendering."""
    start = _number(getattr(clip, "timeline_start", 0.0), 0.0)
    base = _base_values(
        clip,
        ("pos_x", "pos_y", "scale", "rotation", "blur_radius", "opacity"),
        {"pos_x": 0.0, "pos_y": 0.0, "scale": 1.0, "rotation": 0.0,
         "blur_radius": 0.0, "opacity": 1.0},
    )
    values = evaluate_keyframed_values(
        clip, _number(timeline_sec, 0.0) - start, base, skip=interactive)
    values["scale"] = max(0.01, values["scale"])
    values["blur_radius"] = max(0.0, values["blur_radius"])
    values["opacity"] = max(0.0, min(1.0, values["opacity"]))
    return values


def evaluate_subtitle_effects(block: Any, timeline_sec: float,
                              *, interactive: bool = False) -> dict[str, float]:
    """Return subtitle position, scale, rotation, font size and opacity."""
    start = _number(getattr(block, "timeline_start", 0.0), 0.0)
    position = getattr(block, "position", "bottom") or "bottom"
    pos_map = {"top": -0.85, "center": 0.0, "bottom": 0.85}
    fallback_x = 0.0 if getattr(block, "pos_x", None) is None else getattr(block, "pos_x")
    fallback_y = pos_map.get(position, 0.85) if getattr(block, "pos_y", None) is None else getattr(block, "pos_y")
    base = _base_values(
        block,
        ("pos_x", "pos_y", "scale", "rotation", "font_size", "opacity"),
        {"pos_x": _number(fallback_x, 0.0),
         "pos_y": _number(fallback_y, 0.85), "scale": 1.0,
         "rotation": 0.0, "font_size": 15.0, "opacity": 1.0},
    )
    values = evaluate_keyframed_values(
        block, _number(timeline_sec, 0.0) - start, base, skip=interactive)
    values["scale"] = max(0.01, values["scale"])
    values["font_size"] = max(1.0, values["font_size"])
    values["opacity"] = max(0.0, min(1.0, values["opacity"]))
    return values


def subtitle_words(text: str) -> list[str]:
    """Split Latin text by words and CJK text by visible characters."""
    words = (text or "").split()
    if len(words) <= 1:
        chars = _CJK_RE.findall(text or "")
        if len(chars) > 1:
            words = chars
    return words


def _word_timing_intervals(block: Any, words: list[str]):
    """Normalize legacy numeric timings and newer ASR timing dictionaries."""
    start = _number(getattr(block, "timeline_start", 0.0), 0.0)
    end = _number(getattr(block, "timeline_end", start), start)
    duration = max(0.001, end - start)
    raw = getattr(block, "word_timings", None) or []
    if len(raw) == len(words) and all(isinstance(item, dict) for item in raw):
        result = []
        for index, item in enumerate(raw):
            item_start = _number(item.get("start", 0.0), 0.0)
            item_end = _number(item.get("end", duration), duration)
            if item_end <= item_start:
                item_end = duration if index == len(raw) - 1 else item_start + 0.001
            label = str(item.get("text", "") or words[index])
            result.append((item_start, item_end, label))
        return result
    if len(raw) == len(words):
        starts = [_number(value, 0.0) for value in raw]
        return [(starts[index], starts[index + 1] if index + 1 < len(starts) else duration,
                 words[index]) for index in range(len(words))]
    step = duration / len(words)
    return [(index * step, (index + 1) * step, word)
            for index, word in enumerate(words)]


def subtitle_word_interval(block: Any, timeline_sec: float,
                           words: list[str]) -> tuple[str, float, float]:
    """Return active word and normalized fade progress for a subtitle frame."""
    if not words:
        return "", 0.0, 0.0
    start = _number(getattr(block, "timeline_start", 0.0), 0.0)
    rel = _number(timeline_sec, start) - start
    for item_start, item_end, word in _word_timing_intervals(block, words):
        if item_start <= rel < item_end:
            fade = max(0.001, _number(getattr(block, "word_anim_duration", 0.15), 0.15))
            progress = max(0.0, min(1.0, (rel - item_start) / fade))
            return word, progress, rel
    return "", 0.0, rel


def subtitle_word_animation_state(block: Any, timeline_sec: float,
                                  words: list[str]) -> tuple[str, float, float]:
    """Return word, fade alpha and shared pop scale for one subtitle frame."""
    word, alpha, rel = subtitle_word_interval(block, timeline_sec, words)
    pop_scale = 1.0 + 0.3 * (1.0 - abs(alpha - 0.5) * 2.0)
    return word, alpha, pop_scale


def effect_signature(obj: Any, timeline_sec: float, *, interactive: bool = False) -> tuple:
    """Stable compact cache key for a renderable effect state."""
    values = (evaluate_video_effects if hasattr(obj, "source_path")
              else evaluate_subtitle_effects)(obj, timeline_sec, interactive=interactive)
    return (id(obj), *(round(values[key], 4) for key in sorted(values)))

