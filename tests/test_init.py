import json

import pytest

from mad.cli import ROLE_PRESETS, _parse_verifier, cmd_init


def test_presets_are_valid_role_lineups():
    from mad.cli import _ROLE_FACTORIES
    for name, roles in ROLE_PRESETS.items():
        assert roles, name
        assert len(roles) == len(set(roles)), name
        for r in roles:
            assert r in _ROLE_FACTORIES, (name, r)
    # presets grow monotonically: minimal ⊂ standard ⊂ full
    assert set(ROLE_PRESETS["minimal"]) < set(ROLE_PRESETS["standard"])
    assert set(ROLE_PRESETS["standard"]) < set(ROLE_PRESETS["full"])


def test_parse_verifier_null_and_script():
    assert _parse_verifier("null") == {"kind": "null"}
    spec = _parse_verifier("script:python gate.py")
    assert spec == {"kind": "script", "command": "python gate.py"}


def test_parse_verifier_rejects_unknown():
    with pytest.raises(SystemExit):
        _parse_verifier("llm_judge")


def test_init_generates_runnable_config(tmp_path):
    args = type("A", (), {})()  # lightweight args namespace
    args.task = "  Discuss whether caching helps sparse lookups.  "
    args.dir = tmp_path / "proj"
    args.preset = "standard"
    args.roles = None
    args.runtime = "mock"
    args.model = None
    args.rounds = 6
    args.max_messages = 60
    args.verifier = "null"
    assert cmd_init(args) == 0

    cfg = json.loads((args.dir / "session.json").read_text(encoding="utf-8"))
    assert cfg["task"] == "Discuss whether caching helps sparse lookups."  # stripped
    assert cfg["roles"] == ROLE_PRESETS["standard"]
    assert cfg["runtime"] == {"kind": "mock"}
    assert cfg["verifier"] == {"kind": "null"}
    # gate-less mode must not demand a verifier gate
    assert cfg["require_verifier_gate"] is False
    assert (args.dir / "memory.json").exists()


def test_init_gated_mode_requires_gate(tmp_path):
    args = type("A", (), {})()
    args.task = "sort faster"
    args.dir = tmp_path / "proj"
    args.preset = "minimal"
    args.roles = None
    args.runtime = "mock"
    args.model = None
    args.rounds = 4
    args.max_messages = 40
    args.verifier = "script:python check.py"
    assert cmd_init(args) == 0
    cfg = json.loads((args.dir / "session.json").read_text(encoding="utf-8"))
    assert cfg["verifier"] == {"kind": "script", "command": "python check.py"}
    assert cfg["require_verifier_gate"] is True


def test_init_rejects_unknown_roles(tmp_path):
    args = type("A", (), {})()
    args.task = "t"
    args.dir = tmp_path / "proj"
    args.preset = "minimal"
    args.roles = "proposer,wizard"
    args.runtime = "mock"
    args.model = None
    args.rounds = 4
    args.max_messages = 40
    args.verifier = "null"
    with pytest.raises(SystemExit):
        cmd_init(args)
