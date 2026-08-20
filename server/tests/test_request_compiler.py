from creative_server.request_compiler import compile_request


def test_chat_compiler_produces_real_provider_messages() -> None:
    inputs, params = compile_request("chat", {"prompt": "一个雨夜故事"}, {"copywriting_workbench": True, "product_name": "雨伞", "copy_duration": "15"}, "生成口播文案", "deepseek-chat")
    assert inputs["messages"][0]["role"] == "system"
    assert "雨伞" in inputs["messages"][1]["content"]
    assert params["model"] == "deepseek-chat"


def test_image_and_director_video_params_match_desktop_contract() -> None:
    image_inputs, image_params = compile_request("text_to_image", {"prompt": "机器人"}, {"ratio": "16:9", "candidate_count": 2})
    assert image_params["size"] == "2048x1152"
    assert image_params["n"] == 2
    video_inputs, video_params = compile_request("text_to_video", {"prompt": "连续动作"}, {"multi_image_director": True, "duration": 9, "timeline_images": [{"start": 0, "end": 3, "instruction": "推镜"}]})
    assert "0–3秒" in video_inputs["prompt"]
    assert video_params["duration"] == 9


def test_image_style_prompt_is_appended_once() -> None:
    style_prompt = "以输入图片为唯一结构基准。\n只改变：整张画面的材质。"
    inputs, _ = compile_request(
        "image_edit",
        {"prompt": "把画面变成玻璃雕塑"},
        {"ratio": "1:1", "style_prompt": style_prompt},
    )
    assert inputs["prompt"].endswith(style_prompt)
    assert inputs["prompt"].count("以输入图片为唯一结构基准") == 1

    repeated, _ = compile_request(
        "image_edit",
        {"prompt": inputs["prompt"]},
        {"style_prompt": style_prompt},
    )
    assert repeated["prompt"].count("以输入图片为唯一结构基准") == 1


def test_video_request_carries_native_audio_contract() -> None:
    inputs, params = compile_request(
        "text_to_video",
        {"prompt": "机器人穿过雨巷"},
        {"duration": 8, "generate_audio": True, "audio_prompt": "0秒雨声；2秒脚步声；5秒机器人说‘到了’"},
    )
    assert "声音计划：0秒雨声" in inputs["prompt"]
    assert params["generate_audio"] is True

    silent_inputs, silent_params = compile_request(
        "text_to_video",
        {"prompt": "无声空镜"},
        {"generate_audio": False, "audio_prompt": "不应进入提示词"},
    )
    assert "声音计划" not in silent_inputs["prompt"]
    assert silent_params["generate_audio"] is False
