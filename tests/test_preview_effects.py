from core.edit_engine import SubtitleBlock, VideoClip
from core.preview_effects import (
    evaluate_subtitle_effects,
    evaluate_video_effects,
    subtitle_word_animation_state,
    subtitle_word_interval,
    subtitle_words,
)


def test_video_effects_share_keyframe_interpolation_and_sanitize_values():
    clip = VideoClip(
        timeline_start=2.0,
        trim_end=4.0,
        keyframes={
            "scale": [(0.0, 1.0), (2.0, 2.0)],
            "opacity": [(0.0, 1.0), (2.0, 0.0)],
            "rotation": [(0.0, 0.0), (2.0, 90.0)],
        },
    )
    values = evaluate_video_effects(clip, 3.0)
    assert values["scale"] == 1.5
    assert values["rotation"] == 45.0
    assert values["opacity"] == 0.5
    assert values["scale"] >= 0.01


def test_interactive_video_effects_skip_animation_without_mutating_model():
    clip = VideoClip(
        timeline_start=0.0,
        trim_end=2.0,
        pos_x=120.0,
        keyframes={"pos_x": [(0.0, -80.0), (2.0, 80.0)]},
    )
    values = evaluate_video_effects(clip, 1.0, interactive=True)
    assert values["pos_x"] == 120.0
    assert clip.pos_x == 120.0


def test_subtitle_effects_keep_position_fallback_and_word_timing():
    block = SubtitleBlock(
        text="你好世界",
        timeline_start=0.0,
        timeline_end=4.0,
        position="top",
        word_animation=True,
        word_anim_duration=0.2,
    )
    values = evaluate_subtitle_effects(block, 0.1)
    assert values["pos_x"] == 0.0
    assert values["pos_y"] == -0.85
    words = subtitle_words(block.text)
    assert words == ["你", "好", "世", "界"]
    word, progress, _ = subtitle_word_interval(block, 1.1, words)
    assert word == "好"
    assert 0.0 <= progress <= 1.0


def test_subtitle_word_state_accepts_asr_dictionary_timings():
    block = SubtitleBlock(
        text="hello world",
        timeline_start=0.0,
        timeline_end=2.0,
        word_animation=True,
        word_timings=[
            {"start": 0.0, "end": 0.7, "text": "hello"},
            {"start": 0.7, "end": 2.0, "text": "world"},
        ],
    )
    word, alpha, pop_scale = subtitle_word_animation_state(
        block, 0.8, subtitle_words(block.text))
    assert word == "world"
    assert 0.0 <= alpha <= 1.0
    assert 1.0 <= pop_scale <= 1.3

