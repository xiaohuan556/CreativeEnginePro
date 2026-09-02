from pathlib import Path
from unittest.mock import patch
import wave

import pytest

from core.tts_cosyvoice import CosyVoiceEngine


class _Response:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return b"RIFF-test-wave"


class _PcmResponse(_Response):
    def read(self): return b"\x00\x00\x01\x00\x02\x00"


def test_cosyvoice_posts_official_zero_shot_multipart(tmp_path: Path):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF-reference")
    output = tmp_path / "result.wav"
    with patch("urllib.request.urlopen", return_value=_Response()) as request:
        result = CosyVoiceEngine("http://cosyvoice:50000").synthesize(
            "新的台词", str(reference), "参考录音文字", output, 1.2)
    sent = request.call_args.args[0]
    assert sent.full_url == "http://cosyvoice:50000/inference_zero_shot"
    assert b'name="tts_text"' in sent.data and "新的台词".encode() in sent.data
    assert b'name="prompt_text"' in sent.data and "参考录音文字".encode() in sent.data
    assert b'name="prompt_wav"' in sent.data
    assert result.read_bytes() == b"RIFF-test-wave"


def test_cosyvoice_requires_reference_transcript(tmp_path: Path):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF-reference")
    with pytest.raises(ValueError, match="参考录音内容"):
        CosyVoiceEngine("http://cosyvoice:50000").synthesize("台词", str(reference), "", tmp_path / "out.wav")


def test_cosyvoice_wraps_official_raw_pcm_as_wav(tmp_path: Path):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF-reference")
    output = tmp_path / "result.wav"
    with patch("urllib.request.urlopen", return_value=_PcmResponse()):
        CosyVoiceEngine("http://cosyvoice:50000").synthesize(
            "新的台词", str(reference), "参考录音文字", output)

    with wave.open(str(output), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 24_000
        assert wav_file.readframes(3) == b"\x00\x00\x01\x00\x02\x00"
