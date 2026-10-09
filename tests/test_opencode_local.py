"""Test provider dịch qua server local của OpenCode (fake HTTP server)."""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from reviewtrans.core.config import ProviderProfile
from reviewtrans.core.providers import ProviderError
from reviewtrans.core.providers.translate import TranslateJob, create_translator
from reviewtrans.core.providers.translate.opencode_local import (
    OpenCodeLocalTranslator,
    _message_done,
    _message_text,
    read_service,
    service_alive,
)


class _FakeOpenCode(BaseHTTPRequestHandler):
    """Mô phỏng OpenCode local API: session/prompt/model/generate."""

    state: dict = {}

    def log_message(self, *_args) -> None:  # không in ra log pytest
        pass

    def _auth_ok(self) -> bool:
        """Trả401 khi mật khẩu Basic sai — mô phỏng đúng lỗi thật đã gặp."""
        expected = _FakeOpenCode.state.get("password")
        if not expected:
            return True
        want = "Basic " + base64.b64encode(f"opencode:{expected}".encode()).decode()
        if (self.headers.get("Authorization") or "") == want:
            return True
        _FakeOpenCode.state["unauthorized"] = _FakeOpenCode.state.get("unauthorized", 0) + 1
        return False

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def do_GET(self) -> None:  # noqa: N802
        if not self._auth_ok():
            return self._send(401, {"_tag": "UnauthorizedError", "message": "Authentication required"})
        parts = self.path.strip("/").split("/")
        if parts[:2] == ["api", "session"] and len(parts) == 3:
            if parts[2] in _FakeOpenCode.state["sessions"]:
                return self._send(200, {"id": parts[2]})
            return self._send(404, {"message": "not found"})
        if self.path.endswith("/message"):
            sid = parts[-2]
            msgs = _FakeOpenCode.state["messages"].get(sid, [])
            return self._send(200, {"data": msgs})
        if self.path.endswith("/model"):
            return self._send(200, {"data": [
                {"id": "big-pickle", "providerID": "opencode"},
                {"id": "mimo-v2.6-flash-free", "providerID": "opencode"},
            ]})
        return self._send(404, {"message": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = self._body()
        if not self._auth_ok():
            return self._send(401, {"_tag": "UnauthorizedError", "message": "Authentication required"})
        parts = self.path.strip("/").split("/")
        st = _FakeOpenCode.state
        if self.path == "/api/session":
            sid = f"ses_fake{len(st['sessions']) + 1}"
            st["sessions"][sid] = {"model": None}
            st["messages"][sid] = []
            return self._send(200, {"id": sid})
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "session":
            sid, action = parts[2], parts[3]
            if sid not in st["sessions"]:
                return self._send(404, {"message": "no session"})
            if action == "prompt":
                st["messages"][sid].append({"type": "user"})
                st["messages"][sid].append({"type": "assistant"})
                return self._send(200, {"data": {"id": "msg_1"}})
            if action == "model":
                st["sessions"][sid]["model"] = body.get("model", {}).get("id")
                return self._send(200, {})
            if action == "generate":
                if not st["messages"][sid]:
                    return self._send(200, {"data": {"text": ""}})
                st["generates"] += 1
                if st.get("empty_left"):
                    st["empty_left"] -= 1  # mô phỏng generate rỗng (session bận / model chỉ sinh reasoning)
                    return self._send(200, {"data": {"text": ""}})
                return self._send(200, {"data": {"text": st["reply"]}})
        return self._send(404, {"message": "not found"})


@pytest.fixture()
def fake_opencode(monkeypatch):
    _FakeOpenCode.state = {
        "sessions": {}, "messages": {}, "generates": 0, "empty_left": 0,
        "password": None, "unauthorized": 0,
        "reply": "1. Xin chào\n2. Hôm nay trời đẹp",
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenCode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    import reviewtrans.core.providers.translate.opencode_local as mod
    monkeypatch.setattr(mod, "_session_cache", {})
    yield server, _FakeOpenCode.state
    server.shutdown()
    server.server_close()


def _profile(url: str) -> ProviderProfile:
    return ProviderProfile(
        name="OpenCode", kind="opencode", base_url=url, model="big-pickle", api_keys=["fake-pw"]
    )


def test_create_translator_kind(fake_opencode):
    server, _state = fake_opencode
    tr = create_translator(_profile(f"http://127.0.0.1:{server.server_address[1]}"))
    assert isinstance(tr, OpenCodeLocalTranslator)


def test_translate_batch_via_fake_server(fake_opencode):
    server, state = fake_opencode
    url = f"http://127.0.0.1:{server.server_address[1]}"
    tr = create_translator(_profile(url))
    out = tr.translate(
        ["你好", "今天天气不错"],
        TranslateJob(source_lang="zh", target_lang="vi"),
    )
    assert out == ["Xin chào", "Hôm nay trời đẹp"]
    # warm-up 1 lần, generate gọi thật, model được set đúng
    assert state["generates"] == 1
    assert list(state["sessions"].values())[0]["model"] == "big-pickle"


def test_translate_reuses_session(fake_opencode):
    server, state = fake_opencode
    url = f"http://127.0.0.1:{server.server_address[1]}"
    tr = create_translator(_profile(url))
    job = TranslateJob(source_lang="zh", target_lang="vi")
    tr.translate(["你好"], job)
    tr.translate(["再见"], job)
    assert len(state["sessions"]) == 1  # không tạo session mới khi còn dùng được


def test_empty_reply_raises_and_drops_session(fake_opencode, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)  # không chờ retry thật
    server, state = fake_opencode
    state["reply"] = ""
    url = f"http://127.0.0.1:{server.server_address[1]}"
    tr = create_translator(_profile(url))
    with pytest.raises(ProviderError):
        tr.translate(["你好"], TranslateJob(source_lang="zh", target_lang="vi"))


def test_generate_rong_mot_lan_van_phuc_hoi(fake_opencode, monkeypatch):
    """generate trả text rỗng một lần (session bận / model chỉ sinh reasoning) → tự generate lại."""
    monkeypatch.setattr("time.sleep", lambda _s: None)  # không chờ retry thật
    server, state = fake_opencode
    state["empty_left"] = 1
    url = f"http://127.0.0.1:{server.server_address[1]}"
    tr = create_translator(_profile(url))
    out = tr.translate(
        ["你好", "今天天气不错"],
        TranslateJob(source_lang="zh", target_lang="vi"),
    )
    assert out == ["Xin chào", "Hôm nay trời đẹp"]
    assert state["generates"] == 2  # lượt rỗng + lượt thành công, cùng một session


def test_message_done_va_text():
    # bản cũ không có ``time`` → coi là xong ngay
    assert _message_done({"type": "assistant"}) is True
    # còn stream (mới có ``created``) → chưa xong, generate lúc này sẽ ra rỗng
    assert _message_done({"type": "assistant", "time": {"created": 1}}) is False
    assert _message_done({"type": "assistant", "time": {"created": 1, "completed": 2}}) is True
    assert _message_done({"type": "idle"}) is True
    assert _message_done({"type": "user"}) is False
    # chỉ lấy text thật, reasoning/tool không vào bản dịch
    assert _message_text({"content": [{"type": "reasoning", "text": "suy nghĩ"}, {"type": "text", "text": "dịch"}]}) == "dịch"
    assert _message_text({"content": [{"type": "reasoning", "text": "suy nghĩ"}]}) == ""
    assert _message_text({"text": "bản cũ"}) == "bản cũ"


def test_read_service_uu_tien_file_co_url(tmp_path, monkeypatch):
    """File stale chỉ còn password không được đè url/password của file server đang ghi."""
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps({"password": "cu"}))
    fresh = tmp_path / "fresh.json"
    fresh.write_text(json.dumps({"url": "http://127.0.0.1:12345", "password": "moi", "pid": 7}))
    monkeypatch.setattr(
        "reviewtrans.core.providers.translate.opencode_local.service_paths",
        lambda: [stale, fresh],
    )
    data = read_service()
    assert data["url"] == "http://127.0.0.1:12345"
    assert data["password"] == "moi"


def test_key_sai_401_thu_lai_bang_mat_khau_service(fake_opencode, monkeypatch, tmp_path):
    """Ô API key bị dán nhầm key `nvapi-…` →401, phải tự rơi về mật khẩu local từ service.json."""
    monkeypatch.setattr("time.sleep", lambda _s: None)  # không chờ retry thật
    server, state = fake_opencode
    url = f"http://127.0.0.1:{server.server_address[1]}"
    state["password"] = "mat-khau-local"
    svc = tmp_path / "service.json"
    svc.write_text(json.dumps({"url": url, "password": "mat-khau-local"}))
    monkeypatch.setattr(
        "reviewtrans.core.providers.translate.opencode_local.service_paths",
        lambda: [svc],
    )
    profile = ProviderProfile(
        name="OpenCode (máy cục bộ) mới", kind="opencode", base_url=url,
        model="big-pickle", api_keys=["nvapi-KeyDanNhamVaoOTKey"],
    )
    tr = create_translator(profile)
    out = tr.translate(["你好"], TranslateJob(source_lang="zh", target_lang="vi"))
    assert out == ["Xin chào"]
    assert state["unauthorized"] >= 1  # key sai bị từ chối thật, sau đó tự dùng mật khẩu local


def test_missing_server_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("time.sleep", lambda _s: None)  # không chờ retry thật
    monkeypatch.setattr(
        "reviewtrans.core.providers.translate.opencode_local.service_paths",
        lambda: [tmp_path / "nope.json"],
    )
    tr = create_translator(ProviderProfile(name="OpenCode", kind="opencode", base_url=""))
    with pytest.raises(ProviderError, match="OpenCode"):
        tr.translate(["你好"], TranslateJob(source_lang="zh", target_lang="vi"))


def test_read_service_and_alive(tmp_path, monkeypatch):
    svc = tmp_path / "service.json"
    svc.write_text(json.dumps({"url": "http://127.0.0.1:12345", "password": "pw", "pid": 0}))
    monkeypatch.setattr(
        "reviewtrans.core.providers.translate.opencode_local.service_paths",
        lambda: [svc],
    )
    data = read_service()
    assert data["url"] == "http://127.0.0.1:12345"
    # không có pid nhưng có url -> coi là alive (theo hợp đồng service.json)
    assert service_alive(data) is True
