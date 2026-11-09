"""Local planner boundaries plus actual desktop-engine short video exports."""
import json
import os
from pathlib import Path
import subprocess
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from ai import canvas_agent_editing as editing
from ai.providers.base import ProviderRegistry, TaskHandle, TaskResult, TaskStatus
from core.edit_engine import EditTimeline
from utils.ffmpeg_utils import get_ffmpeg_path


APP = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("semantic-edit-sources")
    paths = []
    for index, color in enumerate(("red", "blue")):
        path = folder / f"素材 {index}.mp4"
        subprocess.run([get_ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c={color}:s=320x180:r=12:d=1.5", "-f", "lavfi", "-i",
                        f"sine=frequency={440 + index * 220}:sample_rate=48000:duration=1.5",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                       check=True, capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        paths.append({"asset_id": f"v{index}", "kind": "video", "local_path": str(path)})
    return paths


def _frame(path, second, width=320, height=180):
    data = subprocess.run([get_ffmpeg_path(), "-v", "error", "-ss", str(second), "-i", str(path),
                           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                          check=True, capture_output=True, timeout=30).stdout
    return np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)


def _project(results):
    assert len(results) == 2 and results[1]["kind"] == "project"
    assert results[1]["editable"] and results[1]["path"] == results[0]["timeline_path"]
    from core.project_files import VID_SUFFIX, read_project

    data = read_project(results[1]["path"], VID_SUFFIX)
    return data, EditTimeline.from_dict(data["timelines"][0])


def test_real_export_sequence_duration_mute_and_editable_project(media, tmp_path):
    events = []
    results = editing.run_timeline_edit(
        "按连接顺序拼接，每段一秒，总时长1.5秒，静音", media,
        {"resolution": (320, 180), "fps": 12}, tmp_path,
        progress=lambda percent, message: events.append((percent, message)))
    movie = Path(results[0]["path"])
    info = editing._probe(movie, None)
    assert info["duration"] == pytest.approx(1.5, abs=.15)
    assert not info["has_audio"]
    assert _frame(movie, .25)[:, :, 0].mean() > 200
    assert _frame(movie, 1.25)[:, :, 2].mean() > 200
    _, timeline = _project(results)
    assert timeline.total_duration == pytest.approx(1.5)
    assert [c.duration for c in timeline.video_clips] == [1, .5]
    assert all(c.mute for c in timeline.video_clips)
    assert timeline.video_clips[0].source_path == media[0]["local_path"]
    assert [v for v, _ in events] == sorted(v for v, _ in events)
    assert events[-1][0] == 100
    assert sorted(p.suffix for p in tmp_path.iterdir()) == [".mp4", ".xiaohuanvid"]


def test_real_export_subtitles_effects_and_original_audio(media, tmp_path):
    results = editing.run_timeline_edit(
        '第一段0.5秒，第二段0.75秒，保留原声，添加字幕：“Hello”，缩放80%，模糊2',
        media, {"resolution": (320, 180), "fps": 12}, tmp_path)
    movie = Path(results[0]["path"])
    assert editing._probe(movie, None)["has_audio"]
    _, timeline = _project(results)
    assert [c.duration for c in timeline.video_clips] == [.5, .75]
    assert all(not c.mute and c.scale == .8 and c.blur_radius == 2 for c in timeline.video_clips)
    assert timeline.subtitle_blocks[0].text == "Hello"
    assert timeline.subtitle_blocks[0].timeline_end == 1.25
    picture = _frame(movie, .25)
    # Desktop engine actually applies the reduced scale and burns white text.
    assert picture[0:8].mean() < 12
    assert ((picture[:, :, 0] > 180) & (picture[:, :, 1] > 180) & (picture[:, :, 2] > 180)).sum() > 10


@pytest.mark.parametrize("resolution,ratio,expected", [
    ("480p", "16:9", (854, 480)), ("720p", "16:9", (1280, 720)),
    ("1080p", "16:9", (1920, 1080)), ("1080p", "9:16", (1080, 1920)),
    ("720p", "1:1", (720, 720)), ("480p", "adaptive", (854, 480)),
    ((320, 180), "9:16", (320, 180)),
])
def test_agent_resolution_contract(resolution, ratio, expected):
    assert editing._output_size({"resolution": resolution, "ratio": ratio}, {"width": 320, "height": 180}) == expected


def test_real_export_agent_string_resolution(media, tmp_path):
    results = editing.run_timeline_edit("每段0.25秒，静音", media,
                                         {"resolution": "480p", "ratio": "1:1", "fps": 12}, tmp_path)
    assert (results[0]["width"], results[0]["height"]) == (480, 480)
    data, _ = _project(results)
    assert data["canvas_custom_size"] == [480, 480]


class _Provider:
    name = "openai"
    capabilities = ["chat", "json"]

    def __init__(self, vision=True):
        self.config = {"supports_vision": vision}

    def supports(self, operation):
        return operation in self.capabilities


class _Manager:
    def __init__(self, response=None, provider=None, pending=False, error=""):
        self.registry = ProviderRegistry()
        if provider is not None:
            self.registry.register(provider)
        self.response, self.pending, self.error = response, pending, error
        self.calls = []

    def submit(self, provider, request):
        self.calls.append((provider, request))
        self.handle = TaskHandle("edit-plan", provider, request.operation,
                                 status=TaskStatus.RUNNING if self.pending else TaskStatus.FAILED if self.error else TaskStatus.DONE,
                                 result=TaskResult(not self.error, self.response, error=self.error))
        return self.handle


@pytest.mark.parametrize("provider", [None, _Provider(vision=False)])
def test_unsupported_visual_semantics_never_fake_completion(media, tmp_path, provider):
    manager = _Manager(provider=provider)
    with pytest.raises(editing.UnsupportedSemanticEdit, match="视觉模型不可用"):
        editing.run_timeline_edit("识别指定logo画面并删除", media, {}, tmp_path, manager=manager)
    assert not manager.calls
    assert not list(tmp_path.iterdir())


def test_control_plane_provider_is_excluded_before_submit(media, tmp_path):
    # Even inherited proxy providers or explicit selection cannot route to web.
    Proxy = type("ControlPlaneProvider", (_Provider,), {"__module__": "ai.providers.control_plane"})
    manager = _Manager(provider=Proxy())
    with pytest.raises(editing.UnsupportedSemanticEdit, match="视觉模型不可用"):
        editing.run_timeline_edit("删除有logo的画面", media,
                                 {"provider": "openai", "model": "gpt-5.5"}, tmp_path, manager=manager)
    assert not manager.calls


def _sources():
    return [{"asset_id": "v0", "kind": "video", "duration": 2}]


@pytest.mark.parametrize("row", [
    {"asset_id": "other", "trim_end": 1},
    {"asset_id": "v0", "trim_start": -1, "trim_end": 1},
    {"asset_id": "v0", "trim_end": 2.001},
    {"asset_id": "v0", "trim_end": float("nan")},
    {"asset_id": "v0", "trim_end": float("inf")},
    {"asset_id": "v0", "trim_end": True},
    {"asset_id": "v0", "trim_start": 1, "trim_end": 1},
    {"asset_id": "v0", "trim_end": 1, "timeline_start": 1},
    {"asset_id": "v0", "trim_end": 1, "source_path": "C:/unconnected.mp4"},
    {"asset_id": "v0", "trim_end": 1, "mute": "false"},
    {"asset_id": "v0", "trim_end": 1, "effects": {"speed": 0}},
    {"asset_id": "v0", "trim_end": 1, "effects": {"shell": "something"}},
])
def test_strict_plan_rejects_invalid_ranges_sources_and_effects(row):
    with pytest.raises(ValueError):
        editing._validate_plan({"clips": [row]}, _sources())


def test_strict_subtitles_and_total_duration():
    plan = {"clips": [{"asset_id": "v0", "trim_end": 1}],
            "subtitles": [{"text": "Hi", "timeline_start": 0, "timeline_end": 1.1}]}
    with pytest.raises(ValueError):
        editing._validate_plan(plan, _sources())
    with pytest.raises(ValueError, match="不能凭空补齐"):
        editing._validate_plan({"clips": [{"asset_id": "v0", "trim_end": 1}]}, _sources(), 2)


def test_references_are_probed_not_trusted(media, tmp_path):
    with pytest.raises(ValueError, match="trim_end"):
        editing.run_timeline_edit("每段10秒", [{**media[0], "duration": 100}], {}, tmp_path)
    with pytest.raises(ValueError, match="本地文件"):
        editing.run_timeline_edit("拼接", ["https://example.com/video.mp4"], {}, tmp_path)


def test_cancel_before_work_and_during_render_cleans_partial_files(media, tmp_path):
    with pytest.raises(editing.TimelineEditCancelled):
        editing.run_timeline_edit("拼接", media, {}, tmp_path, cancel_check=lambda: True)
    cancelled = False

    def progress(percent, _message):
        nonlocal cancelled
        if percent >= 40:
            cancelled = True

    with pytest.raises(editing.TimelineEditCancelled):
        editing.run_timeline_edit("每段1秒", media, {"resolution": (320, 180)}, tmp_path,
                                 progress=progress, cancel_check=lambda: cancelled)
    assert not list(tmp_path.iterdir())


def test_export_failure_is_not_success_and_cleans_files(media, tmp_path, monkeypatch):
    def fail(_timeline, path, *_args):
        path.write_bytes(b"partial")
        raise editing.TimelineEditError("test encoder failure")
    monkeypatch.setattr(editing, "_export", fail)
    with pytest.raises(editing.TimelineEditError, match="encoder failure"):
        editing.run_timeline_edit("拼接", media, {}, tmp_path)
    assert not list(tmp_path.iterdir())


def test_cancel_pending_provider_and_failure(media, tmp_path):
    manager = _Manager(provider=_Provider(), pending=True)
    with pytest.raises(editing.TimelineEditCancelled):
        editing.run_timeline_edit("拼接", media, {"planner": "llm"}, tmp_path,
                                 cancel_check=lambda: bool(manager.calls), manager=manager)
    assert manager.handle.status == TaskStatus.CANCELLED
    manager = _Manager(provider=_Provider(), error="provider offline")
    with pytest.raises(editing.TimelineEditError, match="provider offline"):
        editing.run_timeline_edit("拼接", media, {"planner": "llm"}, tmp_path, manager=manager)
    assert not list(tmp_path.iterdir())


def test_vision_plan_uses_actual_screenshots_and_direct_provider(media, tmp_path):
    plan = {"clips": [{"asset_id": "v0", "trim_start": .5, "trim_end": 1.5}],
            "visual_evidence": [{"asset_id": "v0", "time": 0, "description": "opening logo"}]}
    manager = _Manager(json.dumps(plan), _Provider())
    results = editing.run_timeline_edit("识别logo画面并删除", media[:1],
                                         {"model": "test-vision", "resolution": (320, 180), "fps": 12},
                                         tmp_path, manager=manager)
    _, request = manager.calls[0]
    assert request.params["model"] == "test-vision"
    assert request.params["response_format"] == {"type": "json_object"}
    assert not request.use_cache
    images = [r for r in request.inputs["messages"][1]["content"] if r["type"] == "image_url"]
    assert len(images) == 3 and images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert results[0]["duration"] == 1
    assert results[0]["semantic_review"]["visual_sampling_interval"] == .5


@pytest.mark.parametrize("response,pattern", [
    ('{"clips":[{"asset_id":"v0","trim_end":1}]}', "截图证据"),
    ('```json\n{}\n```', "严格 JSON"),
    ('{"error":"无法确认logo位置"}', "无法确认"),
    ({"clips": [{"asset_id": "v0", "trim_end": 1}], "visual_evidence": [
        {"asset_id": "v0", "time": .1, "description": "not sampled"}]}, "未提供的截图"),
    ({"clips": [{"asset_id": "v0", "trim_end": 1.5}], "visual_evidence": [
        {"asset_id": "v0", "time": 0, "description": "logo"}]}, "没有删除"),
])
def test_vision_refuses_unverified_or_noop_plans(media, tmp_path, response, pattern):
    manager = _Manager(response, _Provider())
    with pytest.raises((ValueError, editing.TimelineEditError), match=pattern):
        editing.run_timeline_edit("删除logo画面", media[:1], {}, tmp_path, manager=manager)
    assert not list(tmp_path.iterdir())


def test_default_manager_never_initializes_web_registry(monkeypatch):
    import config
    import ai.service
    monkeypatch.setattr(ai.service, "_manager", None)
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(ai.service, "get_ai_manager", lambda: pytest.fail("web registry called"))
    with pytest.raises(editing.UnsupportedSemanticEdit, match="直连"):
        editing._default_manager()


def test_model_defaults_come_from_existing_configuration(monkeypatch):
    import config
    monkeypatch.setattr(config, "LLM_MODEL_NAME", "configured-model")
    _, model = editing._select_provider(_Manager(provider=_Provider()), {}, False)
    assert model == "configured-model"


def test_sampling_budget_refuses_incomplete_coverage(media, tmp_path):
    manager = _Manager(provider=_Provider())
    with pytest.raises(editing.UnsupportedSemanticEdit, match="超过上限"):
        editing.run_timeline_edit("删除logo画面", media, {"max_visual_samples": 1}, tmp_path, manager=manager)
    assert not manager.calls
    assert not list(tmp_path.iterdir())


def test_splitting_or_reordering_original_coverage_is_not_removal():
    original, _ = editing._validate_plan({"clips": [{"asset_id": "v0", "trim_end": 2}]}, _sources())
    split, _ = editing._validate_plan({"clips": [
        {"asset_id": "v0", "trim_start": 1, "trim_end": 2},
        {"asset_id": "v0", "trim_start": 0, "trim_end": 1}]}, _sources())
    assert not editing._has_removed_range(original, split)
    removed, _ = editing._validate_plan({"clips": [{"asset_id": "v0", "trim_end": 1.9}]}, _sources())
    assert editing._has_removed_range(original, removed)


def test_llm_cannot_drop_known_audio_effect_caption_requirements(media, tmp_path):
    manager = _Manager({"clips": [{"asset_id": "v0", "trim_end": .5, "mute": False,
                                   "effects": {"scale": 1}}]}, _Provider())
    results = editing.run_timeline_edit('每段0.5秒，静音，缩放80%，字幕：“Exact caption”', media[:1],
                                         {"planner": "llm", "resolution": (320, 180), "fps": 12},
                                         tmp_path, manager=manager)
    _, timeline = _project(results)
    assert timeline.video_clips[0].mute and timeline.video_clips[0].scale == .8
    assert timeline.subtitle_blocks[0].text == "Exact caption"


def test_llm_cannot_ignore_requested_segment_duration(media, tmp_path):
    manager = _Manager({"clips": [{"asset_id": "v0", "trim_end": 1.5}]}, _Provider())
    with pytest.raises(ValueError, match="每段指定时长"):
        editing.run_timeline_edit("每段0.5秒", media[:1], {"planner": "llm"}, tmp_path, manager=manager)
    assert not list(tmp_path.iterdir())


def test_duration_is_output_seconds_when_speed_is_requested():
    videos = [{"asset_id": "v0", "duration": 2}]
    contract, unknown, _ = editing._contract("每段0.5秒", {"effects": {"speed": 2}}, videos)
    assert not unknown
    plan, duration = editing._validate_plan(contract, _sources())
    assert plan["clips"][0]["trim_end"] == 1
    assert duration == .5


def test_owned_default_manager_registers_only_direct_llm(monkeypatch):
    import config
    import ai.service
    monkeypatch.setattr(ai.service, "_manager", None)
    monkeypatch.setattr(config, "LLM_API_KEY", "test-no-request")
    monkeypatch.setattr(config, "LLM_MODE", "openai")
    monkeypatch.setattr(ai.service, "_build_registry", lambda: pytest.fail("web registry called"))
    manager, owned = editing._default_manager()
    try:
        assert owned
        assert len(manager.registry.list_all()) == 1
        assert type(manager.registry.get("openai")).__module__ == "ai.providers.llm.openai"
    finally:
        manager.stop()


def test_real_export_cancel_after_renderer_progress(media, tmp_path):
    cancelled = False

    def progress(percent, _message):
        nonlocal cancelled
        if percent >= 48 and percent < 100:
            cancelled = True

    # Higher frame count keeps the real worker running across progress polling.
    with pytest.raises(editing.TimelineEditCancelled):
        editing.run_timeline_edit("静音，淡入淡出", media,
                                 {"resolution": (640, 360), "fps": 60}, tmp_path,
                                 progress=progress, cancel_check=lambda: cancelled)
    assert cancelled and not list(tmp_path.iterdir())


@pytest.fixture(scope="module")
def generated_audio(tmp_path_factory):
    """Generated PCM assets with distinct frequencies to audit the actual mix."""
    folder = tmp_path_factory.mktemp("generated-voice-and-music")
    refs = []
    for name, role, frequency in (("配音", "replace_audio", 880), ("音乐", "bgm", 1320)):
        path = folder / f"{name}.wav"
        rate = 16000
        samples = .12 * np.sin(2 * np.pi * frequency * np.arange(rate * 2) / rate)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes((samples * 32767).astype("<i2").tobytes())
        refs.append({"asset_id": name, "path": str(path), "kind": "audio", "role": role})
    return refs


def _audio_samples(path):
    data = subprocess.run([get_ffmpeg_path(), "-v", "error", "-i", str(path), "-vn",
                           "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                          check=True, capture_output=True, timeout=30).stdout
    return np.frombuffer(data, dtype="<f4")


def _tone_strength(samples, frequency, start=.25, end=1.1):
    segment = samples[int(start * 16000):int(end * 16000)].astype(float)
    window = np.hanning(len(segment))
    phase = np.exp(-2j * np.pi * frequency * np.arange(len(segment)) / 16000)
    return abs((segment * window * phase).sum()) * 2 / window.sum()


def test_real_generated_audio_replaces_original_at_specified_start(media, generated_audio, tmp_path):
    voice = {**generated_audio[0], "timeline_start": .5, "trim_start": .2}
    results = editing.run_timeline_edit("拼接，使用生成的配音替换原声", media[:1] + [voice],
                                         {"resolution": (320, 180), "fps": 12}, tmp_path)
    data, timeline = _project(results)
    assert timeline.video_clips[0].mute
    clip = timeline.audio_clips[0]
    assert clip.source_path == voice["path"]
    assert clip.timeline_start == .5 and clip.trim_start == .2
    assert clip.trim_end == pytest.approx(1.2)
    assert timeline.total_duration == pytest.approx(1.5)
    assert data["local_edit"]["plan"]["audios"][0]["role"] == "replace_audio"
    audio = _audio_samples(results[0]["path"])
    assert np.sqrt(np.mean(audio[1600:4800] ** 2)) < .001  # silence before .5s
    assert _tone_strength(audio, 880, .7, 1.3) > .07
    assert _tone_strength(audio, 440, .7, 1.3) < .003  # removed original, not merely an extra track


@pytest.mark.parametrize("instruction", ["保留原声并添加配音和背景音乐", "混音", "拼接"])
def test_real_audio_roles_and_preserve_mix_rules(media, generated_audio, tmp_path, instruction):
    voice = {**generated_audio[0], "role": "edit_audio" if instruction == "拼接" else "replace_audio"}
    music = {**generated_audio[1], "volume": .4}
    results = editing.run_timeline_edit(instruction, media[:1] + [voice, music],
                                         {"resolution": (320, 180), "fps": 12}, tmp_path)
    _, timeline = _project(results)
    assert not timeline.video_clips[0].mute
    assert len(timeline.audio_tracks) == 2
    assert all(track[0].timeline_start == 0 for track in timeline.audio_tracks)
    assert timeline.audio_tracks[1][0].volume == .4
    assert timeline.total_duration == pytest.approx(1.5)  # longer music does not extend the video
    audio = _audio_samples(results[0]["path"])
    assert _tone_strength(audio, 440) > .06
    assert _tone_strength(audio, 880) > .07
    assert _tone_strength(audio, 1320) > .02


def test_audio_instruction_start_volume_and_explicit_video_mute(media, generated_audio, tmp_path):
    refs = media[:1] + [{**generated_audio[0], "role": "edit_audio"}, generated_audio[1]]
    results = editing.run_timeline_edit("静音，添加配音和背景音乐，配音从0.5秒开始，音乐音量20%", refs,
                                         {"resolution": (320, 180), "fps": 12}, tmp_path)
    _, timeline = _project(results)
    assert timeline.video_clips[0].mute
    assert timeline.audio_tracks[0][0].timeline_start == .5
    assert timeline.audio_tracks[1][0].volume == .2
    audio = _audio_samples(results[0]["path"])
    assert _tone_strength(audio, 880, .1, .35) < .002
    assert _tone_strength(audio, 880, .7, 1.3) > .07
    assert _tone_strength(audio, 1320) > .01
    assert _tone_strength(audio, 440) < .003


@pytest.mark.skipif(os.name != "nt", reason="Windows built-in offline speech synthesis")
def test_real_export_with_locally_generated_voiceover(media, tmp_path):
    # Produces actual speech locally without a web server or paid model, then
    # feeds the resulting asset exactly like a completed voice-generation step.
    voice = tmp_path / "generated narration.wav"
    quoted_path = str(voice).replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"try {{ $speaker.SetOutputToWaveFile('{quoted_path}'); "
        "$speaker.Speak('This is a generated voiceover for the local editing engine.') } "
        "finally { $speaker.Dispose() }")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, capture_output=True, timeout=30)
    assert voice.is_file() and voice.stat().st_size > 1000
    results = editing.run_timeline_edit("把生成的配音合成到视频，替换原声", media[:1] + [
        {"path": str(voice), "kind": "audio", "role": "replace_audio"}],
        {"resolution": (320, 180), "fps": 12}, tmp_path / "finished")
    _, timeline = _project(results)
    assert timeline.video_clips[0].mute and timeline.audio_clips[0].source_path == str(voice)
    samples = _audio_samples(results[0]["path"])
    assert np.sqrt(np.mean(samples ** 2)) > .005


def test_model_cannot_discard_connected_generated_audio(media, generated_audio, tmp_path):
    manager = _Manager({"clips": [{"asset_id": "v0", "trim_end": 1.5}], "audios": []}, _Provider())
    results = editing.run_timeline_edit("拼接", media[:1] + generated_audio,
                                         {"planner": "llm", "resolution": (320, 180), "fps": 12},
                                         tmp_path, manager=manager)
    _, timeline = _project(results)
    assert timeline.video_clips[0].mute
    assert sum(len(track) for track in timeline.audio_tracks) == 2
    audio = _audio_samples(results[0]["path"])
    assert _tone_strength(audio, 880) > .07 and _tone_strength(audio, 1320) > .07


@pytest.mark.parametrize("extra", [
    {"timeline_start": -1}, {"timeline_start": 1.5}, {"timeline_start": float("nan")},
    {"trim_start": -.1}, {"trim_end": 3}, {"trim_start": 1, "trim_end": .5},
    {"volume": float("inf")}, {"mute": "false"}, {"role": "unknown"},
])
def test_invalid_audio_is_rejected_not_ignored(media, generated_audio, tmp_path, extra):
    with pytest.raises(ValueError):
        editing.run_timeline_edit("拼接", media[:1] + [{**generated_audio[0], **extra}], {}, tmp_path)
    assert not list(tmp_path.iterdir())


def test_audio_plan_only_connected_sources_and_all_inputs_retained():
    sources = _sources() + [{"asset_id": "a", "kind": "audio", "duration": 3, "role": "bgm"}]
    plan = {"clips": [{"asset_id": "v0", "trim_end": 2}]}
    checked, duration = editing._validate_plan(plan, sources)
    assert duration == 2 and checked["audios"][0]["trim_end"] == 2
    for row in ({"asset_id": "unconnected"}, {"asset_id": "a", "path": "arbitrary.wav"},
                {"asset_id": "v0"}):
        with pytest.raises(ValueError):
            editing._validate_plan({**plan, "audios": [row]}, sources)


def test_bad_or_missing_audio_stream_is_an_error(media, tmp_path):
    with pytest.raises(ValueError, match="没有连接音频"):
        editing.run_timeline_edit("添加配音", media[:1], {}, tmp_path)
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio")
    with pytest.raises(editing.TimelineEditError, match="素材分析失败"):
        editing.run_timeline_edit("拼接", media[:1] + [{"path": str(bad), "kind": "audio"}], {}, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_audio_words_in_subtitle_are_literal(media, tmp_path):
    results = editing.run_timeline_edit('字幕：“配音从1秒开始，替换原声”', media[:1],
                                         {"resolution": (320, 180), "fps": 12}, tmp_path)
    _, timeline = _project(results)
    assert not timeline.video_clips[0].mute and not timeline.audio_clips
    assert timeline.subtitle_blocks[0].text == "配音从1秒开始，替换原声"


@pytest.mark.parametrize("placement,expected", [
    ({}, .5), ({"timeline_start": 0}, 0), ({"start_time": .25}, .25), ({"start": .75}, .75),
])
def test_audio_start_defaults_and_reference_priority(media, generated_audio, placement, expected):
    sources = editing._references(media[:1] + [{**generated_audio[0], **placement}], None)
    editing._audio_contract("拼接", {"audio_start": .5}, sources)
    checked, _ = editing._validate_plan({"clips": [{"asset_id": "v0", "trim_end": 1.5}]}, sources)
    assert checked["audios"][0]["timeline_start"] == expected


def test_explicit_audio_plan_placement_and_trim(media, generated_audio, tmp_path):
    plan = {"clips": [{"asset_id": "v0", "trim_end": 1.5}], "audios": [
        {"asset_id": "配音", "timeline_start": .25, "trim_start": .5, "trim_end": 1,
         "volume": .5}]}
    results = editing.run_timeline_edit("拼接", media[:1] + generated_audio[:1],
                                         {"plan": plan, "resolution": (320, 180), "fps": 12}, tmp_path)
    _, timeline = _project(results)
    clip = timeline.audio_clips[0]
    assert clip.timeline_start == .25 and clip.trim_start == .5 and clip.trim_end == 1
    assert clip.volume == .5 and timeline.video_clips[0].mute
    audio = _audio_samples(results[0]["path"])
    assert _tone_strength(audio, 880, .35, .65) > .04
    assert np.sqrt(np.mean(audio[16000:20000] ** 2)) < .001


def test_visual_edit_carries_audio_without_screenshotting_audio(media, generated_audio, tmp_path):
    plan = {"clips": [{"asset_id": "v0", "trim_start": .5, "trim_end": 1.5}],
            "visual_evidence": [{"asset_id": "v0", "time": 0, "description": "opening logo"}]}
    manager = _Manager(plan, _Provider())
    results = editing.run_timeline_edit("识别logo画面并删除", media[:1] + generated_audio[:1],
                                         {"resolution": (320, 180), "fps": 12}, tmp_path, manager=manager)
    content = manager.calls[0][1].inputs["messages"][1]["content"]
    assert sum(row["type"] == "image_url" for row in content) == 3
    _, timeline = _project(results)
    assert timeline.total_duration == 1 and timeline.audio_clips[0].trim_end == 1
    assert _tone_strength(_audio_samples(results[0]["path"]), 880, .2, .8) > .07
