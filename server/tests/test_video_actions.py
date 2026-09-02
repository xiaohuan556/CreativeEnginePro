from creative_server.request_compiler import compile_request
from creative_server.provider_catalog import available_providers
from creative_server.task_policy import estimate_task_credits


def test_continue_video_uses_image_to_video_contract_after_tail_extraction() -> None:
    inputs, params = compile_request("image_to_video", {"prompt": "无缝续拍", "image": "tail.jpg"}, {"duration": 8, "ratio": "16:9"}, "基于尾帧续拍")
    assert inputs["image"] == "tail.jpg"
    assert params["duration"] == 8
    assert params["aspect_ratio"] == "16:9"


def test_seedance_catalog_exposes_25_video_edit_contract(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "SEEDREAM_API_KEY", "ark-test")
    available_providers.cache_clear()
    try:
        seedance = next(item for item in available_providers()
                        if item["name"] == "seedance")
        assert "video_edit" in seedance["capabilities"]
        assert any("2-5" in item["id"] for item in seedance["profile"]["models"])
        assert estimate_task_credits("video_edit", "seedance") == 60
    finally:
        available_providers.cache_clear()
