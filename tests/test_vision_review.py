import json
import tempfile
import unittest
from pathlib import Path

from ai.vision_review import build_review_messages, parse_review


class VisionReviewTests(unittest.TestCase):
    def test_build_messages_preserves_typed_reference_and_candidate_order(self):
        with tempfile.TemporaryDirectory() as temp:
            reference = Path(temp) / "character.png"
            candidate = Path(temp) / "candidate.png"
            reference.write_bytes(b"reference")
            candidate.write_bytes(b"candidate")
            messages = build_review_messages(
                {"scene": "A robot waves"}, [str(candidate)], [{
                    "path": str(reference), "role": "character",
                    "asset_id": "robot", "label": "white robot"}])
            content = messages[1]["content"]
            text = "\n".join(item.get("text", "") for item in content)
            self.assertIn("role=character", text)
            self.assertIn("asset_id=robot", text)
            self.assertIn("CANDIDATE 1", text)
            self.assertEqual(2, sum(
                1 for item in content if item.get("type") == "image_url"))

    def test_parse_review_accepts_fenced_json(self):
        payload = {"candidates": [{
            "index": 1, "decision": "fail", "confidence": 0.92,
            "missing_assets": ["phone"], "identity_errors": ["wrong robot"],
            "reason": "reference mismatch",
        }]}
        rows = parse_review(
            "```json\n" + json.dumps(payload) + "\n```", 1)
        self.assertEqual("fail", rows[0]["decision"])
        self.assertEqual(["phone"], rows[0]["missing_assets"])


if __name__ == "__main__":
    unittest.main()
