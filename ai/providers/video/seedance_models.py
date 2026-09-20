"""Seedance model capabilities shared by desktop and server adapters.

The provider remains ``seedance`` because all variants use the same Ark API
key and task endpoint.  Model-specific limits live here so choosing 2.5 never
silently changes the behaviour of an existing 2.0 project.
"""
from __future__ import annotations


SEEDANCE_20_MODEL = "doubao-seedance-2-0-260128"
SEEDANCE_25_MODEL = "doubao-seedance-2-5-260628"

SEEDANCE_MODEL_PROFILES: tuple[dict, ...] = (
    {
        "id": SEEDANCE_20_MODEL,
        "label": "Seedance 2.0",
        "min_duration": 4,
        "max_duration": 15,
        "durations": [4, 5, 6, 8, 10, 12, 15],
        "ratios": ["adaptive", "16:9", "9:16"],
        "reference_assets": 9,
        "reference_images": 9,
        "reference_videos": 3,
        "reference_audios": 3,
        "resolutions": ["720p", "1080p"],
        "native_audio": True,
        "supports_multi_shot": True,
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_extend": False,
        "supports_video_edit": False,
    },
    {
        "id": SEEDANCE_25_MODEL,
        "label": "Seedance 2.5",
        "min_duration": 4,
        "max_duration": 30,
        "durations": [4, 5, 6, 8, 10, 12, 15, 20, 25, 30],
        "ratios": ["adaptive", "16:9", "9:16"],
        "reference_assets": 30,
        "reference_images": 30,
        "reference_videos": 10,
        "reference_audios": 10,
        "resolutions": ["480p", "720p"],
        "native_audio": True,
        "supports_multi_shot": True,
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_extend": True,
        "supports_video_edit": True,
    },
)


def seedance_model_profile(model: str = "") -> dict:
    """Return a copy of the closest known profile for an endpoint ID."""
    normalized = str(model or "").strip().lower()
    for profile in SEEDANCE_MODEL_PROFILES:
        if normalized == str(profile["id"]).lower():
            return dict(profile)
    # Support gateway aliases without making them the persisted Ark ID.
    if "2.5" in normalized or "2-5" in normalized or "2_5" in normalized:
        return dict(SEEDANCE_MODEL_PROFILES[1])
    return dict(SEEDANCE_MODEL_PROFILES[0])


def seedance_models_payload() -> list[dict]:
    return [dict(profile) for profile in SEEDANCE_MODEL_PROFILES]
