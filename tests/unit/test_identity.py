import hashlib

import pytest

from robo_minerador.identity import occurrence_id, stable_digest


def test_key_order_does_not_change_nested_identity():
    assert stable_digest({"path": "ação.ts", "nested": {"b": 2, "a": 1}}) == stable_digest(
        {"nested": {"a": 1, "b": 2}, "path": "ação.ts"}
    )


def test_digest_uses_compact_utf8_json_without_ascii_escaping():
    assert stable_digest({"é": "😀"}) == hashlib.sha256('{"é":"😀"}'.encode()).hexdigest()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_digest_rejects_nonfinite_json(value):
    with pytest.raises(ValueError):
        stable_digest({"metric": value})


def test_digest_preserves_source_newlines():
    assert stable_digest({"code": "a\r\nb"}) != stable_digest({"code": "a\nb"})


def test_occurrence_contract_is_versioned_and_uses_utf16_offset():
    prefix = "// 😀\r\n"
    offset = len(prefix.encode("utf-16-le")) // 2
    assert offset != len(prefix)
    assert occurrence_id(42, "abc123", "src/ação.ts", offset) == stable_digest(
        {
            "version": 1,
            "repo_id": 42,
            "commit_sha": "abc123",
            "path": "src/ação.ts",
            "start_offset": offset,
        }
    )


def test_occurrences_differ_by_repository_commit_unicode_path_and_position():
    inputs = [
        (42, "abc", "src/ação.ts", 7),
        (43, "abc", "src/ação.ts", 7),
        (42, "def", "src/ação.ts", 7),
        (42, "abc", "src/😀.ts", 7),
        (42, "abc", "src/ação.ts", 8),
    ]
    assert len({occurrence_id(*args) for args in inputs}) == len(inputs)


@pytest.mark.parametrize(
    "path",
    ["", "/a.ts", "../a.ts", "src/../a.ts", "a\\b.ts", "C:/a.ts", "./a.ts", "a//b.ts", "a/", "."],
)
def test_occurrence_rejects_noncanonical_repository_paths(path):
    with pytest.raises(ValueError):
        occurrence_id(42, "abc", path, 0)


@pytest.mark.parametrize("repo_id,offset", [(True, 0), (1.0, 0), (42, -1), (42, True), (42, 1.5)])
def test_occurrence_requires_integer_repository_and_nonnegative_integer_offset(repo_id, offset):
    with pytest.raises(ValueError):
        occurrence_id(repo_id, "abc", "a.ts", offset)
