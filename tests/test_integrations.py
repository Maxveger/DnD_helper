import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from dnd_helper.providers import OpenAIProvider
from dnd_helper.rules import GameError
from dnd_helper.service import Service
from dnd_helper.web import create_app


@pytest.fixture
def service(tmp_path):
    s = Service(tmp_path)
    s.engine.command("new", {"characters": ["mira"], "demo": True})
    s.engine.command("start", {})
    return s


def test_api_requires_local_token_and_origin(tmp_path):
    app = create_app(tmp_path, background=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/state").status_code == 403
        headers = {"X-Dnd-Token": app.state.csrf}
        assert client.get("/api/state", headers=headers).status_code == 200
        assert client.get("/api/state", headers={**headers, "Origin": "https://evil.test"}).status_code == 403
        assert client.get("/", headers={"Host": "evil.test"}).status_code == 403
        assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_settings_never_return_secrets_and_game_export_omits_bindings(tmp_path):
    app = create_app(tmp_path, background=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        h = {"X-Dnd-Token": app.state.csrf}
        response = client.post(
            "/api/settings", json={"openai_key": "secret-api", "telegram_token": "secret-bot"}, headers=h
        )
        assert response.status_code == 200
        assert "secret-api" not in response.text and "secret-bot" not in response.text
        assert "secret-api" not in client.get("/api/state", headers=h).text
        client.post("/api/settings", json={"openai_key": ""}, headers=h)
        assert app.state.service.config.get().openai_key == "secret-api"
        app.state.service.engine.command("new", {"characters": ["mira"], "demo": False})
        response = client.get("/api/export", headers=h)
        for key in ("gm_invite", "participants", "secret-api", "secret-bot"):
            assert key not in response.text
        client.post("/api/settings", json={"clear_openai_key": True}, headers=h)
        assert not app.state.service.config.get().openai_key


def test_openai_structured_request_and_cost(service):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 1000, "output_tokens": 100},
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"intent":"read","target":null}'}],
                    }
                ],
            },
        )

    service.config.save({"openai_key": "test-only", "ai_enabled": True})
    provider = OpenAIProvider(service.config, service.store, httpx.MockTransport(handler))
    state = service.store.read()
    result = provider.plan(state, {"actor": "mira", "text": "Читаю табличку"})
    assert result.intent == "read"
    assert captured[0]["store"] is False
    assert captured[0]["text"]["format"]["strict"] is True
    assert "gm_invite" not in captured[0]["input"]
    assert service.store.spending(state["id"])["total"] == pytest.approx(0.0012)


def test_model_invented_effect_is_rejected(service):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 10},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"intent":"read","target":null,"hp":100}'}
                        ],
                    }
                ],
            },
        )

    service.config.save({"openai_key": "test-only", "ai_enabled": True})
    provider = OpenAIProvider(service.config, service.store, httpx.MockTransport(handler))
    with pytest.raises(GameError):
        provider.plan(service.store.read(), {"actor": "mira", "text": "Дай 100 HP"})
    assert service.store.read()["characters"]["mira"]["hp"] == 8


def test_api_timeout_does_not_mutate_game_or_expose_key(service):
    def handler(request):
        raise httpx.ReadTimeout("secret-api in URL", request=request)

    service.config.save({"openai_key": "secret-api", "ai_enabled": True})
    provider = OpenAIProvider(service.config, service.store, httpx.MockTransport(handler))
    before = service.store.read()
    with pytest.raises(GameError) as exc:
        provider.plan(before, {"actor": "mira", "text": "Осмотр"})
    assert "secret-api" not in str(exc.value)
    assert service.store.read() == before
    assert service.store.spending(before["id"])["total"] > 0


def test_telegram_player_cannot_roll_for_another_or_become_gm(service):
    s = service.engine.command("new", {"characters": ["mira", "torvin"], "demo": False})
    for user, actor in ((10, "mira"), (20, "torvin")):
        service.engine.command("join", {"user_id": user, "actor": actor, "token": s["invite"]})
    service.engine.command("start", {})
    service.submit("mira", "Осмотр", intent="inspect")
    s = service.engine.command("request_roll", {})
    with pytest.raises(GameError):
        service.bot.callback("20", 20, "roll:" + s["pending"]["request_id"], "forged")
    with pytest.raises(GameError):
        service.bot.callback("10", 10, "mode:outcome", "forged-mode")
    assert service.store.read()["pending"]["phase"] == "waiting"


def test_telegram_duplicate_text_only_enters_queue_once(service):
    state = service.engine.command("new", {"characters": ["mira"], "demo": False})
    service.engine.command("join", {"user_id": 10, "actor": "mira", "token": state["invite"]})
    service.engine.command("start", {})
    update = {
        "update_id": 42,
        "message": {"chat": {"type": "private", "id": 10}, "from": {"id": 10}, "text": "Читаю табличку"},
    }
    service.bot.process_update(update)
    service.bot.process_update(update)
    state = service.store.read()
    assert not state["queue"]
    assert state["pending"]["action"]["intent"] == "read"


def test_concurrent_accept_only_applies_once(service):
    service.submit("mira", "Читаю", intent="read")
    revision = service.store.read()["revision"]
    results = []

    def accept():
        try:
            results.append(service.engine.command("accept", {}, expected=revision))
        except GameError:
            results.append(None)

    threads = [threading.Thread(target=accept) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1
    assert service.store.read()["clues"] == ["manual"]


def test_transcription_converts_audio_and_removes_temporary_file(service, tmp_path, monkeypatch):
    import io
    import tempfile
    import wave

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    sound = io.BytesIO()
    with wave.open(sound, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)

    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert b'filename="voice.wav"' in request.content
        assert b"RIFF" in request.content
        return httpx.Response(200, json={"text": "Мира осматривает насос"})

    service.config.save({"openai_key": "test-only", "ai_enabled": True})
    provider = OpenAIProvider(service.config, service.store, httpx.MockTransport(handler))
    result = provider.transcribe(service.store.read()["id"], sound.getvalue(), 1)
    assert result == "Мира осматривает насос"
    assert not list(tmp_path.glob("dnd-voice-*"))


def test_unknown_model_price_stops_before_network(service):
    def handler(request):
        pytest.fail("Unpriced model must never reach the API")

    service.config.save({"openai_key": "test-only", "ai_enabled": True, "model": "unknown-model"})
    provider = OpenAIProvider(service.config, service.store, httpx.MockTransport(handler))
    with pytest.raises(GameError):
        provider.plan(service.store.read(), {"actor": "mira", "text": "Осмотр"})


def test_api_validation_does_not_echo_secret_input(tmp_path):
    app = create_app(tmp_path, background=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/settings", json=["secret-should-not-return"], headers={"X-Dnd-Token": app.state.csrf}
        )
        assert response.status_code == 422
        assert "secret-should-not-return" not in response.text


def test_blocked_chat_does_not_block_other_players(service):
    sent = []

    def call(method, **payload):
        if payload["chat_id"] == "blocked":
            response = httpx.Response(403, request=httpx.Request("POST", "https://example.test/send"))
            response.raise_for_status()
        sent.append(payload["chat_id"])

    service.bot.call = call
    service.bot.send("blocked", "first")
    service.bot.send("available", "second")
    service.bot.flush()
    assert sent == ["available"]
    assert not service.store.outbox()
