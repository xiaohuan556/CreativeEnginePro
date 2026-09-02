from ai.style_presets import STYLE_PRESETS, compile_style_prompt


def test_desktop_carries_full_web_style_catalog():
    assert len(STYLE_PRESETS) >= 40
    assert {"吹制玻璃", "针织毛线", "厚涂油画", "生物荧光"}.issubset(
        {item["label"] for item in STYLE_PRESETS})


def test_style_prompt_keeps_reference_contract_and_scope():
    prompt = compile_style_prompt({
        "style_preset":"knitted-wool", "style_scope":"subject",
        "style_strength":80,
        "style_preserve":["identity", "pose", "composition"],
    }, True)
    assert "唯一结构基准" in prompt
    assert "只转换主体" in prompt
    assert "主体身份" in prompt
    assert "knitted wool" in prompt
