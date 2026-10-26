"""T14 mandatory gates, repetition-local counting and offline evidence replay."""

import importlib

import pytest

from robo_minerador.validation import cases


@pytest.mark.parametrize(
    "sets,expected",
    [
        ([{"a", "b"}] * 3, False),
        ([set("abcd"), set("abc"), set("abcd")], False),
        ([set("abc"), set("abcd"), set("abcd")], False),
        ([set("abcd")] * 2, False),
        ([set("abcd")] * 4, False),
        ([set("abcd")] * 3, True),
        ([set("abcd"), set("efgh"), set("ijkl")], True),
    ],
)
def test_minimum_is_four_distinct_cases_in_each_of_exactly_three_runs(sets, expected):
    rule = getattr(cases, "meets_minimum_in_each_run", None)
    assert rule is not None, "T14 repetition-local minimum is missing"
    assert rule(sets) is expected


def service_module():
    try:
        return importlib.import_module("robo_minerador.validation.service")
    except ModuleNotFoundError:
        pytest.fail("T14 eligibility service is missing")


@pytest.mark.parametrize("status", ["rejected", "not_evaluated", None])
@pytest.mark.parametrize(
    "criterion", [f"H-{group}{n:02}" for group in ("R", "F") for n in range(1, 7)]
)
def test_every_mandatory_criterion_must_be_present_and_approved(criterion, status):
    module = service_module()
    decisions = dict.fromkeys(module.MANDATORY_CRITERIA, "approved")
    assert module.all_mandatory_approved(decisions)
    if status is None:
        del decisions[criterion]
    else:
        decisions[criterion] = status
    assert not module.all_mandatory_approved(decisions)


def local_files():
    return {
        "package.json": b'{"scripts":{"test":"jest"},"devDependencies":{"jest":"29.7.0"}}',
        "value.ts": b"import { one } from './local'; export function f(n:number){return n + one;}",
        "local.ts": b"export const one = 1;",
        "value.test.ts": b"import { f } from './value'; test('one',()=>{expect(f(1)).toBe(2)});",
    }


def test_local_dependency_review_combines_configuration_and_preserved_sources():
    review = service_module().review_dependencies(local_files(), ())
    assert review.status == "approved"
    assert review.local_paths == ("local.ts", "value.test.ts", "value.ts")
    assert review.configuration_paths == ("package.json",)


@pytest.mark.parametrize(
    "source",
    [
        b"export function f(){return fetch('https://example.test');}",
        b"import db from 'database-client'; export function f(){return db.query('x');}",
        b"export function f(){return process.env.TOKEN;}",
        b"export function f(){return globalThis['fetch']('x');}",
        b"export function f(){return import('./local');}",
        b"import { one } from './missing'; export function f(){return one;}",
    ],
)
def test_unresolved_service_or_dynamic_configuration_is_never_approved(source):
    review = service_module().review_dependencies({**local_files(), "value.ts": source}, ())
    assert review.status == "not_evaluated"


def test_successful_isolation_does_not_erase_an_ignored_network_attempt():
    attempts = ("ECONNREFUSED: ignored local fetch attempt",)
    review = service_module().review_dependencies(local_files(), attempts)
    assert review.status == "not_evaluated"
    assert review.observed_attempts == attempts


def test_service_configuration_requires_analysis_even_when_tests_pass():
    files = {**local_files(), "compose.yaml": b"services: {database: {image: postgres}}"}
    assert service_module().review_dependencies(files, ()).status == "not_evaluated"


@pytest.mark.parametrize(
    "path,body",
    [
        ("jest.config.cjs", b"module.exports={globalSetup:'./external.cjs'};"),
        ("vitest.config.ts", b"export default {test:{environment:'external-service'}};"),
        ("helper.js", b"fetch('https://example.test').catch(()=>{});"),
        ("tsconfig.json", b'{"compilerOptions":{"plugins":[{"name":"remote-service"}]}}'),
        ("package.json", b'{"scripts":{"test":"jest","pretest":"node service.js"}}'),
    ],
)
def test_unsupported_executable_configuration_does_not_become_external_independence(path, body):
    assert (
        service_module().review_dependencies({**local_files(), path: body}, ()).status
        == "not_evaluated"
    )


def test_t16_loader_never_trusts_an_accepted_flag_in_caller_json(tmp_path):
    module = service_module()
    assert hasattr(module.EligibilityService, "load"), "T16 needs an evidence-replaying loader"
    from robo_minerador.state.artifacts import ArtifactStore
    from robo_minerador.state.store import StateStore

    artifacts = ArtifactStore(tmp_path / "cas")
    with StateStore(tmp_path / "state.sqlite", artifacts=artifacts) as state:
        run = state.create_run("T14-forged-acceptance")
        forged = artifacts.put(b'{"kind":"eligible_occurrence","accepted":true}')
        with pytest.raises(ValueError):
            module.EligibilityService(state, artifacts).load(run, forged)


@pytest.mark.parametrize("path", ["package.json", "tsconfig.json", "service.json"])
def test_unreadable_or_custom_configuration_is_an_audited_limitation(path):
    result = service_module().review_dependencies({**local_files(), path: b"not-json"}, ())
    assert result.status == "not_evaluated"
    assert result.limitations


@pytest.mark.parametrize(
    "package",
    [
        b'{"scripts":{"test":"jest"},"jest":{"testEnvironment":"external-service"}}',
        b'{"scripts":{"test":"jest --config node_modules/external-config/index.js"}}',
        b'{"scripts":{"test":"vitest run --config node_modules/external-config/index.js"}}',
    ],
)
def test_embedded_or_external_selected_runner_configuration_stays_unresolved(package):
    assert (
        service_module().review_dependencies({**local_files(), "package.json": package}, ()).status
        == "not_evaluated"
    )


@pytest.mark.parametrize("suffix", ["tsx", "js", "json", "mts"])
def test_imported_local_module_must_belong_to_the_actually_reviewed_subset(suffix):
    files = local_files()
    files["value.ts"] = (
        f"import {{ one }} from './remote.{suffix}'; export function f(n:number){{return n + one;}}"
    ).encode()
    files["remote." + suffix] = b"export const one = fetch('https://example.test');"
    review = service_module().review_dependencies(files, ())
    assert review.status == "not_evaluated"
    assert review.limitations


@pytest.mark.parametrize("binding", ["one /* fetch */", "one /* fetch */ as localOne"])
def test_import_comments_cannot_authorize_a_global_network_identifier(binding):
    files = local_files()
    files["value.ts"] = (
        f"import {{ {binding} }} from './local'; "
        "export function f(n:number){fetch('https://example.test');return n;}"
    ).encode()
    assert service_module().review_dependencies(files, ()).status == "not_evaluated"


def test_import_alias_has_to_be_supported_rather_than_authorizing_arbitrary_globals():
    files = local_files()
    files["value.ts"] = (
        b"import { one as localOne } from './local'; "
        b"export function f(n:number){return n + localOne;}"
    )
    assert service_module().review_dependencies(files, ()).status == "approved"


def test_import_text_inside_a_string_is_not_a_binding():
    files = local_files()
    files["value.ts"] = (
        b"const label = \"import { fetch } from './local';\"; "
        b"export function f(n:number){fetch('https://example.test');return n;}"
    )
    assert service_module().review_dependencies(files, ()).status == "not_evaluated"


@pytest.mark.parametrize("shadow", ["local", "local.tsx"])
def test_unreviewed_resolution_alternative_cannot_hide_behind_a_reviewed_ts_module(shadow):
    files = {**local_files(), shadow: b"export const one = fetch('https://example.test');"}
    assert service_module().review_dependencies(files, ()).status == "not_evaluated"


def test_unsupported_regex_literal_cannot_smuggle_import_bindings():
    files = local_files()
    files["value.ts"] = (
        b"const label = /import {fetch} from 'vitest';/; "
        b"export function f(n:number){fetch('https://example.test');return n;}"
    )
    assert service_module().review_dependencies(files, ()).status == "not_evaluated"


def test_an_unresolved_named_export_cannot_authorize_a_global_name():
    files = local_files()
    files["value.ts"] = (
        b"import {fetch} from './local'; "
        b"export function f(n:number){fetch('https://example.test');return n;}"
    )
    assert service_module().review_dependencies(files, ()).status == "not_evaluated"


def test_a_resolved_local_alias_may_shadow_a_global_without_claiming_network_access():
    files = local_files()
    files["value.ts"] = (
        b"import {one as fetch} from './local'; export function f(n:number){return n + fetch;}"
    )
    assert service_module().review_dependencies(files, ()).status == "approved"


@pytest.mark.parametrize(
    "counts,unknown,expected",
    [
        ([3, 4, 4], [1, 0, 0], "not_evaluated"),
        ([4, 3, 4], [0, 1, 0], "not_evaluated"),
        ([3, 4, 4], [0, 0, 0], "rejected"),
        ([4, 3, 4], [0, 0, 0], "rejected"),
        ([4, 4, 4], [1, 0, 0], "approved"),
        ([0, 4, 4], [0, 0, 0], "rejected"),
    ],
)
def test_minimum_distinguishes_observed_deficit_from_unknown_possible_cases(
    counts, unknown, expected
):
    rule = getattr(service_module(), "case_minimum_status", None)
    assert rule is not None, (
        "H-F03 currently mistakes partial T11 observation for a factual deficit"
    )
    proofs = tuple(
        cases.CaseValidation(
            "evaluated",
            tuple(str(i) for i in range(count)),
            "target",
            "unit",
            "unit",
            unresolved_case_ids=tuple("u" + str(i) for i in range(potential)),
        )
        for count, potential in zip(counts, unknown, strict=True)
    )
    assert rule(proofs, minimum=4) == expected
