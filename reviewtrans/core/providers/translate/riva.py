"""NVIDIA NIM (Riva NMT trên NVCF): dịch máy chuyên dụng, không bị chặn theo IP như Google free."""
from __future__ import annotations

import os
import threading
import time

from ...langs import riva_code
from .. import ProviderError
from .base import TranslateJob
from .machine import MachineTranslator

NVCF_BASE = "grpc.nvcf.nvidia.com:443"
DEFAULT_FUNCTION_ID = "0778f2eb-b64d-45e7-acae-7dd9b9b35b4d"  # riva-translate-1.6b
ENV_FUNCTION_ID = "RIVA_FUNCTION_ID"

# get_config trả về es-ES/es-US… → thử đúng mã trước rồi đến biến thể
_RIVA_VARIANTS = {"es": ["es-ES", "es-US"], "pt": ["pt-BR", "pt-PT"]}
_CALL_GAP = 0.2  # giãn cách giữa hai lần gọi NVCF


class RivaTranslator(MachineTranslator):
    """API key = NVIDIA_API_KEY (nvapi-…). Không key thì đọc biến môi trường cùng tên."""

    max_batch = 100
    _lock = threading.Lock()
    _last = 0.0

    def __init__(self, profile, model_override: str = "") -> None:
        super().__init__(profile, model_override)
        self._client = None

    def _auth(self):
        try:
            import riva.client
        except ImportError as exc:
            raise ProviderError("Chưa cài nvidia-riva-client (pip install nvidia-riva-client)") from exc

        key = self.keys.next()
        function_id = str(self.profile.option("function_id", os.environ.get(ENV_FUNCTION_ID, DEFAULT_FUNCTION_ID)))
        base = (self.profile.base_url or NVCF_BASE).rstrip("/")
        metadata = [("function-id", function_id)]
        if key:
            metadata.append(("authorization", f"Bearer {key}"))
        return riva.client.Auth(uri=base, use_ssl=not base.startswith("http://"), metadata_args=metadata)

    def _nmt(self):
        if self._client is None:
            import riva.client

            self._client = riva.client.NeuralMachineTranslationClient(self._auth())
        return self._client

    def _translate_many(self, texts: list[str], job: TranslateJob) -> list[str]:
        target = riva_code(job.target_lang)
        if not target:
            raise ProviderError("NVIDIA NIM cần ngôn ngữ đích cụ thể (không hỗ trợ 'auto').")
        source = riva_code(job.source_lang)
        if not source:
            raise ProviderError(f"NVIDIA NIM không hỗ trợ ngôn ngữ nguồn: {job.source_lang}")
        last: Exception | None = None
        # mã nội bộ (“es”) tra biến thể; không có thì dùng mã đã ánh xạ (“es-ES”)
        for src in _RIVA_VARIANTS.get(job.source_lang, [source]):
            try:
                return self._call(texts, src, target)
            except ProviderError as exc:  # cặp ngôn ngữ không có → thử biến thể kế tiếp
                last = exc
                if "ngôn ngữ" not in str(exc):
                    raise
        raise ProviderError(f"{self.profile.name}: không dịch được {source}→{target}: {last}")

    def _call(self, texts: list[str], source: str, target: str) -> list[str]:
        with RivaTranslator._lock:
            wait = _CALL_GAP - (time.time() - RivaTranslator._last)
            if wait > 0:
                time.sleep(wait)
            RivaTranslator._last = time.time()
        try:
            response = self._nmt().translate(texts=texts, model="", source_language=source, target_language=target)
        except Exception as exc:  # noqa: BLE001 - grpc ném nhiều loại lỗi khác nhau
            message = str(exc)
            if "Could not find a valid model" in message:
                raise ProviderError(f"NVIDIA NIM không có cặp ngôn ngữ {source}→{target}")
            raise ProviderError(f"NVIDIA NIM: {message[:300]}") from exc
        out = [item.text for item in response.translations]
        if len(out) != len(texts):
            raise ProviderError(f"NVIDIA NIM trả về {len(out)}/{len(texts)} câu")
        return out

    def test(self) -> str:
        return self.translate(["你好，世界"], TranslateJob(source_lang="zh", target_lang="vi"))[0]
