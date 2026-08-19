from creative_server.request_compiler import compile_request


def test_continue_video_uses_image_to_video_contract_after_tail_extraction() -> None:
    inputs, params = compile_request("image_to_video", {"prompt": "无缝续拍", "image": "tail.jpg"}, {"duration": 8, "ratio": "16:9"}, "基于尾帧续拍")
    assert inputs["image"] == "tail.jpg"
    assert params["duration"] == 8
    assert params["aspect_ratio"] == "16:9"
