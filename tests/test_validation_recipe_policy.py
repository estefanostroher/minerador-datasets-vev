import copy
import hashlib
import json

import pytest

from robo_minerador import validation_recipe_resolver as resolver
from robo_minerador.validation_instrument import MATRIX


def write_policy(tmp_path, policy):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def test_default_policy_and_raw_hash(tmp_path):
    policy = resolver.load_recipe_policy()
    raw = resolver.DEFAULT_POLICY.read_bytes()
    assert policy["sha256"] == hashlib.sha256(raw).hexdigest()
    assert policy["version"] == 1
    assert [(p["node"], p["vitest"], p["vite"]) for p in policy["profiles"]] == [
        ("22.23.4", "4.1.10", "7.3.6"), ("22.23.4", "4.1.11", "8.3.0"), ("22.23.4", "4.1.11", "8.0.16"),
        ("24.22.2", "5.0.5", "8.3.4"), ("26.13.0", "5.0.5", "8.3.5")]
    assert set((p["node"], p["vitest"], p["vite"]) for p in policy["profiles"]) == MATRIX
    assert policy["limits"] == {"max_files": 64, "max_nonlock_bytes": 1048576,
        "max_lock_bytes": 8388608, "max_total_bytes": 16777216,
        "helper_seconds": 30, "helper_stdout_bytes": 1048576}
    path = tmp_path / "policy.json"
    path.write_bytes(raw + b"\n")
    changed = resolver.load_recipe_policy(path)
    assert changed["sha256"] != policy["sha256"]
    assert {k: v for k, v in changed.items() if k != "sha256"} == {k: v for k, v in policy.items() if k != "sha256"}


def test_load_returns_independent_objects_and_preserves_policy_order(tmp_path):
    original = resolver.load_recipe_policy()
    original["profiles"][0]["node"] = "modified"
    original["limits"]["max_files"] = 1
    fresh = resolver.load_recipe_policy()
    assert fresh["profiles"][0]["node"] == "22.23.4"
    assert fresh["limits"]["max_files"] == 64
    policy = json.loads(resolver.DEFAULT_POLICY.read_bytes())
    policy["profiles"].reverse()
    assert resolver.load_recipe_policy(write_policy(tmp_path, policy))["profiles"] == policy["profiles"]


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(version=2),
    lambda p: p.update(version=True),
    lambda p: p.update(extra=1),
    lambda p: p["profiles"].append(copy.deepcopy(p["profiles"][0])),
    lambda p: p["profiles"][0].update(extra=1),
    lambda p: p["profiles"][0].update(node="22"),
    lambda p: p["profiles"][0].update(npm="^10.9.9"),
    lambda p: p["profiles"][0].update(vitest="04.1.10"),
    lambda p: p["profiles"][0].update(vite="8.0.16"),
    lambda p: p["profiles"][0].update(image="node:latest"),
    lambda p: p["profiles"][0].update(image="node@sha256:" + "0" * 64),
    lambda p: p["profiles"][0].update(platform="linux/arm64"),
    lambda p: p["profiles"][0].update(npm="11.19.1"),
    lambda p: p["profiles"][0].update(node=[]),
    lambda p: p.update(profiles=[]),
    lambda p: p["rules"].update(unknown=True),
    lambda p: p["limits"].update(unknown=1),
    lambda p: p["limits"].update(max_files=True),
])
def test_invalid_policy_rejected(tmp_path, mutation):
    policy = json.loads(resolver.DEFAULT_POLICY.read_bytes())
    mutation(policy)
    with pytest.raises(ValueError):
        resolver.load_recipe_policy(write_policy(tmp_path, policy))


@pytest.mark.parametrize("field", ["max_files", "max_nonlock_bytes", "max_lock_bytes", "max_total_bytes", "helper_seconds", "helper_stdout_bytes"])
def test_limit_boundaries(tmp_path, field):
    policy = json.loads(resolver.DEFAULT_POLICY.read_bytes())
    resolver.load_recipe_policy(write_policy(tmp_path, policy))
    policy["limits"][field] += 1
    with pytest.raises(ValueError):
        resolver.load_recipe_policy(write_policy(tmp_path, policy))


@pytest.mark.parametrize("raw", [b'{"version":1,"version":1}', b'{"version":NaN}', b'\xff', b'[]', b'{'])
def test_strict_json_rejected(tmp_path, raw):
    path = tmp_path / "policy.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        resolver.load_recipe_policy(path)
