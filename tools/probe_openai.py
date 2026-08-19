# -*- coding: utf-8 -*-
"""
ModelHub 统一代理（ChatGPT / GPT-Image / Veo 3.1）配置与连通性探针。

用法：
  python tools/probe_openai.py            # 只检查配置，不发起真实调用（无费用）
  python tools/probe_openai.py --chat     # 真实调用一次聊天（产生费用）
  python tools/probe_openai.py --image    # 真实调用一次图片生成（产生费用）
  python tools/probe_openai.py --video    # 真实调用一次视频生成（产生费用）
  python tools/probe_openai.py --all      # 同时验证全部三项（产生费用）

当前网关：modelhub.ailemac.com
基址已写入 .env 的 OPENAI_BASE_URL。
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_CTX = ssl.create_default_context()


def _post_json(url: str, api_key: str, payload: dict, timeout: int = 120) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {raw[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}") from e


def _get_json(url: str, api_key: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {raw[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}") from e


def _mask(key: str) -> str:
    if not key:
        return "(未配置)"
    return key[:6] + "..." + key[-4:] if len(key) > 10 else key[:6] + "..."


def _check_config():
    import api_config
    from config import LLM_API_KEY, LLM_MODE, LLM_MODEL, LLM_BASE_URL

    print("═" * 55)
    print("ModelHub 统一代理配置检查  (modelhub.ailemac.com)")
    print("═" * 55)

    print(f"\n[LLM - ChatGPT]")
    print(f"  base_url       = {LLM_BASE_URL}")
    print(f"  LLM_MODE       = {LLM_MODE}")
    print(f"  LLM_MODEL      = {LLM_MODEL}")
    print(f"  LLM_API_KEY    = {_mask(LLM_API_KEY)}")

    print(f"\n[图像 - GPT-Image]")
    img = api_config.get("openai_image")
    print(f"  base_url       = {img.default_base_url}")
    print(f"  default_model  = {img.default_model}")
    print(f"  OPENAI_API_KEY = {_mask(img.value())}")

    print(f"\n[视频 - Veo 3.1]")
    vid = api_config.get("veo")
    print(f"  base_url       = {vid.default_base_url}")
    print(f"  default_model  = {vid.default_model}")
    print(f"  OPENAI_API_KEY = {_mask(vid.value())}")

    ok = True
    if not LLM_API_KEY:
        print("\n❌ LLM_API_KEY 未配置")
        ok = False
    if LLM_MODE != "openai":
        print(f"\n⚠️ LLM_MODE 当前是 {LLM_MODE}，想走 ChatGPT 请设为 openai")
        ok = False
    if not img.value():
        print("\n⚠️ OPENAI_API_KEY 未配置，GPT-Image / Veo 无法使用")
        ok = False

    print("\n" + ("✅ 配置检查通过" if ok else "❌ 配置检查未通过"))
    return ok


def _probe_chat():
    from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    print("\n[真实调用] ChatGPT chat.completions")
    api_key = LLM_API_KEY
    base = (LLM_BASE_URL or "https://modelhub.ailemac.com/api/v1").rstrip("/")
    model = LLM_MODEL or "gpt-5.5"
    if not api_key:
        print("❌ 跳过：LLM_API_KEY 未配置")
        return False
    try:
        resp = _post_json(
            f"{base}/chat/completions", api_key,
            {"model": model, "messages": [{"role": "user", "content": "Say OK."}], "max_completion_tokens": 10},
            timeout=60,
        )
        choice = (resp.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "")
        print(f"✅ 成功 | model={model} | 返回: {content[:80]}")
        return True
    except Exception as e:
        print(f"❌ 失败: {e}")
        return False


def _probe_image():
    import api_config
    img = api_config.get("openai_image")
    api_key = img.value()
    base = (img.default_base_url or os.environ.get("OPENAI_BASE_URL") or "https://modelhub.ailemac.com/api/v1").rstrip("/")
    model = img.default_model or "gpt-image-2"
    print("\n[真实调用] GPT-Image images/generations")
    if not api_key:
        print("❌ 跳过：OPENAI_API_KEY 未配置")
        return False
    try:
        resp = _post_json(
            f"{base}/images/generations", api_key,
            {"model": model, "prompt": "A cute cat icon, flat design, white background",
             "n": 1, "size": "1024x1024", "response_format": "url"},
            timeout=180,
        )
        data = resp.get("data") or []
        url = (data[0] if data else {}).get("url")
        if url:
            print(f"✅ 成功 | model={model} | image_url={url[:80]}...")
            return True
        print(f"❌ 返回异常: {json.dumps(resp, ensure_ascii=False)[:300]}")
        return False
    except Exception as e:
        print(f"❌ 失败: {e}")
        return False


def _probe_video():
    import api_config, time
    vid = api_config.get("veo")
    api_key = vid.value()
    base = (vid.default_base_url or os.environ.get("OPENAI_BASE_URL") or "https://modelhub.ailemac.com/api/v1").rstrip("/")
    model = vid.default_model or "veo-3.1-generate-preview"
    print("\n[真实调用] Veo 3.1 videos/generations（异步，最长等待 10 分钟）")
    if not api_key:
        print("❌ 跳过：OPENAI_API_KEY 未配置")
        return False
    try:
        # 1) 提交
        submit = _post_json(
            f"{base}/videos/generations", api_key,
            {"model": model, "prompt": "A cat walking on a sunny beach, cinematic",
             "duration": 5, "resolution": "720p", "aspect_ratio": "16:9"},
            timeout=60,
        )
        task_id = submit.get("id")
        if not task_id:
            print(f"❌ 提交未返回任务 ID: {json.dumps(submit, ensure_ascii=False)[:300]}")
            return False
        print(f"  任务已提交 | task_id={task_id} | 轮询中...")

        # 2) 轮询
        query_url = f"{base}/videos/generations/{task_id}"
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(5)
            status = _get_json(query_url, api_key, timeout=60)
            st = status.get("status")
            if st == "succeeded":
                data = status.get("data") or []
                video_url = (data[0] if data else {}).get("video_url")
                if video_url:
                    print(f"✅ 成功 | model={model} | video_url={video_url[:80]}...")
                    return True
                print(f"❌ 成功但缺少 video_url: {json.dumps(status, ensure_ascii=False)[:300]}")
                return False
            elif st == "failed":
                err = status.get("error") or {}
                msg = err.get("message", "") if isinstance(err, dict) else str(err)
                print(f"❌ 任务失败: {msg}")
                return False
            else:
                print(f"  status={st} ... 等待中")

        print("❌ 轮询超时（10 分钟）")
        return False
    except Exception as e:
        print(f"❌ 失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="ModelHub 代理探针")
    parser.add_argument("--chat", action="store_true", help="真实调用 chat.completions（产生费用）")
    parser.add_argument("--image", action="store_true", help="真实调用 images/generations（产生费用）")
    parser.add_argument("--video", action="store_true", help="真实调用 videos/generations（产生费用，异步）")
    parser.add_argument("--all", action="store_true", help="同时验证 chat + image + video")
    args = parser.parse_args()

    _check_config()

    if args.all:
        args.chat = args.image = args.video = True

    results = []
    if args.chat:
        results.append(("chat", _probe_chat()))
    if args.image:
        results.append(("image", _probe_image()))
    if args.video:
        results.append(("video", _probe_video()))

    if results:
        print("\n" + "═" * 55)
        print("探针结果：")
        for name, success in results:
            print(f"  {name}: {'✅ 通过' if success else '❌ 失败'}")
        print("═" * 55)
    else:
        print("\n💡 本次为配置检查，未产生任何费用。加 --chat/--image/--video 可真实验证。")


if __name__ == "__main__":
    main()
