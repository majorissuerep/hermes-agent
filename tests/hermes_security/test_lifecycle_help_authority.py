"""Operation-only children can discover lifecycle help without gaining mutation authority."""
import shlex

import pytest

from hermes_cli.management import schema
from hermes_security.authority_native import checked_arguments


@pytest.mark.parametrize('help_flag', ['--help', '-h'])
def test_schema_lifecycle_help_is_available_to_operation_children(help_flag):
    for command in schema()['lifecycle_commands']:
        arguments = shlex.split(command)[1:]
        assert arguments[-1] == '--help'
        arguments[-1] = help_flag
        assert checked_arguments(arguments) == arguments
        nested = ["manage", "run", "hermes", "--", *arguments]
        assert checked_arguments(nested) == nested


def test_help_only_families_keep_mutations_extra_arguments_and_unknown_commands_refused():
    mutations = {
        'plugins': ['install', 'synthetic-plugin'],
        'secure-vault': ['export', '--output', 'synthetic-output'],
        'update': [],
        'pm': ['run', 'python', '-c', 'synthetic'],
    }
    malformed = [
        ['--help', 'run', 'python', '-c', 'synthetic'],
        ['--help', '--run-module', 'synthetic'],
        ['--help', '--profile=foreign'],
        ['--help', '--yolo'],
        ['--help=synthetic'],
        ['--', '--help'],
        ['-hsynthetic'],
        ['-h', '--help'],
    ]
    for family, arguments in mutations.items():
        for suffix in [arguments, *malformed]:
            with pytest.raises(ValueError):
                checked_arguments([family, *suffix])
    with pytest.raises(ValueError):
        checked_arguments(['unknown-command', '--help'])
