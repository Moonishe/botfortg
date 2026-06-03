import asyncio
import httpx
import logging
from io import BytesIO
from pathlib import Path

from src.db.repo import cache_transcript, get_cached_transcript
from src.db.session import get_session


logger = logging.getLogger(__name__)


async def _read_file_bytes(file_path: Path) -> bytes:
    """Read file bytes in a thread to avoid blocking the event loop."""

    def _read() -> bytes:
        with open(file_path, "rb") as f:
            return f.read()

    return await asyncio.to_thread(_read)


class TranscriptionService:
    """Локальный faster-whisper / OpenAI Whisper / Gemini / Mistral (multimodal) / hybrid."""

    def __init__(self, model_size: str = "small") -> None:
        self._model_size = model_size
        self._model = None
        self._model_failed = False
        self._lock = asyncio.Lock()

    async def _ensure_local_model(self) -> object | None:
        from src.config import settings

        if settings.disable_local_transcription:
            return None
        if self._model_failed:
            return None
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model
            if self._model_failed:
                return None
            try:
                from faster_whisper import WhisperModel

                def _load() -> object:
                    return WhisperModel(
                        self._model_size, device="auto", compute_type="auto"
                    )

                self._model = await asyncio.to_thread(_load)
            except Exception:
                logger.exception(
                    "Failed to load faster-whisper model, local transcription disabled"
                )
                self._model_failed = True
                self._model = None
        return self._model

    async def _transcribe_local(self, path: Path, language: str | None) -> str:
        model = await self._ensure_local_model()
        if model is None:
            raise RuntimeError("Local transcription unavailable (model not loaded)")

        def _run() -> str:
            segments, _info = model.transcribe(str(path), language=language)
            return " ".join(seg.text.strip() for seg in segments).strip()

        return await asyncio.to_thread(_run)

    async def _transcribe_openai(
        self, path: Path, openai_key: str, language: str | None
    ) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=openai_key,
            timeout=httpx.Timeout(120.0, connect=15.0),
        )
        try:
            file_bytes = await _read_file_bytes(path)
            resp = await client.audio.transcriptions.create(
                model="whisper-1",
                file=BytesIO(file_bytes),
                language=language,
            )
            return resp.text
        finally:
            await client.close()

    async def _transcribe_gemini(
        self, path: Path, gemini_key: str, language: str | None
    ) -> str:
        from google import genai

        client = genai.Client(api_key=gemini_key)

        def _run() -> str:
            audio_file = client.files.upload(file=str(path))
            prompt = "Transcribe this audio verbatim. Return only the transcription text, nothing else."
            if language:
                prompt += f" The audio language is {language}."
            resp = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=[prompt, audio_file],
            )
            return resp.text or ""

        return await asyncio.wait_for(
            asyncio.to_thread(_run),
            timeout=120.0,
        )

    async def _transcribe_mistral(
        self, path: Path, mistral_key: str, language: str | None
    ) -> str:
        import httpx

        suffix = path.suffix.lstrip(".") or "ogg"
        mime = f"audio/{suffix}" if suffix != "oga" else "audio/ogg"

        data = {
            "model": "voxtral-mini-transcribe-latest",
            "language": language or "ru",
            "diarize": "false",
            "temperature": "0",
            "stream": "false",
            "timestamp_granularities": "segment",
        }

        file_bytes = await _read_file_bytes(path)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.mistral.ai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {mistral_key}"},
                data=data,
                files={"file": (path.name, BytesIO(file_bytes), mime)},
            )
            resp.raise_for_status()
            return resp.json().get("text", "")

    async def _transcribe_custom(
        self,
        path: Path,
        provider_name: str,
        api_key: str,
        model: str,
        endpoint: str | None,
    ) -> str:
        """Transcribe via any STT provider using LlmKeySlot config."""
        from urllib.parse import urlparse
        import ipaddress as _ipaddress
        import socket as _socket

        def _check_stt_url(url: str) -> None:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if parsed.scheme != "https":
                raise ValueError(f"STT endpoint must use HTTPS, got: {parsed.scheme}")
            try:
                ip = _ipaddress.ip_address(host)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_reserved
                    or ip.is_link_local
                ):
                    raise ValueError(f"STT endpoint resolves to internal IP: {ip}")
            except ValueError as e:
                if "STT endpoint" in str(e):
                    raise
                # Not an IP — it's a domain, check DNS
                try:
                    addrinfo = _socket.getaddrinfo(host, None, _socket.AF_UNSPEC)
                    for family, _, _, _, sockaddr in addrinfo:
                        resolved = _ipaddress.ip_address(sockaddr[0])
                        if (
                            resolved.is_private
                            or resolved.is_loopback
                            or resolved.is_reserved
                        ):
                            raise ValueError(
                                f"STT endpoint DNS resolves to internal IP"
                            )
                except (_socket.gaierror, OSError):
                    pass

        import httpx

        url = (endpoint or "").rstrip("/")
        if not url:
            if provider_name == "deepgram":
                url = "https://api.deepgram.com/v1/listen"
            elif provider_name == "assemblyai":
                url = "https://api.assemblyai.com/v2/transcript"
            else:
                raise ValueError(f"No endpoint for {provider_name}")

        _check_stt_url(url)

        params: dict[str, str] = {}
        if model:
            params["model"] = model

        # Дефолтные настройки для Deepgram Nova-3 (русский, умное форматирование)
        if provider_name == "deepgram":
            params.setdefault("language", "ru")
            params.setdefault("smart_format", "true")
            params.setdefault("numerals", "true")
            params.setdefault("diarize", "true")
            params.setdefault("profanity_filter", "false")
            params.setdefault("punctuate", "true")
            params.setdefault("dictation", "false")

        file_bytes = await _read_file_bytes(path)
        async with httpx.AsyncClient(timeout=120.0) as client:
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = await client.post(
                url, headers=headers, params=params, content=file_bytes
            )
            resp.raise_for_status()
            data = resp.json()
            # Extract text — different providers have different response formats
            text = data.get("text", "")
            if not text:
                # Deepgram format
                results = data.get("results", {})
                channels = results.get("channels", [{}])
                if channels:
                    alternatives = channels[0].get("alternatives", [{}])
                    if alternatives:
                        text = alternatives[0].get("transcript", "")
            return text

    async def transcribe(
        self,
        path: Path,
        *,
        file_id: str | None = None,
        mode: str = "hybrid",
        openai_key: str | None = None,
        gemini_key: str | None = None,
        mistral_key: str | None = None,
        api_provider: str = "openai",
        language: str | None = None,
        custom_stt_key: str | None = None,
        custom_stt_model: str = "",
        custom_stt_endpoint: str | None = None,
    ) -> str:
        if file_id:
            async with get_session() as session:
                cached = await get_cached_transcript(session, file_id)
                if cached:
                    return cached

        text = ""
        if mode == "api":
            text = await self._call_api_transcribe(
                path,
                openai_key,
                gemini_key,
                mistral_key,
                api_provider,
                language,
                custom_stt_key=custom_stt_key,
                custom_stt_model=custom_stt_model,
                custom_stt_endpoint=custom_stt_endpoint,
            )
        elif mode == "local":
            text = await self._transcribe_local(path, language)
        else:  # hybrid
            try:
                text = await self._transcribe_local(path, language)
            except Exception:
                logger.exception("Local transcription failed, falling back to API")
                text = await self._call_api_transcribe(
                    path,
                    openai_key,
                    gemini_key,
                    mistral_key,
                    api_provider,
                    language,
                    custom_stt_key=custom_stt_key,
                    custom_stt_model=custom_stt_model,
                    custom_stt_endpoint=custom_stt_endpoint,
                )

        if file_id and text:
            async with get_session() as session:
                await cache_transcript(session, file_id, text)
        return text

    async def _call_api_transcribe(
        self,
        path: Path,
        openai_key: str | None,
        gemini_key: str | None,
        mistral_key: str | None,
        api_provider: str,
        language: str | None,
        custom_stt_key: str | None = None,
        custom_stt_model: str = "",
        custom_stt_endpoint: str | None = None,
    ) -> str:
        if api_provider in ("deepgram", "assemblyai"):
            key = custom_stt_key or openai_key or ""
            if not key:
                raise ValueError(f"API key required for {api_provider}")
            return await self._transcribe_custom(
                path, api_provider, key, custom_stt_model or "", custom_stt_endpoint
            )
        elif api_provider == "gemini":
            if not gemini_key:
                raise ValueError("Gemini API key required")
            return await self._transcribe_gemini(path, gemini_key, language)
        elif api_provider == "mistral":
            if not mistral_key:
                raise ValueError("Mistral API key required")
            return await self._transcribe_mistral(path, mistral_key, language)
        else:
            if not openai_key:
                raise ValueError("OpenAI API key required")
            return await self._transcribe_openai(path, openai_key, language)


transcription_service = TranscriptionService()


def get_transcription_meta(
    text: str, provider: str = "unknown", confidence: float = 0.0, language: str = "ru"
) -> dict:
    """Метаданные транскрипции для контекста модели."""
    return {
        "is_transcription": True,
        "provider": provider,
        "confidence": round(confidence, 2),
        "language": language,
        "length": len(text) if text else 0,
    }
