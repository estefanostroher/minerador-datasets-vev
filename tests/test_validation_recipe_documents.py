import pytest

from robo_minerador.validation_recipe_documents import classify_documents, document_candidates


def classify(text, extra=None):
    files = {'README.md': text, **(extra or {})}
    return classify_documents(files, list(files), '.')


def codes(result):
    return [issue['code'] for issue in result['issues']]


def test_candidates_are_casefolded_deduplicated_and_sorted():
    assert document_candidates(['pkg/docs/Testing.md', 'README.MD', 'else/README.md', 'pkg/README.md'], 'pkg') == ['README.MD', 'pkg/README.md', 'pkg/docs/Testing.md']


@pytest.mark.parametrize('heading', ['Quality', 'QUALITÉ', 'Qualidade', 'test', 'testing', 'tests', 'tests unitaires', 'unit tests', 'testes'])
def test_recognized_headings_confirm_fenced_test(heading):
    result = classify(f'## {heading} ##\r\n```sh\r\nnpm ci\r\n$ npm test\r\nnpm run lint\r\n```\r\n')
    assert result['confirmed']
    assert not result['issues']
    assert result['evidence']


@pytest.mark.parametrize('prefix', ['✅ ', '✅️ '])
def test_quality_heading_with_checkmark(prefix):
    assert classify(f'## {prefix}Qualité\n```\nnpm test\nnpm run lint\n```')['confirmed']


@pytest.mark.parametrize('heading', ['✅ Deploy', '✅ Tests', '✅ Quality extra', '✅ ✅ Quality', '✅ 🔥 Quality', '🔥 Quality', '✅Quality'])
def test_checkmark_does_not_broaden_heading_grammar(heading):
    assert not classify(f'## {heading}\n```\nnpm test\n```')['confirmed']


@pytest.mark.parametrize('text', ['## Deploy\n`npm test`', '## Testing stuff\n`npm test`', 'Testing\n=======\n`npm test`', '## Testing\nnpm test'])
def test_unrecognized_document_does_not_confirm(text):
    assert 'UNIT_SCOPE_UNRESOLVED' in codes(classify(text))


@pytest.mark.parametrize('command,expected', [('npm run build', 'AUTO_RECIPE_PREPARATION_UNRESOLVED'), ('npm lint', 'AUTO_RECIPE_PREPARATION_UNRESOLVED'), ('npm test -- --run', 'AUTO_RECIPE_PREPARATION_UNRESOLVED')])
def test_commands_before_test_block_preparation(command, expected):
    assert expected in codes(classify(f'## Tests\n```\n{command}\nnpm test\n```'))


def test_unknown_after_test_and_without_test_block_scope():
    for text in ('`npm test` `npm run build`', '`npm run build`'):
        assert 'UNIT_SCOPE_UNRESOLVED' in codes(classify('## Tests\n' + text))


@pytest.mark.parametrize('text', ['## Tests\n````sh\nnpm test\n````', '## Tests\n~~~\nnpm test\n~~~', '## Tests\n``bash``', '## Tests\n```python\nnpm test\n```', '## Tests\n```\nnpm test'])
def test_unsupported_code_syntax_is_ambiguous(text):
    assert not classify(text)['confirmed']


def test_fenced_fake_headings_and_deploy_are_opaque():
    result = classify('```md\n## Tests\n```\n## Deploy\n```\nlogin\n```\n## Tests\n`npm test`')
    assert result['confirmed'] and not result['issues']


def test_subsections_overlap_without_duplicating_evidence():
    result = classify('## Tests\n### Quality\n`npm test`\n## Deploy\n`unknown`')
    assert result['confirmed'] and len(result['evidence']) == 1


def test_conflicting_documents_and_ci_always_block():
    result = classify('## Tests\n`npm test`', {'docs/testing.md': '## Tests\n`npm test` `unknown`', '.GitHub/Workflows/nested/CI.YAML': 'anything'})
    assert not result['confirmed']
    assert any(i['evidence'].get('reason') == 'CI_WORKFLOW_UNSUPPORTED' for i in result['issues'])
    assert len(result['issues']) >= 2


@pytest.mark.parametrize('path', ['.gitlab-ci.yml', '.circleci/config.yml', 'azure-pipelines.yml', '.travis.yml', 'Jenkinsfile'])
def test_other_ci_markers_block(path):
    assert not classify('## Tests\n`npm test`', {path: ''})['confirmed']


def test_competing_spellings_and_missing_contents_fail_closed():
    result = classify_documents({'README.md': '## Tests\n`npm test`'}, ['README.md', 'readme.MD'], '.')
    assert not result['confirmed']
    assert 'UNIT_SCOPE_UNRESOLVED' in codes(result)


def test_ambiguous_input_precedes_ci_then_preparation_and_scope():
    files = {'README.md': '## Tests\n`npm run build` `npm test` `bad`',
             'readme.md': '', '.github/workflows/a.yml': ''}
    result = classify_documents(files, list(reversed(list(files))), '.')
    assert [i['code'] for i in result['issues']][:3] == [
        'UNIT_SCOPE_UNRESOLVED', 'UNIT_SCOPE_UNRESOLVED',
        'AUTO_RECIPE_PREPARATION_UNRESOLVED']
    assert result['issues'][1]['evidence']['reason'] == 'CI_WORKFLOW_UNSUPPORTED'


def test_known_only_section_does_not_cancel_other_document():
    result = classify('## Tests\n`npm test`', {'docs/testing.md': '## Quality\n`npm ci` `npm lint`'})
    assert result['confirmed']


def test_unclosed_fence_outside_section_blocks():
    assert not classify('## Tests\n`npm test`\n## Deploy\n~~~\nanything')['confirmed']


@pytest.mark.parametrize('command', ['npm run test', 'npm test --run', 'NPM test', 'cd app && npm test', '$ $ npm test', 'npm test # comment'])
def test_exact_commands_only(command):
    assert not classify('## Tests\n```\n' + command + '\n```')['confirmed']


def test_overlapping_sections_use_first_test_of_union():
    result = classify('## Tests\n`npm test`\n### Quality\n`npm lint` `npm test`')
    assert result['confirmed']
    assert len(result['evidence']) == 2


def test_overlapping_sections_unknown_after_first_test_is_scope_issue_once():
    result = classify('## Tests\n`npm test`\n### Quality\n`npm run build` `npm test`')
    assert codes(result) == ['UNIT_SCOPE_UNRESOLVED']
    assert len(result['evidence']) == 2


def test_inventory_not_files_drives_selection_and_ci():
    result = classify_documents({'README.md': '## Tests\n`npm test`', 'docs/testing.md': '## Tests\n`bad`'}, ['README.md', '.github/workflows/a.yml'], '.')
    assert not result['confirmed']
    assert len(result['evidence']) == 1
    assert result['issues'][0]['evidence']['reason'] == 'CI_WORKFLOW_UNSUPPORTED'
