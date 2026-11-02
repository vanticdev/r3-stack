"""Native YAML parsing retains duplicate, alias and unsafe-tag diagnostics."""
import pytest
import yaml

from r3_model.spec_files import SpecError, _parse


def test_large_description_avoids_python_yaml_scanner(monkeypatch):
    if not hasattr(yaml, 'CSafeLoader'):
        pytest.skip('Optional libyaml accelerator is unavailable')

    def slow_scanner(*args):
        raise AssertionError('Description used the Python YAML scanner')

    monkeypatch.setattr(yaml.scanner.Scanner, 'check_token', slow_scanner)
    assert _parse('value: 2e-6\nitems: [1, 2, 3]\n', 'fixture.yaml') == {
        'value': 2e-6, 'items': [1, 2, 3]}


@pytest.mark.parametrize('text,code', [
    ('a: 1\na: 2\n', 'spec_duplicate_definition'),
    ('group: {a: 1, a: 2}\n', 'spec_duplicate_definition'),
    ('a: &cycle [*cycle]\n', 'spec_include_cycle'),
    ('a: !!python/name:builtins.eval ignored\n', 'spec_yaml_invalid'),
    ('a: [1, 2\n', 'spec_yaml_invalid'),
])
def test_acceleration_keeps_parser_guards(text, code):
    with pytest.raises(SpecError) as error:
        _parse(text, 'fixture.yaml')
    assert error.value.code == code
    assert error.value.errors[0]['file'] == 'fixture.yaml'
