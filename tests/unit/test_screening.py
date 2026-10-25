import base64
import hashlib
import json

import pytest

from robo_minerador.discovery.github import GitHubResponse
from robo_minerador.screening.commands import plan_commands
from robo_minerador.screening.licenses import allowed_spdx, assess_license

# Complete MIT text, including original attribution; fixtures are never dataset inputs.
MIT = b"""MIT License

Copyright (c) 2026 Fixture Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
SHA = "a" * 40


def license_response(body: bytes = MIT, spdx: str = "MIT", ref: str = SHA) -> GitHubResponse:
    data = {
        "path": "LICENSE",
        "encoding": "base64",
        "content": base64.b64encode(body).decode(),
        "license": {"spdx_id": spdx},
    }
    return GitHubResponse(
        {
            "endpoint": "/repos/owner/project/license",
            "params": {"ref": ref},
            "status": 200,
            "error": None,
            "body_base64": base64.b64encode(json.dumps(data).encode()).decode(),
        }
    )


@pytest.mark.parametrize("spdx", ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"])
def test_explicit_license_allowlist(spdx: str) -> None:
    assert allowed_spdx(spdx)


@pytest.mark.parametrize(
    "spdx", [None, "mit", "MIT OR GPL-3.0-only", "GPL-3.0-only", "NOASSERTION", "some MIT text"]
)
def test_other_licenses_not_allowed(spdx: str | None) -> None:
    assert not allowed_spdx(spdx)


def test_license_preserves_body_and_attribution() -> None:
    result = assess_license(
        {"LICENSE": MIT, "src/a.ts": b"export function a() {}"}, SHA, license_response()
    )
    assert result.status == "approved"
    assert result.reason_code == "license_verified_at_commit"
    assert base64.b64decode(result.body_base64) == MIT
    assert result.file_evidence["src/a.ts"].status == "root_license_candidate"


@pytest.mark.parametrize(
    "body,spdx,ref,status,reason",
    [
        (MIT + b"\n", "MIT", SHA, "not_evaluated", "license_unverifiable"),
        (MIT, "MIT", "b" * 40, "not_evaluated", "license_unverifiable"),
        (MIT, "NOASSERTION", SHA, "not_evaluated", "license_ambiguous"),
        (MIT, "GPL-3.0-only", SHA, "rejected", "license_not_allowed"),
    ],
)
def test_license_api_evidence_must_match_commit_bytes(
    body: bytes, spdx: str, ref: str, status: str, reason: str
) -> None:
    result = assess_license({"LICENSE": MIT}, SHA, license_response(body, spdx, ref))
    assert (result.status, result.reason_code) == (status, reason)


def test_missing_vs_unverifiable_license() -> None:
    missing = assess_license(
        {"src/a.ts": b"// MIT is a word, not a license"}, SHA, None, api_missing=True
    )
    assert (missing.status, missing.reason_code) == ("rejected", "license_missing")
    assert assess_license({"LICENSE": MIT}, SHA, None).status == "not_evaluated"


def test_conflicting_header_and_nested_license_not_overridden() -> None:
    result = assess_license(
        {
            "LICENSE": MIT,
            "src/a.ts": b"// SPDX-License-Identifier: GPL-3.0-only\nexport function a() {}",
            "vendor/LICENSE": MIT,
        },
        SHA,
        license_response(),
    )
    assert result.status == "not_evaluated"
    assert result.file_evidence["src/a.ts"].status == "conflicting_header"
    assert result.file_evidence["vendor/LICENSE"].status == "license_scope_unresolved"
    assert "GPL-3.0-only" in result.file_evidence["src/a.ts"].identifiers


def files(package: dict[str, object], **extra: bytes) -> dict[str, bytes]:
    return {"package.json": json.dumps(package).encode(), **extra}


def test_jest_plan_preserves_options_and_requires_no_build() -> None:
    plan = plan_commands(
        files(
            {
                "scripts": {"test": "jest --config custom.json --runInBand"},
                "devDependencies": {"jest": "^29.0.0"},
            }
        )
    )
    assert plan.status == "planned"
    assert plan.suite_argv == ("npm", "test", "--", "--ci", "--watch=false", "--watchAll=false")
    assert plan.packages[0].scripts["test"] == "jest --config custom.json --runInBand"
    assert plan.build_argv == ()
    assert plan.environment == {"CI": "true"}


def test_vitest_run_is_explicit_before_execution() -> None:
    plan = plan_commands(
        files({"scripts": {"test": "vitest --coverage"}, "devDependencies": {"vitest": "^3"}})
    )
    assert plan.status == "planned"
    assert plan.suite_argv == ("npm", "test", "--", "--run", "--watch=false")


@pytest.mark.parametrize(
    "package,status",
    [
        ({"name": "jest-vitest-test"}, "no_test_configuration"),
        (
            {"scripts": {"test": "mocha"}, "devDependencies": {"mocha": "*"}},
            "framework_unsupported",
        ),
        (
            {"scripts": {"test": "echo jest"}, "devDependencies": {"jest": "*"}},
            "suite_scope_unresolved",
        ),
        (
            {"scripts": {"test": "jest && mocha"}, "devDependencies": {"jest": "*", "mocha": "*"}},
            "framework_unsupported",
        ),
        (
            {"scripts": {"test": "node scripts/run.js"}, "devDependencies": {"jest": "*"}},
            "suite_scope_unresolved",
        ),
        ({"scripts": {"test": "jest"}}, "framework_ambiguous"),
        (
            {"scripts": {"test": "jest --watch"}, "devDependencies": {"jest": "*"}},
            "suite_scope_unresolved",
        ),
    ],
)
def test_distinguish_absent_unsupported_and_ambiguous(
    package: dict[str, object], status: str
) -> None:
    assert plan_commands(files(package)).status == status


def test_all_workspaces_preserved_and_partial_scope_unresolved() -> None:
    package = {"workspaces": ["packages/*"], "scripts": {"test": "npm test --workspace alpha"}}
    tree = files(
        package,
        **{
            "packages/a/package.json": json.dumps(
                {"name": "alpha", "scripts": {"test": "jest"}, "devDependencies": {"jest": "*"}}
            ).encode(),
            "packages/b/package.json": json.dumps(
                {
                    "name": "beta",
                    "scripts": {"test": "vitest run"},
                    "devDependencies": {"vitest": "*"},
                }
            ).encode(),
        },
    )
    plan = plan_commands(tree)
    assert [p.package_path for p in plan.packages] == [
        "package.json",
        "packages/a/package.json",
        "packages/b/package.json",
    ]
    assert plan.workspaces == ("packages/a", "packages/b")
    assert plan.status == "suite_scope_unresolved"
    assert plan.suite_argv == ()


def test_yaml_workspaces_and_manager_hint_are_preserved() -> None:
    plan = plan_commands(
        files(
            {"packageManager": "pnpm@9.0.0"},
            **{
                "pnpm-workspace.yaml": b"packages:\n  - packages/*\n",
                "packages/a/package.json": (
                    b'{"name":"a","scripts":{"test":"vitest run"},"devDependencies":{"vitest":"*"}}'
                ),
            },
        )
    )
    assert plan.workspaces == ("packages/a",)
    assert plan.packages[0].manager_hint == "pnpm@9.0.0"
    assert plan.status == "suite_scope_unresolved"


def test_root_suite_priority_and_supported_mixed_aggregation() -> None:
    plan = plan_commands(
        files(
            {"workspaces": ["packages/*"], "scripts": {"test": "npm test --workspaces"}},
            **{
                "packages/a/package.json": (
                    b'{"scripts":{"test":"jest"},"devDependencies":{"jest":"*"}}'
                ),
                "packages/b/package.json": (
                    b'{"scripts":{"test":"vitest run"},"devDependencies":{"vitest":"*"}}'
                ),
            },
        )
    )
    assert plan.status == "planned"
    assert plan.suite_argv == ("npm", "test")
    assert plan.frameworks == ("jest", "vitest")


def test_explicit_pretest_build_is_evidence_not_extra_compilation() -> None:
    plan = plan_commands(
        files(
            {
                "scripts": {"pretest": "npm run build", "build": "tsc", "test": "jest"},
                "devDependencies": {"jest": "*"},
            }
        )
    )
    assert plan.status == "planned"
    assert plan.build_argv == ()  # npm test invokes the declared lifecycle itself.
    assert "pretest" in plan.packages[0].scripts


@pytest.mark.parametrize(
    "script",
    [
        "jest --listTests",
        "jest --showConfig",
        "jest --findRelatedTests",
        "jest --lastCommit",
        "jest --onlyFailures",
        "jest --watchAll=1",
        "jest --help",
        "jest -t example",
        "vitest related src/a.ts",
    ],
)
def test_non_encompassing_or_nonexecuting_scripts_are_unresolved(script: str) -> None:
    runner = script.split()[0]
    assert (
        plan_commands(files({"scripts": {"test": script}, "devDependencies": {runner: "*"}})).status
        == "suite_scope_unresolved"
    )


def test_unsupported_mixed_suite_without_dependency_is_detected() -> None:
    plan = plan_commands(
        files({"scripts": {"test": "jest && mocha"}, "devDependencies": {"jest": "*"}})
    )
    assert plan.status == "framework_unsupported"


def test_corrupt_workspace_does_not_discard_other_workspaces() -> None:
    plan = plan_commands(
        files(
            {"workspaces": ["packages/*"]},
            **{
                "packages/a/package.json": b"not json",
                "packages/b/package.json": (
                    b'{"scripts":{"test":"jest"},"devDependencies":{"jest":"*"}}'
                ),
            },
        )
    )
    assert plan.workspaces == ("packages/a", "packages/b")
    assert len(plan.packages) == 3
    assert plan.status == "suite_scope_unresolved"


def test_complex_lifecycle_is_not_claimed_understood() -> None:
    plan = plan_commands(
        files(
            {
                "scripts": {"pretest": "node dynamic.js && npm run prepare", "test": "jest"},
                "devDependencies": {"jest": "*"},
            }
        )
    )
    assert plan.status == "suite_scope_unresolved"


def test_license_type_and_malformed_content_unverifiable() -> None:
    for change in ({"type": "dir"}, {"content": "%%%"}, {"license": None}, {"content": None}):
        response = license_response()
        data = {**response.data, **change}
        response.envelope["body_base64"] = base64.b64encode(json.dumps(data).encode()).decode()
        assert assess_license({"LICENSE": MIT}, SHA, response).status == "not_evaluated"


def test_license_missing_api_but_file_header_retained_as_unverifiable() -> None:
    result = assess_license(
        {"src/a.ts": b"// SPDX-License-Identifier: MIT\n"}, SHA, None, api_missing=True
    )
    assert result.status == "not_evaluated"
    assert result.file_evidence["src/a.ts"].identifiers == ("MIT",)


def test_notice_is_attribution_not_automatic_conflict() -> None:
    result = assess_license(
        {"LICENSE": MIT, "NOTICE": b"Copyright 2026 Fixture Authors\n"}, SHA, license_response()
    )
    assert result.status == "approved"
    assert result.file_evidence["NOTICE"].sha256


@pytest.mark.parametrize(
    "header",
    [
        b"// @license GPL-3.0-only\n",
        b"// License: GPL-2.0\n",
        b"/* Apache License, Version 2.0 */\n",
    ],
)
def test_non_spdx_license_headers_remain_unresolved(header: bytes) -> None:
    result = assess_license(
        {"LICENSE": MIT, "a.ts": header + b"export function a() {}"}, SHA, license_response()
    )
    assert result.status == "not_evaluated"
    assert result.file_evidence["a.ts"].status == "license_scope_unresolved"


def test_root_jest_projects_cover_all_workspaces_and_take_priority() -> None:
    plan = plan_commands(
        files(
            {
                "workspaces": ["packages/*"],
                "scripts": {"test": "jest --runInBand"},
                "devDependencies": {"jest": "*"},
                "jest": {"projects": ["<rootDir>/packages/*"]},
            },
            **{
                "packages/a/package.json": b'{"name":"a","devDependencies":{"jest":"*"}}',
                "packages/b/package.json": b'{"name":"b","devDependencies":{"jest":"*"}}',
            },
        )
    )
    assert plan.status == "planned"
    assert plan.suite_argv[:2] == ("npm", "test")
    assert len(plan.packages) == 3


def test_root_projects_partial_or_dynamic_scope_is_unresolved() -> None:
    for config in ({"projects": ["<rootDir>/packages/a"]}, {"projects": [{"displayName": "a"}]}):
        plan = plan_commands(
            files(
                {
                    "workspaces": ["packages/*"],
                    "scripts": {"test": "jest"},
                    "devDependencies": {"jest": "*"},
                    "jest": config,
                },
                **{
                    "packages/a/package.json": b'{"name":"a"}',
                    "packages/b/package.json": b'{"name":"b"}',
                },
            )
        )
        assert plan.status == "suite_scope_unresolved"


def test_non_npm_manager_is_deferred_to_t07_without_losing_suite_scope() -> None:
    plan = plan_commands(
        files(
            {
                "packageManager": "pnpm@9.0.0",
                "scripts": {"test": "vitest run --coverage"},
                "devDependencies": {"vitest": "*"},
            }
        )
    )
    assert plan.status == "planned"
    assert plan.reason_code == "manager_resolution_pending"
    assert plan.suite_argv == ()


def test_workspace_patterns_retained_in_plan() -> None:
    plan = plan_commands(
        files(
            {"workspaces": ["packages/*", "!packages/docs"]},
            **{
                "packages/a/package.json": b'{"name":"a"}',
                "packages/docs/package.json": b'{"name":"docs"}',
            },
        )
    )
    assert plan.workspace_patterns == ("packages/*", "!packages/docs")
    assert plan.workspaces == ("packages/a",)


def test_recursive_workspace_glob_keeps_zero_and_multiple_levels() -> None:
    plan = plan_commands(
        files(
            {"workspaces": ["packages/**/app"]},
            **{
                "packages/app/package.json": b'{"name":"a"}',
                "packages/deep/app/package.json": b'{"name":"b"}',
            },
        )
    )
    assert plan.workspaces == ("packages/app", "packages/deep/app")


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("prefix", [b"", b"// SPDX-License-Identifier: MIT\n"])
def test_review_mpl_notice_cannot_inherit_root_mit(newline: bytes, prefix: bytes) -> None:
    source = (
        prefix
        + b"""/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/. */
export function a() { return 1; }
"""
    ).replace(b"\n", newline)
    result = assess_license({"LICENSE": MIT, "a.ts": source}, SHA, license_response())
    assert (result.status, result.reason_code) == ("not_evaluated", "license_ambiguous")
    evidence = result.file_evidence["a.ts"]
    assert evidence.status == "license_scope_unresolved"
    assert base64.b64decode(evidence.header_base64) == source
    assert evidence.sha256 == hashlib.sha256(source).hexdigest()
    # Only explicit SPDX lines are recorded; prose does not invent an identifier.
    assert evidence.identifiers == (("MIT",) if prefix else ())
    assert base64.b64decode(result.body_base64) == MIT


@pytest.mark.parametrize("config_location", ["package", "root_json", "nested_json"])
def test_review_jest_projects_with_nonrepository_config_root_are_unresolved(
    config_location: str,
) -> None:
    package: dict[str, object] = {
        "workspaces": ["packages/*"],
        "scripts": {"test": "jest"},
        "devDependencies": {"jest": "*"},
    }
    extra = {"packages/a/package.json": b"{}", "packages/b/package.json": b"{}"}
    config = {"rootDir": "packages/a", "projects": ["<rootDir>/packages/*"]}
    if config_location == "package":
        package["jest"] = config
    elif config_location == "root_json":
        extra["jest.config.json"] = json.dumps(config).encode()
    else:
        # No rootDir: Jest defaults to the config's directory, not the repository root.
        package["scripts"] = {"test": "jest --config config/jest.json"}
        extra["config/jest.json"] = b'{"projects":["<rootDir>/packages/*"]}'
    plan = plan_commands(files(package, **extra))
    assert plan.status == "suite_scope_unresolved"
    assert plan.suite_argv == ()
    assert plan.workspaces == ("packages/a", "packages/b")
    assert len(plan.packages) == 3


@pytest.mark.parametrize("lifecycle", ["pretest", "posttest"])
def test_review_workspace_aggregator_does_not_bypass_root_lifecycle(lifecycle: str) -> None:
    script = "node dynamic-scope.js"
    plan = plan_commands(
        files(
            {
                "workspaces": ["packages/*"],
                "scripts": {"test": "npm test --workspaces", lifecycle: script},
            },
            **{
                "packages/a/package.json": (
                    b'{"scripts":{"test":"jest"},"devDependencies":{"jest":"*"}}'
                )
            },
        )
    )
    assert plan.status == "suite_scope_unresolved"
    assert plan.suite_argv == ()
    assert plan.packages[0].scripts[lifecycle] == script


def config_option_tree(script: str, *, workspaces: bool = True) -> dict[str, bytes]:
    package: dict[str, object] = {
        "scripts": {"test": script},
        "devDependencies": {"jest": "*"},
        "jest": {"projects": ["<rootDir>/packages/*"]},
    }
    if workspaces:
        package["workspaces"] = ["packages/*"]
    return files(
        package,
        **{
            "packages/a/package.json": b"{}",
            "packages/b/package.json": b"{}",
            "complete.json": b'{"projects":["<rootDir>/packages/*"]}',
            "config/partial.json": b'{"projects":["<rootDir>/../packages/a"]}',
        },
    )


@pytest.mark.parametrize("option", ["--config {}", "-c {}", "--config={}", "-c={}"])
@pytest.mark.parametrize(
    "path,expected_status",
    [("config/partial.json", "suite_scope_unresolved"), ("complete.json", "planned")],
)
def test_review_config_option_forms_have_equivalent_scope(
    option: str,
    path: str,
    expected_status: str,
) -> None:
    script = "jest " + option.format(path)
    plan = plan_commands(config_option_tree(script))
    assert plan.status == expected_status
    assert bool(plan.suite_argv) == (expected_status == "planned")
    assert plan.workspaces == ("packages/a", "packages/b")
    assert len(plan.packages) == 3
    assert plan.packages[0].scripts["test"] == script


@pytest.mark.parametrize("workspaces", [False, True])
@pytest.mark.parametrize(
    "options",
    [
        "--config config/partial.json --config complete.json",
        "-c=config/partial.json --config=complete.json",
        "--config=complete.json -c=config/partial.json",
        "-c complete.json --config complete.json",
    ],
)
def test_review_multiple_config_options_are_unresolved(options: str, workspaces: bool) -> None:
    script = "jest " + options
    plan = plan_commands(config_option_tree(script, workspaces=workspaces))
    assert plan.status == "suite_scope_unresolved"
    assert plan.suite_argv == ()
    assert plan.packages[0].scripts["test"] == script


@pytest.mark.parametrize(
    "script",
    [
        "vitest run run",
        "vitest -w unit",
        "vitest --maxWorkers unit",
        "vitest run --runInBand",
        "vitest run --ci",
        "vitest --coverage run",
        "vitest --run run",
        "vitest run foo",
        "jest foo",
        "jest run",
        "vitest --threads unit",
        "vitest --workers unit",
        "vitest run --testNamePattern unit",
        "jest --testNamePattern unit",
        "jest -w foo",
    ],
)
def test_review_runner_grammar_does_not_hide_positional_filters(script: str) -> None:
    runner = script.split()[0]
    plan = plan_commands(
        files(
            {
                "scripts": {"test": script},
                "devDependencies": {runner: "*"},
            }
        )
    )
    assert plan.status == "suite_scope_unresolved"
    assert plan.suite_argv == ()
    assert plan.packages[0].scripts["test"] == script


@pytest.mark.parametrize(
    "script",
    [
        "vitest run",
        "vitest run --coverage",
        "vitest --run",
        "jest -w2",
        "jest -w 2",
        "jest --maxWorkers=2",
    ],
)
def test_review_runner_grammar_keeps_supported_whole_suite_commands(script: str) -> None:
    runner = script.split()[0]
    plan = plan_commands(
        files(
            {
                "scripts": {"test": script},
                "devDependencies": {runner: "*"},
            }
        )
    )
    assert plan.status == "planned"
    assert plan.suite_argv[:2] == ("npm", "test")
    assert plan.packages[0].scripts["test"] == script


def test_review_vitest_config_value_is_not_run_subcommand() -> None:
    plan = plan_commands(
        files(
            {"workspaces": ["packages/*"], "scripts": {"test": "npm test --workspaces"}},
            **{
                "packages/a/package.json": (
                    b'{"scripts":{"test":"vitest --config run"},"devDependencies":{"vitest":"*"}}'
                ),
                "packages/a/run": b"{}",
            },
        )
    )
    assert plan.status == "suite_scope_unresolved"
    assert plan.suite_argv == ()
