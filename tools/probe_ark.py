# -*- coding: utf-8 -*-
"""
Ark（火山方舟）接入探针 —— 验证 Seedream / Seedance 真实调用是否跑通。

用法：
    python tools/probe_ark.py

前置条件（已在 .env 配好）：
    SEEDREAM_API_KEY=你的真实 Ark API Key（Bearer，即你给的 ark-...）

说明：
    * 豆包模型共用同一个 Key，无需为每个模型单独申请，也无需自建推理接入点。
    * 模型用「发布版模型 ID」（已内置在 api_config 的 default_model）：
        Seedream 5.0 Pro → doubao-seedream-5-0-pro-260628
        Seedance 2.0     → doubao-seedance-2-0-260128
      若你的账号模型 ID 与内置不同，可在 .env 用 SEEDREAM_MODEL / SEEDANCE_MODEL 覆盖。
    * 本脚本直接构造 Provider 调 execute()，会真实计费，请谨慎。
"""
from __future__ import annotations

import os
import sys
import time

# 把项目根加入 path，使 `import api_config` / `import config` 可用
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import api_config  # 触发 .env 载入（_ensure_env）
from api_config import get as ac_get

from ai.providers.image.seedream import SeedreamProvider
from ai.providers.video.veo import SeedanceProvider
from ai.providers.base import TaskRequest, TaskStatus


def _check_key() -> str:
    key = os.environ.get("SEEDREAM_API_KEY", "")
    if not key:
        print("✗ 未检测到 SEEDREAM_API_KEY。")
        print("  请在项目根 .env 中加入： SEEDREAM_API_KEY=你的Ark密钥(ark-...)")
        sys.exit(1)
    return key


def _model(name: str) -> str:
    """模型 ID：优先 .env 的 <NAME>_MODEL，其次 api_config 内置默认。"""
    env = os.environ.get(f"{name.upper()}_MODEL", "").strip()
    if env:
        return env
    return ac_get(name).default_model or ""


def _report(name: str, handle) -> bool:
    ok = handle.is_success
    if ok:
        data = handle.result.data
        print(f"  ✓ {name} 成功 → {data}")
    else:
        print(f"  ✗ {name} 失败 → {handle.result.error if handle.result else '未知'}")
    return ok


def probe_seedream(key: str) -> bool:
    print("\n[1/2] Seedream 文生图（提示词：一只戴墨镜的橘猫，海边日落，超写实）")
    p = SeedreamProvider(api_key=key)
    req = TaskRequest(
        operation="text_to_image",
        inputs={"prompt": "一只戴着墨镜的橘猫，坐在海边，日落，超写实摄影风格。"},
        params={"size": "2K", "output_format": "png", "watermark": False},
    )
    t0 = time.time()
    h = p.execute(req)
    print(f"      用时 {time.time()-t0:.1f}s")
    return _report("Seedream", h)


def probe_seedance(key: str) -> bool:
    print("\n[2/2] Seedance 文生视频（提示词：海浪拍打礁石，慢镜头，电影感）")
    print("      （视频为异步任务，最长轮询 10 分钟，请耐心等待…）")
    p = SeedanceProvider(api_key=key)
    req = TaskRequest(
        operation="text_to_video",
        inputs={"prompt": "海浪缓缓拍打礁石，慢镜头，电影感，自然光。"},
        params={"duration": 5, "ratio": "16:9", "generate_audio": True, "watermark": False},
    )
    t0 = time.time()
    h = p.execute(req)
    print(f"      用时 {time.time()-t0:.1f}s")
    return _report("Seedance", h)


def main():
    print("=" * 56)
    print("Ark 接入探针  ·  Seedream 5.0 Pro / Seedance 2.0")
    print("=" * 56)
    m_sd = _model("seedream")
    m_sv = _model("seedance")
    print(f"  Seedream 模型 ID : {m_sd or '(缺失)'}")
    print(f"  Seedance 模型 ID : {m_sv or '(缺失)'}")
    if not m_sd or not m_sv:
        print("  ✗ 模型 ID 缺失，请检查 api_config 内置默认或 .env 的 *_MODEL 覆盖。")
        sys.exit(1)

    key = _check_key()
    print(f"  Ark API Key      : 已配置 ({key[:6]}...)")

    r1 = probe_seedream(key)
    r2 = probe_seedance(key)

    print("\n" + "=" * 56)
    print("结论：")
    print(f"  Seedream  : {'OK' if r1 else 'FAIL'}")
    print(f"  Seedance  : {'OK' if r2 else 'FAIL'}")
    print("=" * 56)
    sys.exit(0 if (r1 and r2) else 2)


if __name__ == "__main__":
    main()
