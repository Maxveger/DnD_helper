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
prompt=sys.stdin.read()
assert prompt and args[-1]=="-"
assert "--ignore-user-config" in args and "--ephemeral" in args
assert "resume" not in args
assert any(arg.startswith("model_instructions_file=") for arg in args)
role_arg=next(arg for arg in args if arg.startswith("model_instructions_file="))
role=open(json.loads(role_arg.split("=",1)[1]),encoding="utf-8").read()
assert "Do not invoke tools" in role
assert "CODEX_API_KEY" not in os.environ and "OPENAI_API_KEY" not in os.environ
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

    return TestProvider(timeout=0.5)


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
    from dnd_helper.free_world import Proposal

    schema = output_schema(Proposal)
    assert "oneOf" not in json.dumps(schema)
    assert "discriminator" not in json.dumps(schema)
    assert "default" not in json.dumps(schema)
    assert '"title"' not in json.dumps(schema)
    assert "minItems" not in json.dumps(schema)
    assert "anyOf" in json.dumps(schema)
    assert "oneOf" in json.dumps(Proposal.model_json_schema())


def test_director_travel_destination_is_limited_to_prepared_options():
    from dnd_helper.codex_provider import request_schema
    from dnd_helper.free_world import DirectorCard

    schema = request_schema(
        DirectorCard,
        {
            "travel_options": [
                {"destination": "courtyard"},
                {"destination": "archive"},
            ],
            "entity_index": {"courtyard": {}, "archive": {}},
        },
    )
    assert schema["$defs"]["TravelEffect"]["properties"]["destination"]["enum"] == [
        "courtyard",
        "archive",
    ]


def test_codex_output_schema_requires_every_object_property():
    from dnd_helper.codex_provider import output_schema
    from dnd_helper.free_world import Advice

    schema = output_schema(Advice)
    entity = schema["$defs"]["Entity"]
    assert set(entity["required"]) == set(entity["properties"])
    assert set(schema["required"]) == set(schema["properties"])


def test_assistant_schema_constrains_evidence_targets_and_speaker_ids():
    from dnd_helper.codex_provider import request_schema
    from dnd_helper.free_world import Advice, initial_world

    world = initial_world()
    world["action"] = {"actor": "mira"}
    schema = request_schema(Advice, world)["properties"]
    assert schema["evidence"]["items"]["enum"] == ["water", "help"]
    assert schema["speaker"]["anyOf"][0]["enum"] == ["ada"]
    assert schema["intent"]["$ref"] == "#/$defs/Intent"
    world["entities"]["mira"]["location"] = "forest"
    world["facts"] = {}
    full_schema = request_schema(Advice, world)
    schema = full_schema["properties"]
    assert schema["speaker"]["anyOf"][0]["enum"] == ["ada"]
    assert schema["evidence"]["maxItems"] == 0
    assert set(full_schema["$defs"]["Intent"]["properties"]["targets"]["items"]["enum"]) == set(
        world["entities"]
    )


def test_assistant_schema_uses_compact_scene_but_allows_indexed_targets():
    from dnd_helper.codex_provider import request_schema
    from dnd_helper.free_world import Advice, build_scene_context
    from dnd_helper.world_documents import example_document, start_document

    world = start_document(example_document())
    payload = build_scene_context(
        world,
        {"id": "a", "actor": "mira", "participants": ["mira"], "text": "Осматриваю площадь", "mode": "action"},
    )
    schema = request_schema(Advice, payload)
    assert set(schema["properties"]["evidence"]["items"]["enum"]) == {"water", "help"}
    assert schema["properties"]["speaker"]["anyOf"][0]["enum"] == ["ada"]
    assert set(schema["$defs"]["Intent"]["properties"]["targets"]["items"]["enum"]) == (
        set(payload["entity_index"]) | {"water", "help"}
    )
