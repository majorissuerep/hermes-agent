"""Tests for the shared magic-byte audio container sniffer.

``tools/audio_container.py`` is the single owner of container detection:
the outbound TTS repair (``tools/tts_tool.py``), the inbound gateway audio
cache (``gateway/platforms/base.py``), and Signal's ``_guess_extension``
all delegate to it. These tests cover every magic-byte branch, the
wrong-extension repair behaviour on the inbound cache path, and unknown
passthrough.
"""

import os
from unittest.mock import patch

import pytest

from tools.audio_container import CONTAINER_TO_EXT, sniff_audio_ext, sniff_container

# --- canonical headers ------------------------------------------------------
OGG = b"OggS\x00\x02" + b"\x00" * 64
FLAC = b"fLaC" + b"\x00" * 64
WAV = b"RIFF\x24\x08\x00\x00WAVEfmt " + b"\x00" * 64
WEBP = b"RIFF\x24\x08\x00\x00WEBPVP8 " + b"\x00" * 64
MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64
MP3_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 64
AAC_ADTS = b"\xff\xf1\x50\x80" + b"\x00" * 64
M4A = b"\x00\x00\x00\x1cftypM4A " + b"\x00" * 64
M4B = b"\x00\x00\x00\x1cftypM4B " + b"\x00" * 64
MP4_ISOM = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
UNKNOWN = b"not-audio-at-all" + b"\x00" * 64


class TestSniffContainer:
    @pytest.mark.parametrize(
        "data,expected",
        [
            (OGG, "ogg"),
            (FLAC, "flac"),
            (WAV, "wav"),
            (MP3_ID3, "mp3"),
            (MP3_FRAME, "mp3"),
            (AAC_ADTS, "aac"),
            (M4A, "m4a"),
            (M4B, "m4a"),
            (MP4_ISOM, "mp4"),
            (WEBM, "webm"),
        ],
    )
    def test_magic_bytes(self, data, expected):
        assert sniff_container(data) == expected


    def test_every_container_has_an_extension(self):
        for data in (OGG, FLAC, WAV, MP3_ID3, AAC_ADTS, M4A, MP4_ISOM, WEBM):
            container = sniff_container(data)
            assert container in CONTAINER_TO_EXT


class TestSniffAudioExt:
    @pytest.mark.parametrize(
        "data,expected",
        [
            (OGG, ".ogg"),
            (FLAC, ".flac"),
            (WAV, ".wav"),
            (MP3_ID3, ".mp3"),
            (MP3_FRAME, ".mp3"),
            (AAC_ADTS, ".aac"),
            (M4A, ".m4a"),
            (WEBM, ".webm"),
        ],
    )
    def test_container_wins_over_claimed_ext(self, data, expected):
        assert sniff_audio_ext(data, ".ogg" if expected != ".ogg" else ".mp3") == expected


    def test_fallback_without_dot_is_normalized(self):
        assert sniff_audio_ext(UNKNOWN, "mp3") == ".mp3"


class TestInboundCacheUsesSniffer:
    """cache_audio_from_bytes must repair wrong caller-supplied extensions."""

    @pytest.mark.parametrize(
        "data,claimed,expected_suffix",
        [
            (MP3_ID3, ".ogg", ".mp3"),   # MP3 bytes delivered as "voice.ogg"
            (WAV, ".ogg", ".wav"),        # RIFF/WAVE in an .ogg wrapper claim
            (M4A, ".ogg", ".m4a"),        # iOS voice note claimed as ogg
            (OGG, ".mp3", ".ogg"),        # real opus claimed as mp3
            (AAC_ADTS, ".ogg", ".aac"),   # Android ADTS AAC voice note
            (WEBM, ".ogg", ".webm"),
            (FLAC, ".mp3", ".flac"),
        ],
    )
    def test_wrong_ext_repaired(self, tmp_path, data, claimed, expected_suffix):
        from tools.media_cache import cache_audio_from_bytes

        with patch("tools.media_cache.get_hermes_dir", return_value=tmp_path):
            result = cache_audio_from_bytes(data, ext=claimed)

        saved = tmp_path / os.path.basename(result)
        assert saved.suffix == expected_suffix
        assert saved.read_bytes() == data




class TestSignalDelegatesToCentralSniffer:
    """signal._guess_extension audio branches delegate to the shared module."""
