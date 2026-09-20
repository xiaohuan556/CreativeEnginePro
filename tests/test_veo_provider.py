import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai.providers.ark_http import ArkHTTPError, _http_error_message
from ai.providers.base import TaskRequest
from ai.providers.video.veo import (
    SeedanceProvider, VeoProvider, _seedance_generate_audio,
)
from ai.providers.video.seedance_models import (
    SEEDANCE_20_MODEL, SEEDANCE_25_MODEL, seedance_model_profile,
)


OPERATION = (
    "projects/test/locations/global/publishers/google/models/"
    "veo-3.1-generate-001/operations/test-operation"
)


def _done(video: bytes = b"fake-mp4") -> dict:
    return {
        "name": OPERATION,
        "done": True,
        "response": {
            "generateVideoResponse": {
                "generatedSamples": [{
                    "video": {
                        "encodedVideo": base64.b64encode(video).decode("ascii"),
                        "encoding": "base64",
                    }
                }]
            }
        },
    }


class VeoProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name) / "outputs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.operation_log = Path(self.temp_dir.name) / "operations.json"
        self.provider = VeoProvider(
            api_key="sk-test",
            model="veo-3.1-generate-preview",
            base_url="https://modelhub.example/api/v1",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _request(self, **params) -> TaskRequest:
        values = {
            "duration": 4,
            "ratio": "16:9",
            "resolution": "720p",
            "generate_audio": True,
        }
        values.update(params)
        return TaskRequest(
            operation="text_to_video",
            inputs={"prompt": "A paper boat moves across calm water"},
            params=values,
        )

    def _patches(self, get_side_effect):
        return (
            patch("ai.providers.video.veo.ark_post", return_value={"name": OPERATION}),
            patch("ai.providers.video.veo.ark_get", side_effect=get_side_effect),
            patch("ai.providers.video.veo.wait", return_value=None),
            patch.object(self.provider, "_out_dir", return_value=self.output_dir),
            patch.dict(os.environ, {
                "CEP_VEO_OPERATION_LOG": str(self.operation_log),
            }),
        )

    def test_ark_privacy_http_json_is_rendered_as_actionable_chinese(self):
        raw = json.dumps({"error": {
            "code":"InputImageSensitiveContentDetected.PrivacyInformation",
            "message":"The request failed because the input image may contain real person.",
        }})
        message = _http_error_message(400, raw)
        self.assertIn("真人隐私保护", message)
        self.assertNotIn('"error"', message)

    def test_seedance_audio_defaults_on_and_respects_explicit_choice(self):
        self.assertTrue(_seedance_generate_audio({}))
        self.assertTrue(_seedance_generate_audio({"generate_audio": True}))
        self.assertFalse(_seedance_generate_audio({"generate_audio": False}))

    def test_seedance_25_profile_exposes_30_second_limit(self):
        old = seedance_model_profile(SEEDANCE_20_MODEL)
        new = seedance_model_profile(SEEDANCE_25_MODEL)
        self.assertEqual(15, old["max_duration"])
        self.assertEqual(30, new["max_duration"])
        self.assertEqual(30, new["reference_images"])

    def test_seedance_20_rejects_30_seconds_before_paid_submit(self):
        provider = SeedanceProvider(api_key="ark-test", model=SEEDANCE_20_MODEL)
        request = TaskRequest(
            operation="text_to_video", inputs={"prompt": "测试"},
            params={"model": SEEDANCE_20_MODEL, "duration": 30, "resolution": "720p"})
        with patch("ai.providers.video.veo.ark_post") as mock_post:
            handle = provider.execute(request)
        self.assertFalse(handle.is_success)
        self.assertIn("仅支持 4–15 秒", handle.result.error)
        mock_post.assert_not_called()

    def test_seedance_rejects_square_ratio_before_paid_submit(self):
        provider = SeedanceProvider(api_key="ark-test", model=SEEDANCE_25_MODEL)
        request = TaskRequest(
            operation="text_to_video", inputs={"prompt": "测试"},
            params={"model": SEEDANCE_25_MODEL, "duration": 5,
                    "ratio": "1:1", "resolution": "720p"})
        with patch("ai.providers.video.veo.ark_post") as mock_post:
            handle = provider.execute(request)
        self.assertFalse(handle.is_success)
        self.assertIn("不支持画面比例 1:1", handle.result.error)
        mock_post.assert_not_called()

    def test_seedance_local_reference_video_is_submitted_as_web_url(self):
        source = Path(self.temp_dir.name) / "reference.mp4"
        source.write_bytes(b"local-video-bytes")
        provider = SeedanceProvider(
            api_key="ark-test", model=SEEDANCE_25_MODEL,
            base_url="https://video.example/v3")
        request = TaskRequest(
            operation="video_edit",
            inputs={
                "prompt":"保持主体，修改镜头运动",
                "reference_assets":[{
                    "path":str(source), "role":"reference_video",
                }],
            },
            params={
                "model":SEEDANCE_25_MODEL, "duration":5,
                "ratio":"adaptive", "resolution":"720p",
            })
        succeeded = {
            "status":"succeeded",
            "content":{"video_url":"https://example.test/result.mp4"},
        }
        with (
            patch(
                "ai.providers.video.provider_media_relay.publish_local_media",
                return_value="https://relay.example/reference.mp4") as publish,
            patch("ai.providers.video.veo.ark_post", return_value={"id":"task-1"}) as post,
            patch("ai.providers.video.veo.ark_get", return_value=succeeded),
            patch("ai.providers.video.veo.download"),
            patch.object(provider, "_out_dir", return_value=self.output_dir),
        ):
            handle = provider.execute(request)

        self.assertTrue(handle.is_success, handle.result.error)
        payload = post.call_args.args[2]
        video_item = next(
            item for item in payload["content"]
            if item.get("type") == "video_url")
        self.assertEqual(
            "https://relay.example/reference.mp4",
            video_item["video_url"]["url"])
        publish.assert_called_once_with(source)

    def test_seedance_keeps_remote_and_asset_media_urls_unchanged(self):
        self.assertEqual(
            "https://example.test/reference.mp4",
            SeedanceProvider._remote_media_url(
                "https://example.test/reference.mp4", "参考视频"))
        self.assertEqual(
            "asset://asset-123",
            SeedanceProvider._remote_media_url(
                "asset://asset-123", "参考视频"))

    def test_seedance_timestamp_edit_hard_locks_downloaded_result(self):
        source = Path(self.temp_dir.name) / "original.mp4"
        source.write_bytes(b"original-video")
        provider = SeedanceProvider(
            api_key="ark-test", model=SEEDANCE_25_MODEL,
            base_url="https://video.example/v3")
        request = TaskRequest(
            operation="video_edit",
            inputs={
                "prompt":"只修改选定范围",
                "reference_assets":[{
                    "path":str(source), "role":"reference_video",
                }],
            },
            params={
                "model":SEEDANCE_25_MODEL, "duration":-1,
                "ratio":"adaptive", "resolution":"720p",
                "video_edit_hard_lock":True,
                "video_edit_source_path":str(source),
                "video_edit_start":2.0, "video_edit_end":6.0,
                "video_edit_scope":"element",
                "video_edit_region":{
                    "x":0.1, "y":0.2, "width":0.3, "height":0.4},
            })
        succeeded = {
            "status":"succeeded",
            "content":{"video_url":"https://example.test/result.mp4"},
        }
        lock_metadata = {
            "applied":True, "outside_time_preserved":True,
            "outside_region_preserved":True,
        }

        def fake_download(_url, target, timeout=0):
            Path(target).write_bytes(b"generated-video")

        with (
            patch(
                "ai.providers.video.provider_media_relay.publish_local_media",
                return_value="https://relay.example/reference.mp4"),
            patch("ai.providers.video.veo.ark_post", return_value={"id":"task-1"}),
            patch("ai.providers.video.veo.ark_get", return_value=succeeded),
            patch("ai.providers.video.veo.download", side_effect=fake_download),
            patch(
                "ai.providers.video.timestamp_edit_lock.enforce_timestamp_edit_lock",
                return_value=lock_metadata) as enforce,
            patch.object(provider, "_out_dir", return_value=self.output_dir),
        ):
            handle = provider.execute(request)

        self.assertTrue(handle.is_success, handle.result.error)
        enforce.assert_called_once()
        args = enforce.call_args.args
        self.assertEqual(source, Path(args[0]))
        self.assertEqual(2.0, enforce.call_args.kwargs["start"])
        self.assertEqual(6.0, enforce.call_args.kwargs["end"])
        self.assertEqual(
            lock_metadata, handle.result.provider_raw["timestamp_edit_lock"])

    def test_transient_poll_failures_do_not_duplicate_submit(self):
        patches = self._patches([
            TimeoutError("timed out"),
            ArkHTTPError("temporary 502", status=502),
            {"name": OPERATION, "done": False},
            _done(b"video-after-retry"),
        ])
        with patches[0] as mock_post, patches[1] as mock_get, patches[2], patches[3], patches[4]:
            handle = self.provider.execute(self._request())

        self.assertTrue(handle.is_success, handle.result.error if handle.result else "")
        self.assertEqual(1, mock_post.call_count)
        self.assertEqual(4, mock_get.call_count)
        self.assertEqual(b"video-after-retry", Path(handle.result.data).read_bytes())
        self.assertEqual(OPERATION, handle.result.provider_raw["operation"])
        records = json.loads(self.operation_log.read_text(encoding="utf-8"))
        self.assertEqual("done", records[OPERATION]["status"])

    def test_done_with_empty_samples_is_rechecked_without_resubmit(self):
        empty = {
            "name": OPERATION,
            "done": True,
            "response": {"generateVideoResponse": {"generatedSamples": []}},
        }
        patches = self._patches([empty, empty, _done(b"late-video")])
        with patches[0] as mock_post, patches[1] as mock_get, patches[2], patches[3], patches[4]:
            handle = self.provider.execute(self._request())

        self.assertTrue(handle.is_success, handle.result.error if handle.result else "")
        self.assertEqual(1, mock_post.call_count)
        self.assertEqual(3, mock_get.call_count)
        self.assertEqual(b"late-video", Path(handle.result.data).read_bytes())

    def test_repeated_empty_result_fails_without_second_submit(self):
        empty = {
            "name": OPERATION,
            "done": True,
            "response": {"generateVideoResponse": {"generatedSamples": []}},
        }
        patches = self._patches([empty] * 5)
        with patches[0] as mock_post, patches[1] as mock_get, patches[2], patches[3], patches[4]:
            handle = self.provider.execute(self._request())

        self.assertFalse(handle.is_success)
        self.assertEqual(1, mock_post.call_count)
        self.assertEqual(5, mock_get.call_count)
        self.assertIn(OPERATION, handle.result.error)

    def test_existing_operation_can_resume_without_post_or_prompt(self):
        request = TaskRequest(
            operation="text_to_video",
            metadata={"veo_operation": OPERATION},
        )
        patches = self._patches([_done(b"resumed-video")])
        with patches[0] as mock_post, patches[1], patches[2], patches[3], patches[4]:
            handle = self.provider.execute(request)

        self.assertTrue(handle.is_success, handle.result.error if handle.result else "")
        mock_post.assert_not_called()
        self.assertEqual(b"resumed-video", Path(handle.result.data).read_bytes())

    def test_unsupported_ratio_fails_before_paid_submit(self):
        patches = self._patches([])
        with patches[0] as mock_post, patches[1], patches[2], patches[3], patches[4]:
            handle = self.provider.execute(self._request(ratio="1:1"))

        self.assertFalse(handle.is_success)
        self.assertIn("仅支持 16:9 或 9:16", handle.result.error)
        mock_post.assert_not_called()

    def test_payload_includes_resolution_and_negative_prompt(self):
        request = self._request(duration=6, resolution="1080p")
        request.inputs["negative_prompt"] = "flicker, subtitles"
        patches = self._patches([_done()])
        with patches[0] as mock_post, patches[1], patches[2], patches[3], patches[4]:
            handle = self.provider.execute(request)

        self.assertTrue(handle.is_success, handle.result.error if handle.result else "")
        payload = mock_post.call_args.args[2]
        params = payload["parameters"]
        self.assertEqual("1080p", params["resolution"])
        self.assertEqual(8, params["durationSeconds"])
        self.assertEqual("flicker, subtitles", params["negativePrompt"])

    def test_paid_submit_uses_the_task_locked_model(self):
        request = self._request(model="locked-veo-model")
        patches = self._patches([_done()])
        with patches[0] as mock_post, patches[1], patches[2], patches[3], patches[4]:
            handle = self.provider.execute(request)

        self.assertTrue(handle.is_success, handle.result.error if handle.result else "")
        self.assertIn("/locked-veo-model:predictLongRunning", mock_post.call_args.args[0])

    def test_typed_asset_references_use_veo_ingredients(self):
        refs = []
        for index, role in enumerate(("scene", "character", "element", "style")):
            path = Path(self.temp_dir.name) / f"{role}.png"
            path.write_bytes(b"fake-image-" + bytes([index]))
            refs.append({
                "path": str(path), "role": role,
                "asset_id": f"asset-{index}", "label": role,
                "priority": index + 10,
            })
        request = self._request(duration=4)
        request.inputs["reference_assets"] = refs
        patches = self._patches([_done()])
        with patches[0] as mock_post, patches[1], patches[2], patches[3], patches[4]:
            handle = self.provider.execute(request)

        self.assertTrue(handle.is_success, handle.result.error if handle.result else "")
        payload = mock_post.call_args.args[2]
        ingredients = payload["instances"][0]["referenceImages"]
        self.assertEqual(3, len(ingredients))
        self.assertTrue(all(item["referenceType"] == "asset" for item in ingredients))
        self.assertEqual(8, payload["parameters"]["durationSeconds"])


if __name__ == "__main__":
    unittest.main()
