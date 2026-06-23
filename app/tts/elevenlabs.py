"""ElevenLabs TTS — Flash v2.5 스트리밍 (async). 출력 PCM 24kHz."""
from __future__ import annotations

from typing import AsyncIterator

import httpx

from ..config import ELEVENLABS_API_KEY, ELEVENLABS_MODEL, ELEVENLABS_VOICE_ID

SAMPLE_RATE = 24000


async def synthesize_stream(text: str) -> AsyncIterator[bytes]:
    """문장 텍스트 → PCM 24k 청크 스트림."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            url,
            params={"output_format": "pcm_24000"},
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            json={"text": text, "model_id": ELEVENLABS_MODEL},
        ) as r:
            if r.status_code != 200:
                await r.aread()
                raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
            async for chunk in r.aiter_bytes(4096):
                if chunk:
                    yield chunk
