from ai.canvas_registry import (
    CREATION_ITEMS, NODE_CONNECTION_TARGETS, can_connect,
    connection_create_choices, creation_payload, infer_node_spec,
)
from ai.providers.video.seedance_models import (
    SEEDANCE_20_MODEL, SEEDANCE_25_MODEL, seedance_model_profile,
)


def test_creation_menu_matches_webai_beginner_order():
    assert [row[1] for row in CREATION_ITEMS] == [
        "文本", "脚本", "图片", "视频", "音频",
        "短片", "口播", "克隆", "多图导演", "拉片", "镜头",
    ]


def test_manual_links_use_production_whitelist():
    assert can_connect("text", "script")
    assert can_connect("script", "storyboard")
    assert can_connect("video", "analysis")
    assert can_connect("analysis", "shot")
    assert not can_connect("storyboard", "video")
    assert not can_connect("audio", "video")
    assert "video_style_transfer" in NODE_CONNECTION_TARGETS["video"]


def test_dangling_wire_choices_keep_semantic_relations():
    text_choices = connection_create_choices("text", "text")
    assert {row["label"] for row in text_choices} >= {
        "写成分镜脚本", "用文字生成图片", "用文字生成视频",
    }
    image_choices = connection_create_choices("image_asset", "image")
    assert {row["relation"] for row in image_choices} >= {
        "reference", "first_frame", "timeline_image", "shot_reference",
    }
    video_choices = connection_create_choices("video", "video")
    assert {row["relation"] for row in video_choices} == {
        "video_source", "content_video", "voice_reference",
    }


def test_specialized_nodes_reuse_desktop_editors_without_losing_identity():
    node_type, payload = creation_payload("voice_clone", "clone_voice")
    assert node_type == "audio_node"
    assert infer_node_spec(node_type, payload) == "voice_clone"
    node_type, payload = creation_payload("video_style_transfer")
    assert node_type == "video_node"
    assert infer_node_spec(node_type, payload) == "video_style_transfer"
    assert payload["style_strength"] == 70
    assert "人物身份" in payload["style_preserve"]
    timeline_type, timeline = creation_payload("long_timeline")
    assert infer_node_spec(timeline_type, timeline) == "long_timeline"
    assert can_connect("long_timeline", "analysis")


def test_seedance_model_capabilities_distinguish_20_and_25():
    old = seedance_model_profile(SEEDANCE_20_MODEL)
    new = seedance_model_profile(SEEDANCE_25_MODEL)
    assert old["max_duration"] == 15
    assert not old["supports_video_edit"]
    assert not old["supports_extend"]
    assert new["max_duration"] == 30
    assert new["supports_video_edit"]
    assert new["supports_extend"]
    assert new["reference_videos"] == 10
