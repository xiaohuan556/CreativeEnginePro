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
