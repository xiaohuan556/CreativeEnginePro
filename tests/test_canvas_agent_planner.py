"""Offline planner contract tests; intentionally require no Qt, models or server.

Run directly with ``python tests/test_canvas_agent_planner.py`` or via pytest.
Loading the file avoids ai.__init__ (which starts the application's Qt imports).
"""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch


_spec = importlib.util.spec_from_file_location(
    "isolated_canvas_agent_planner",
    Path(__file__).resolve().parents[1] / "ai" / "canvas_agent_planner.py")
planner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(planner)


def asset(sid="img", kind="image", role="", **extra):
    ext = {"image": "png", "video": "mp4", "audio": "wav", "text": "txt"}[kind]
    return {"id": sid, "path": f"C:/attached/{sid}.{ext}", "media_type": kind,
            "title": sid, "role": role, **extra}


class PlannerTests(unittest.TestCase):
    def ready(self, instruction, refs=None, settings=None):
        refs = refs or []
        plan = planner.plan_agent(instruction, refs, settings)
        self.assertEqual(plan["status"], "ready", plan)
        self.assertEqual(plan["needs_input"], [])
        self.assertTrue(planner.validate_plan(plan, refs))
        self.assertEqual(json.loads(json.dumps(plan)), plan)
        return plan

    def blocked(self, instruction, refs=None, settings=None):
        plan = planner.plan_agent(instruction, refs or [], settings)
        self.assertEqual(plan["status"], "needs_input", plan)
        self.assertEqual(plan["steps"], [])
        self.assertTrue(all(isinstance(v, str) for v in plan["needs_input"]))
        self.assertTrue(plan["needs_input"])
        self.assertTrue(planner.validate_plan(plan, refs or []))
        with self.assertRaises(ValueError):
            planner.build_planning_messages(instruction, refs or [], settings)
        return plan

    def test_constants_and_white_list(self):
        self.assertEqual(planner.PLAN_SCHEMA, "creative_engine.canvas_agent_plan")
        self.assertEqual(planner.PLAN_VERSION, 1)
        self.assertEqual(planner.ALLOWED_TOOLS, {
            "image_generate", "video_generate", "audio_generate", "script_generate",
            "video_analysis", "image_upscale", "slideshow", "video_mix", "image_batch",
            "audio_separate", "timeline_edit", "pinterest_import"})
        self.assertEqual(set(planner.TOOL_PARAMS), planner.ALLOWED_TOOLS)

    def test_removed_requests_explain_unsupported_instead_of_falling_back(self):
        examples = {
            "AE控制中心": ("AE控制中心", "用AE渲染模板", "使用 D:/user.aep 中的 Main 制作品牌视觉",
                         "render with After Effects"),
            "AE水印尾页": ("AE水印尾页", "生成透明水印", "制作尾页，产品名:kika", "生成片尾", "做个品牌尾页"),
            "链接下载": ("链接下载", "下载视频 https://example.com/input.mp4", "下载图片", "下载音频",
                       "download media https://example.com/input.mp4"),
            "视频包装": ("视频包装", "给视频添加水印和尾页", "给视频加个水印", "给视频增加水印",
                       "拼接片尾", "video packaging", "add watermark to video", "append outro"),
            "批量智能消除": ("批量智能消除", "批量消除图片水印", "消除右下角水印", "擦除底部文字",
                         "移除图片中央物体", "去除水印", "remove watermark", "inpainting"),
        }
        attached = [asset(), asset("v", "video"), asset("mask", role="mask"),
                    asset("wm", "video", "watermark_overlay"), asset("end", "video", "outro"),
                    asset("icon", role="product_icon")]
        for reason, instructions in examples.items():
            for instruction in instructions:
                for refs in ([], attached):
                    with self.subTest(instruction=instruction, attached=bool(refs)):
                        plan = self.blocked(instruction, refs)
                        self.assertIn(reason, plan["summary"])
                        self.assertIn("不支持", plan["summary"])
                        self.assertIn("已移除", "；".join(plan["needs_input"]))

    def test_removed_requests_cannot_bypass_guard_with_routes_or_fixed_tools(self):
        refs = [asset(), asset("v", "video")]
        for text in ("下载图片", "生成尾页", "视频包装", "批量消除水印", "AE控制中心"):
            for route in ("auto", "image", "video", "audio", "editor"):
                with self.subTest(text=text, route=route):
                    self.blocked(text, refs, {"agent_route": route})
            for tool in ("image_generate", "image_batch", "timeline_edit"):
                with self.subTest(text=text, tool=tool):
                    self.blocked(text, refs, {"agent_tool": tool})

    def test_fixed_removed_tools_are_explicitly_unsupported(self):
        for tool in ("ae_template_render", "media_download", "video_packaging", "inpaint"):
            for text in ("", "生成海报"):
                with self.subTest(tool=tool, text=text):
                    self.blocked(text, [asset()], {"agent_tool": tool})

    def test_removed_requests_clear_mixed_and_unknown_chains(self):
        for text in ("生成图片，然后下载视频 https://example.com/a.mp4，然后拉片",
                     "神奇处理；批量消除图片水印；生成海报", "识别产品Logo删除，并添加水印",
                     "生成视频，然后配音，然后视频包装", "制作图片轮播，然后生成尾页"):
            with self.subTest(text=text):
                self.blocked(text, [asset(), asset("v", "video")])

    def test_erasure_stays_removed_with_mask_region_and_legacy_batch_settings(self):
        refs = [asset(), asset("mask", role="mask")]
        for text in ("消除水印", "消除水印 x=0.1 y=0.2 width=0.3 height=0.4",
                     "批量图片固定蒙版", "批量图片去 水 印", "watermark_fixed"):
            with self.subTest(text=text):
                self.blocked(text, refs, {"agent_tool": "image_batch"})
        for settings in ({"region": {"x": .1, "y": .2, "width": .3, "height": .4}},
                         {"erase_x": .1, "erase_y": .2, "erase_width": .3, "erase_height": .4},
                         {"mode": "watermark_fixed"}, {"ae_render_mode": "control_center"},
                         {"project_path": "D:/user.aep", "comp": "Main"}, {"add_watermark": True}):
            with self.subTest(settings=settings):
                self.blocked("批量压缩图片", refs, settings)

    def test_every_retained_tool_actually_plans_and_round_trips(self):
        examples = {
            "image_generate": ("生成海报", []),
            "video_generate": ("生成视频", []),
            "audio_generate": ("朗读你好", []),
            "script_generate": ("写产品脚本", []),
            "video_analysis": ("拉片分析视频", [asset("v", "video")]),
            "image_upscale": ("4倍高清化", [asset()]),
            "slideshow": ("图片按顺序轮播，时长6秒", [asset("i1"), asset("i2")]),
            "video_mix": ("视频混剪，时长10秒", [asset("v", "video")]),
            "image_batch": ("把图片转成png", [asset()]),
            "audio_separate": ("分离人声和背景音乐", [asset("a", "audio")]),
            "timeline_edit": ("视频静音", [asset("v", "video")]),
        }
        self.assertEqual(set(examples), planner.ALLOWED_TOOLS - {"pinterest_import"})
        for tool, (text, refs) in examples.items():
            with self.subTest(tool=tool):
                plan = self.ready(text, refs)
                self.assertEqual([step["tool"] for step in plan["steps"]], [tool])
                self.assertEqual(plan["steps"][0]["inputs"], [ref["id"] for ref in refs])
                self.assertTrue(planner.validate_plan(plan, refs, text))
                self.assertEqual(planner.parse_plan_json(json.dumps(plan), refs, text), plan)

    def test_retained_processors_still_require_their_sources(self):
        for text in ("拉片", "图片高清化", "图片轮播", "视频混剪", "批量压缩图片",
                     "分离人声和背景音乐", "视频静音"):
            with self.subTest(text=text):
                plan = self.blocked(text)
                self.assertNotIn("已移除", plan["summary"])

    def test_upscale_feeds_video_without_erasure(self):
        first, second = self.ready("4倍高清化，然后图生视频", [asset()])["steps"]
        self.assertEqual(first["tool"], "image_upscale")
        self.assertEqual(first["params"]["scale"], 4)
        self.assertEqual(second["tool"], "video_generate")
        self.assertEqual(second["inputs"], [first["id"]])
        self.assertEqual(second["dependencies"], [first["id"]])

    def test_fixed_retained_tools_keep_empty_instruction_support(self):
        for tool, refs, settings in (
                ("image_upscale", [asset()], {"upscale_mode": "traditional"}),
                ("image_generate", [], {}), ("video_generate", [], {}),
                ("audio_generate", [], {}), ("script_generate", [], {}),
                ("video_analysis", [asset("v", "video")], {}),
                ("timeline_edit", [asset("v", "video")], {})):
            with self.subTest(tool=tool):
                plan = self.ready("", refs, {"agent_tool": tool, **settings})
                self.assertEqual([step["tool"] for step in plan["steps"]], [tool])
                if tool == "image_upscale":
                    self.assertEqual(plan["steps"][0]["params"]["mode"], "traditional")

    def test_llm_contract_only_exposes_retained_tools_and_parameters(self):
        system = planner.build_planning_messages("请帮我整理创作策略", [])[0]["content"]
        for removed in ("ae_template_render", "media_download", "video_packaging", "inpaint",
                        "erase_x", "erase_y", "erase_width", "erase_height", "project_path",
                        "output_module", "ae_render_mode", "watermark_fixed"):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, system)
                self.assertTrue(all(removed not in params for params in planner.TOOL_PARAMS.values()))
        for tool in planner.ALLOWED_TOOLS:
            self.assertIn(tool, system)
        self.assertIn("status=needs_input", system)
        self.assertIn("已移除", system)

    def test_generation_chain_and_explicit_dimensions(self):
        plan = self.ready("生成海报，产品名称: kika，尺寸1080x1920，然后图生视频，时长6秒")
        first, second = plan["steps"]
        self.assertEqual([s["tool"] for s in plan["steps"]], ["image_generate", "video_generate"])
        self.assertEqual(first["params"]["mode"], "text_to_image")
        self.assertEqual(first["params"]["product_name"], "kika")
        self.assertEqual((first["params"]["width"], first["params"]["height"]), (1080, 1920))
        self.assertEqual(second["params"]["duration"], 6)
        self.assertEqual(second["inputs"], [first["id"]])
        self.assertEqual(second["dependencies"], [first["id"]])

    def test_instruction_overrides_saved_settings(self):
        plan = self.ready("生成9:16海报，然后图生视频，时长6秒", settings={"ratio": "1:1", "duration": 10})
        self.assertEqual(plan["steps"][1]["params"]["ratio"], "9:16")
        self.assertEqual(plan["steps"][1]["params"]["duration"], 6)

    def test_resize_then_slideshow_never_uses_generation(self):
        refs = [asset("i1"), asset("i2")]
        plan = self.ready("把图片统一调整成160x120，然后制作图片轮播", refs)
        first, second = plan["steps"]
        self.assertEqual([s["tool"] for s in plan["steps"]], ["image_batch", "slideshow"])
        self.assertEqual(first["params"]["image_width"], 160)
        self.assertEqual(first["params"]["image_height"], 120)
        self.assertEqual(first["inputs"], ["i1", "i2"])
        self.assertEqual(second["inputs"], [first["id"]])

    def test_image_formats_and_rename_are_local(self):
        for instruction in ("把图片转成png", "图片重命名为product_{num}", "图片格式转换为webp", "批量压缩图片"):
            with self.subTest(instruction=instruction):
                plan = self.ready(instruction, [asset()])
                self.assertEqual(plan["steps"][0]["tool"], "image_batch")

    def test_edit_and_multimodal_reference_sources(self):
        refs = [asset(), {"id": "brief", "media_type": "text", "content": "卖点与规格"}]
        plan = self.ready("编辑图片为海报", refs)
        self.assertEqual(plan["steps"][0]["params"]["mode"], "image_edit")
        self.assertEqual(plan["steps"][0]["inputs"], ["img", "brief"])
        self.blocked("改图")

    def test_image_to_video_and_multi_images(self):
        self.assertEqual(self.ready("图生视频", [asset()])["steps"][0]["params"]["mode"], "image_to_video")
        plan = self.ready("多图生成视频，9:16，8秒", [asset("i1"), asset("i2")])
        self.assertEqual(plan["steps"][0]["params"]["mode"], "multi_image")
        self.assertEqual(plan["steps"][0]["inputs"], ["i1", "i2"])
        self.blocked("多图生成视频", [asset()])
        self.blocked("图生视频")

    def test_direct_video_audio_and_text(self):
        refs = [asset("v", "video"), asset("a", "audio"),
                {"id": "t", "media_type": "text/plain", "content": "脚本"}]
        plan = self.ready("生成视频", refs)
        self.assertEqual(plan["steps"][0]["inputs"], ["v", "a", "t"])
        self.assertEqual(self.ready("拉片分析视频", refs)["steps"][0]["inputs"], ["v"])

    def test_script_to_audio_content_contract(self):
        plan = self.ready("写产品文案，然后朗读配音")
        script, audio = plan["steps"]
        self.assertEqual(script["tool"], "script_generate")
        self.assertEqual(audio["tool"], "audio_generate")
        self.assertEqual(audio["inputs"], [script["id"]])
        self.assertIs(audio["params"]["text_from_inputs"], True)

    def test_quoted_dialogue_is_preserved(self):
        plan = self.ready('朗读“先问好；然后说欢迎，再生成祝福”')
        self.assertEqual(len(plan["steps"]), 1)
        self.assertIn("先问好；然后说欢迎，再生成祝福", plan["steps"][0]["prompt"])

    def test_voice_clone_requires_audio_consent_and_transcript(self):
        self.blocked("克隆声音")
        self.blocked("声音克隆", [asset("a", "audio")])
        self.blocked("声音克隆", [asset("a", "audio")], {"voice_consent": True})
        plan = self.ready("声音克隆，朗读你好", [asset("a", "audio")],
                          {"voice_consent": True, "reference_transcript": "参考录音原文"})
        self.assertEqual(plan["steps"][0]["params"]["mode"], "voice_clone")

    def test_local_timeline_route_preserves_explicit_effects(self):
        plan = self.ready("静音，模糊12，缩放150%，添加字幕：你好，淡入淡出1秒", [asset("v", "video")],
                          {"agent_route": "editor", "resolution": [1920, 1080]})
        step = plan["steps"][0]
        self.assertEqual(step["tool"], "timeline_edit")
        self.assertEqual(step["params"]["mode"], "local")
        self.assertEqual(step["params"]["effects"], {"blur_radius": 12, "scale": 1.5, "fade_duration": 1})
        self.assertTrue(step["params"]["mute"])

    def test_semantic_edit_delegates_evidence_without_inventing_times(self):
        plan = self.ready("识别产品Logo删除", [asset("v", "video")])
        step = plan["steps"][0]
        self.assertEqual(step["tool"], "timeline_edit")
        self.assertEqual(step["params"]["mode"], "semantic")
        self.assertNotIn("plan", step["params"])
        self.assertEqual(step["prompt"], "识别产品Logo删除")

    def test_generate_video_voice_and_composite_full_chain(self):
        plan = self.ready("生成视频，然后写产品文案，然后配音，然后合成成片")
        video, script, audio, composite = plan["steps"]
        self.assertEqual([s["tool"] for s in plan["steps"]],
                         ["video_generate", "script_generate", "audio_generate", "timeline_edit"])
        self.assertEqual(audio["inputs"], [script["id"]])
        self.assertTrue(audio["params"]["text_from_inputs"])
        self.assertEqual(composite["inputs"], [video["id"], audio["id"]])

    def test_reference_video_script_uses_analysis_report(self):
        analysis, script = self.ready("参考视频写脚本", [asset("v", "video")])["steps"]
        self.assertEqual(analysis["tool"], "video_analysis")
        self.assertEqual(script["inputs"], [analysis["id"]])

    def test_audio_video_composition_routes_and_source_requirements(self):
        refs = [asset("v", "video"), asset("a", "audio")]
        for text in ("合成成片", "合成视频", "把配音加到视频"):
            with self.subTest(text=text):
                step = self.ready(text, refs)["steps"][0]
                self.assertEqual(step["tool"], "timeline_edit")
                self.assertEqual(step["inputs"], ["v", "a"])
                self.assertEqual(step["params"]["mode"], "local")
        self.blocked("合成成片", [asset("a", "audio")])

    def test_audio_and_video_prior_results_feed_composition(self):
        plan = self.ready("生成视频，然后配音，然后合成成片")
        video, audio, final = plan["steps"]
        self.assertEqual(final["tool"], "timeline_edit")
        self.assertEqual(final["inputs"], [video["id"], audio["id"]])
        self.assertEqual(final["dependencies"], final["inputs"])

    def test_editor_accepts_current_runtime_resolution_profiles(self):
        plan = self.ready("静音", [asset("v", "video")], {"agent_route": "editor", "resolution": "1080p"})
        self.assertEqual(plan["steps"][0]["params"]["resolution"], "1080p")

    def test_routes_are_respected(self):
        for route, tool in (("image", "image_generate"), ("video", "video_generate"), ("audio", "audio_generate")):
            with self.subTest(route=route):
                plan = self.ready("创作一个效果", settings={"agent_route": route})
                self.assertEqual(plan["steps"][0]["tool"], tool)
        self.blocked("剪辑", settings={"agent_route": "editor"})

    def test_source_mentions_and_missing_ids(self):
        plan = self.ready("编辑图片 @b", [asset("a"), asset("b")])
        self.assertEqual(plan["steps"][0]["inputs"], ["b"])
        self.blocked("编辑图片 @missing", [asset()])

    def test_source_ids_cannot_collide_with_steps(self):
        plan = self.ready("编辑图片，然后图生视频", [asset("step_1"), asset("step_3")])
        self.assertEqual(len({s["id"] for s in plan["steps"]}), 2)
        self.assertFalse({s["id"] for s in plan["steps"]} & {"step_1", "step_3"})

    def test_missing_input_wins_over_unknown_clauses_and_clears_partial_steps(self):
        self.blocked("生成海报；神奇处理；消除右下角水印")
        self.blocked("神奇处理；拉片")

    def test_unknown_and_empty_instruction_envelopes(self):
        self.blocked("")
        plan = planner.plan_agent("请帮我整理创作策略", [])
        self.assertEqual(plan["status"], "unknown")
        self.assertEqual(plan["steps"], [])
        self.assertTrue(planner.validate_plan(plan, []))
        messages = planner.build_planning_messages(plan["instruction"], [])
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertEqual(json.loads(messages[1]["content"])["instruction"], plan["instruction"])

    def test_references_support_mime_extension_and_text_without_path(self):
        plan = self.ready("生成图片", [{"id": "i", "path": "C:/图.png"},
                                        {"id": "t", "content": "描述", "media_type": "text/plain"}])
        self.assertEqual(plan["steps"][0]["inputs"], ["i", "t"])

    def test_invalid_reference_and_settings_contract(self):
        for refs in ({}, [asset(), asset()], [{"id": "x", "media_type": "image"}],
                     [{"id": "x", "path": "x.exe"}], [asset(role=[])], [asset(sid="")]):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                planner.plan_agent("生成图片", refs)
        with self.assertRaises(ValueError):
            planner.plan_agent("生成图片", [], {"agent_tool": "exec"})

    def test_planning_does_not_mutate_or_execute(self):
        refs = [asset()]
        settings = {"image_quality": 80}
        original = deepcopy((refs, settings))
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                patch("subprocess.Popen", side_effect=AssertionError("execution forbidden")), \
                patch("builtins.open", side_effect=AssertionError("file read forbidden")):
            self.ready("批量压缩图片", refs, settings)
        self.assertEqual((refs, settings), original)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.refs = [asset()]
        self.plan = planner.plan_agent("编辑图片，然后图生视频", self.refs)

    def rejects(self, mutate):
        plan = deepcopy(self.plan)
        mutate(plan)
        with self.assertRaises(ValueError):
            planner.validate_plan(plan, self.refs)

    def test_id_dependencies_and_reference_integrity(self):
        mutations = [
            lambda p: p["steps"][1].update(id=p["steps"][0]["id"]),
            lambda p: p["steps"][0].update(id="img"),
            lambda p: p["steps"][0].update(inputs=["missing"]),
            lambda p: p["steps"][0].update(inputs=["C:/unattached.png"]),
            lambda p: p["steps"][0].update(inputs=[p["steps"][1]["id"]]),
            lambda p: p["steps"][0].update(dependencies=[p["steps"][0]["id"]]),
            lambda p: p["steps"][1].update(dependencies=[]),
            lambda p: p["steps"][1].update(dependencies=["img"]),
            lambda p: p["steps"][0].update(inputs=["img", "img"]),
            lambda p: p["steps"][0].update(inputs=[{"id": "img"}]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.rejects(mutate)

    def test_invalid_tools_and_injected_commands(self):
        self.rejects(lambda p: p["steps"][0].update(tool="python"))
        for key in ("command", "executable", "python_code", "mask_path", "ffmpeg_path"):
            with self.subTest(key=key):
                self.rejects(lambda p: p["steps"][0]["params"].update({key: "untrusted"}))

    def test_invalid_plan_shapes_and_nonfinite_numbers(self):
        mutations = [lambda p: p.update(version=2), lambda p: p.update(version=True),
                     lambda p: p.update(status=[]), lambda p: p.update(needs_input="missing"),
                     lambda p: p.update(status="needs_input", needs_input=["missing"]),
                     lambda p: p["steps"][0]["params"].update(duration=float("nan")),
                     lambda p: p["steps"][0]["params"].update(duration=float("inf")),
                     lambda p: p["steps"][0].update(prompt=""),
                     lambda p: p["steps"][0]["params"].update(width=True)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.rejects(mutate)

    def test_incompatible_media_is_rejected(self):
        self.rejects(lambda p: p["steps"][0].update(tool="video_analysis"))

    def test_json_and_fenced_json_round_trip(self):
        for output in (json.dumps(self.plan), "```json\n" + json.dumps(self.plan) + "\n```"):
            self.assertEqual(planner.parse_plan_json(output, self.refs, self.plan["instruction"]), self.plan)

    def test_rejects_invalid_duplicate_and_nonfinite_json(self):
        for output in ("prefix {} suffix", "[]", '{"schema":1,"schema":2}', '{"a": NaN}', "```python\n{}\n```"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                planner.parse_plan_json(output, self.refs, "创作策略")

    def test_llm_cannot_change_original_instruction(self):
        with self.assertRaises(ValueError):
            planner.parse_plan_json(json.dumps(self.plan), self.refs, "别的指令")


    def test_llm_cannot_return_removed_tools(self):
        for tool in ("ae_template_render", "media_download", "video_packaging", "inpaint"):
            with self.subTest(tool=tool):
                plan = deepcopy(self.plan)
                plan["steps"][0]["tool"] = tool
                with self.assertRaisesRegex(ValueError, "not whitelisted"):
                    planner.parse_plan_json(json.dumps(plan), self.refs, plan["instruction"])

    def test_llm_cannot_reinterpret_removed_requests_as_generation_or_unknown(self):
        for text in ("AE控制中心", "链接下载", "生成尾页", "视频包装", "批量智能消除"):
            for status in ("ready", "unknown"):
                with self.subTest(text=text, status=status):
                    plan = deepcopy(self.plan)
                    plan["instruction"] = text
                    if status == "unknown":
                        plan.update(status="unknown", steps=[])
                    with self.assertRaisesRegex(ValueError, "Unsupported request"):
                        planner.parse_plan_json(json.dumps(plan), self.refs, text)
                    with self.assertRaisesRegex(ValueError, "Unsupported request"):
                        planner.validate_plan(plan, self.refs)

    def test_llm_cannot_hide_unsupported_reason_in_missing_input(self):
        plan = planner.plan_agent("消除水印", self.refs)
        plan.update(needs_input=["请添加蒙版。"], summary="需要蒙版。")
        with self.assertRaisesRegex(ValueError, "Unsupported request"):
            planner.parse_plan_json(json.dumps(plan), self.refs, plan["instruction"])

    def test_llm_removed_refusals_round_trip(self):
        for text, settings in (("消除水印", None), ("给视频添加尾页", None),
                               ("生成图片", {"agent_tool": "media_download"})):
            with self.subTest(text=text, settings=settings):
                plan = planner.plan_agent(text, self.refs, settings)
                self.assertEqual(planner.parse_plan_json(json.dumps(plan), self.refs, text, settings), plan)

    def test_llm_cannot_bypass_removed_settings_with_an_allowed_tool(self):
        for settings in ({"agent_tool": "inpaint"}, {"mode": "watermark_fixed"},
                         {"erase_x": .1}, {"project_path": "D:/user.aep", "comp": "Main"}):
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, "Unsupported request"):
                planner.parse_plan_json(json.dumps(self.plan), self.refs, self.plan["instruction"], settings)

    def test_llm_cannot_smuggle_removed_operations_in_retained_step_prompts(self):
        for tool in ("image_batch", "image_generate", "video_generate", "timeline_edit"):
            for prompt in ("批量消除水印", "固定蒙版", "watermark_fixed", "去 水 印", "下载图片", "生成尾页"):
                with self.subTest(tool=tool, prompt=prompt):
                    plan = planner.plan_agent("批量压缩图片", self.refs)
                    plan["steps"][0].update(tool=tool, params={}, prompt=prompt)
                    with self.assertRaisesRegex(ValueError, "Unsupported step"):
                        planner.parse_plan_json(json.dumps(plan), self.refs, plan["instruction"])

    def test_llm_cannot_smuggle_removed_parameters_into_image_batch(self):
        for params in ({"erase_x": .1, "erase_y": .2, "erase_width": .3, "erase_height": .4},
                       {"operations": ["watermark_fixed"]}, {"mode": "watermark_fixed"},
                       {"ae_render_mode": "watermark"}, {"url": "https://example.com/a.png"},
                       {"add_watermark": True}):
            with self.subTest(params=params):
                plan = planner.plan_agent("批量压缩图片", self.refs)
                plan["steps"][0]["params"] = params
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    planner.parse_plan_json(json.dumps(plan), self.refs, plan["instruction"])


if __name__ == "__main__":
    unittest.main()
