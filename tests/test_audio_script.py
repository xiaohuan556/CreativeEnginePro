import unittest

from ai.audio_script import parse_audio_script, spoken_text


class AudioScriptTest(unittest.TestCase):
    def test_pause_interjection_and_emotion_are_structured(self):
        parts, emotion = parse_audio_script("别走。[停顿:0.8][叹气][语气:克制]我还有话说。")
        self.assertEqual("克制", emotion)
        self.assertEqual("pause", parts[1]["type"])
        self.assertEqual(0.8, parts[1]["seconds"])
        self.assertEqual("叹气", parts[2]["interjection"])
        text, _ = spoken_text("等等[停顿:1][犹豫]也许可以")
        self.assertIn("…………", text)
        self.assertIn("嗯", text)


if __name__ == "__main__":
    unittest.main()
