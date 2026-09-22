import sys
import threading

import pytest
from pydantic import BaseModel, ConfigDict

from dnd_helper.codex_provider import CodexProvider
from dnd_helper.rules import GameError


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


@pytest.fixture
def provider(tmp_path, monkeypatch):
    script = tmp_path / "fake_codex.py"
    script.write_text(
        """import sys,json,os,time
args=sys.argv[1:]
mode=os.environ.get("DND_TEST_MODE", "success")
if "--version" in args:
 print("codex-cli 0.154.0");sys.exit()
if "login" in args:
 print("Logged in using " + ("API key: secret-marker" if mode=="api" else "ChatGPT"));sys.exit()
if "app-server" in args:
 thread="11111111-1111-1111-1111-111111111111"
 turn_count=0
 def send(value): print(json.dumps(value),flush=True)
 for line in sys.stdin:
  request=json.loads(line);method=request.get("method");rid=request.get("id");params=request.get("params",{})
  if method=="initialize": send({"id":rid,"result":{"userAgent":"fake"}})
  elif method=="initialized": pass
  elif method=="thread/start":
   assert params["approvalPolicy"]=="never" and params["sandbox"]=="read-only"
   send({"id":rid,"result":{"thread":{"id":thread}}})
  elif method=="thread/resume": send({"id":rid,"result":{"thread":{"id":thread}}})
  elif method=="turn/start":
   turn_count+=1;turn=f"turn-{turn_count}"
   if mode=="session": assert "outputSchema" not in params
   if mode=="session_schema": assert "outputSchema" in params
   assert params["sandboxPolicy"]=={"type":"readOnly"}
   send({"id":rid,"result":{"turn":{"id":turn}}})
   send({"method":"turn/started","params":{"threadId":thread,"turn":{"id":turn,"items":[],"status":"inProgress"}}})
   send({"method":"item/agentMessage/delta","params":{"threadId":thread,"turnId":turn,"itemId":"answer","delta":"{"}})
   answer="not json" if mode=="bad_session" else json.dumps({"text":"Привет"})
   send({"method":"item/completed","params":{"threadId":thread,"turnId":turn,"completedAtMs":1,"item":{"id":"answer","type":"agentMessage","text":answer}}})
   usage={"inputTokens":9,"cachedInputTokens":4,"cacheWriteInputTokens":0,"outputTokens":3,"reasoningOutputTokens":1,"totalTokens":13}
   send({"method":"thread/tokenUsage/updated","params":{"threadId":thread,"turnId":turn,"tokenUsage":{"last":usage,"total":usage,"modelContextWindow":200000}}})
   send({"method":"turn/completed","params":{"threadId":thread,"turn":{"id":turn,"items":[],"status":"completed"}}})
  elif method=="thread/compact/start":
   turn="compact-1";send({"id":rid,"result":{}})
   send({"method":"turn/started","params":{"threadId":thread,"turn":{"id":turn,"items":[],"status":"inProgress"}}})
   send({"method":"item/completed","params":{"threadId":thread,"turnId":turn,"completedAtMs":1,"item":{"id":"compact","type":"contextCompaction"}}})
   send({"method":"turn/completed","params":{"threadId":thread,"turn":{"id":turn,"items":[],"status":"completed"}}})
  elif method=="turn/interrupt": send({"id":rid,"result":{}})
 sys.exit()
prompt=sys.stdin.read()
assert prompt and args[-1]=="-"
assert "--ignore-user-config" in args
if mode.startswith("session"):
 assert "--ephemeral" not in args
 if mode=="session": assert "--output-schema" not in args
 if mode=="session_schema": assert "--output-schema" in args
 if "resume" in args: assert "11111111-1111-1111-1111-111111111111" in args
else:
 assert "--ephemeral" in args
 assert "resume" not in args
assert any(arg.startswith("model_instructions_file=") for arg in args)
role_arg=next(arg for arg in args if arg.startswith("model_instructions_file="))
role=open(json.loads(role_arg.split("=",1)[1]),encoding="utf-8").read()
assert "Do not invoke tools" in role
assert "CODEX_API_KEY" not in os.environ and "OPENAI_API_KEY" not in os.environ
if "--output-schema" in args:
 assert json.loads(open(args[args.index("--output-schema")+1]).read())["additionalProperties"] is False
if mode=="timeout":time.sleep(30)
if mode=="limit":
 print("usage limit secret-marker",file=sys.stderr);sys.exit(1)
if mode=="tool":
 print(json.dumps({"type":"item.completed","item":{"type":"command_execution"}}))
print(json.dumps({"type":"thread.started","thread_id":"11111111-1111-1111-1111-111111111111"}))
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text": "not json" if mode=="bad" else json.dumps({"text":"Привет"})}}))
if mode!="incomplete":print(json.dumps({"type":"turn.completed","usage":{"input_tokens":9,"output_tokens":3}}))
""",
        encoding="utf-8",
    )

    class TestProvider(CodexProvider):
        def binary(self):
            return [sys.executable, str(script)]

    value = TestProvider(timeout=0.5)
    yield value
    value.close()


def test_codex_stdin_schema_usage_no_api_and_no_shell(provider, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_API_KEY", "secret-marker")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-marker")
    payload = {"action": f"$(touch {tmp_path}/injected); echo secret"}
    result, usage = provider.structured("Инструкция", payload, Reply, threading.Event())
    assert result.text == "Привет"
    assert usage["usage"]["input_tokens"] == 9
    assert "session_id" not in usage
    assert not (tmp_path / "injected").exists()
    assert provider.status()["ready"]


def test_codex_conversation_reuses_one_app_server_process_and_thread(provider, monkeypatch):
    monkeypatch.setenv("DND_TEST_MODE", "session")
    text, first = provider.conversation("Первый ход", threading.Event())
    assert Reply.model_validate_json(text).text == "Привет"
    assert first["session_id"] == "11111111-1111-1111-1111-111111111111"
    process_id = provider.app_process.pid

    text, second = provider.conversation(
        "Следующий ход",
        threading.Event(),
        session_id=first["session_id"],
    )
    assert Reply.model_validate_json(text).text == "Привет"
    assert second["session_id"] == first["session_id"]
    assert provider.app_process.pid == process_id
    assert second["provider"] == "codex-app-server"
    assert second["usage_scope"] == "turn"
    assert second["usage"]["cached_input_tokens"] == 4
    assert second["first_event_seconds"] is not None
    assert second["first_output_seconds"] is not None


def test_codex_conversation_can_enforce_schema_for_bootstrap_or_explicit_retry(provider, monkeypatch):
    monkeypatch.setenv("DND_TEST_MODE", "session_schema")
    text, details = provider.conversation(
        "Первый ход",
        threading.Event(),
        schema_class=Reply,
    )
    assert Reply.model_validate_json(text).text == "Привет"
    assert details["session_id"] == "11111111-1111-1111-1111-111111111111"


def test_codex_app_server_compacts_loaded_thread(provider, monkeypatch):
    monkeypatch.setenv("DND_TEST_MODE", "session")
    _, details = provider.conversation("Первый ход", threading.Event())
    compact = provider.compact(details["session_id"], threading.Event())
    assert compact["provider"] == "codex-app-server"
    assert compact["first_event_seconds"] is not None


@pytest.mark.parametrize(
    "mode,match",
    [
        ("limit", "Лимит"),
        ("bad", "формата"),
        ("incomplete", "формата"),
        ("tool", "инструмент"),
        ("api", "API-ключом"),
        ("timeout", "вовремя"),
    ],
)
def test_codex_failures_do_not_leak_credentials_or_fallback(provider, monkeypatch, mode, match):
    monkeypatch.setenv("DND_TEST_MODE", mode)
    with pytest.raises(GameError, match=match) as exc:
        provider.structured("Инструкция", {}, Reply, threading.Event())
    assert "secret-marker" not in str(exc.value)


def test_codex_cancel_stops_process(provider, monkeypatch):
    monkeypatch.setenv("DND_TEST_MODE", "timeout")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(GameError, match="отменён"):
        provider.structured("Инструкция", {}, Reply, cancel)


def test_codex_tools_disabled_and_auth_untouched(provider):
    options = provider.options("/tmp")
    for name in (
        "shell_tool",
        "unified_exec",
        "multi_agent",
        "browser_use",
        "computer_use",
        "plugins",
        "apps",
        "hooks",
    ):
        assert f"features.{name}=false" in options
    assert 'forced_login_method="chatgpt"' in options
    assert "tools.view_image=false" in options
    assert "project_doc_max_bytes=0" in options
    assert "features.skip_host_skill_discovery=true" in options


def test_codex_schema_uses_supported_union_but_keeps_local_validation():
    import json

    from dnd_helper.codex_provider import output_schema
    from dnd_helper.studio import StudioTurn

    schema = output_schema(StudioTurn)
    assert "oneOf" not in json.dumps(schema)
    assert "discriminator" not in json.dumps(schema)
    assert "default" not in json.dumps(schema)
    assert '"title"' not in json.dumps(schema)
    assert "minItems" not in json.dumps(schema)
    assert "anyOf" in json.dumps(schema)
    assert "anyOf" in json.dumps(StudioTurn.model_json_schema())


def test_codex_output_schema_requires_every_object_property():
    from dnd_helper.codex_provider import output_schema
    from dnd_helper.studio import DirectorPulse

    schema = output_schema(DirectorPulse)
    move = schema["$defs"]["DirectorMove"]
    assert set(move["required"]) == set(move["properties"])
    assert set(schema["required"]) == set(schema["properties"])
