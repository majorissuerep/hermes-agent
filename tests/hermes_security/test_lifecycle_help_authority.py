"""Operation-only children can discover lifecycle help without gaining mutation authority."""
import shlex

import pytest

from hermes_cli.management import schema
from hermes_security.authority_native import checked_arguments, profile_arguments


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
        for prefix in [['--model', 'synthetic'], ['--provider=synthetic'],
                       ['--query=synthetic'], ['--no-banner'], ['--profile=foreign']]:
            with pytest.raises(ValueError):
                checked_arguments([*prefix, family, '--help'])
    with pytest.raises(ValueError):
        checked_arguments(['unknown-command', '--help'])


@pytest.mark.parametrize('help_flag', ['--help', '-h'])
def test_owner_profile_prefix_routes_help_without_foreign_admission_or_filesystem_effects(
        tmp_path, monkeypatch, help_flag):
    # Argument routing only: public profile existence marker is not vault admission.
    home = tmp_path / 'owned' / 'profiles' / 'work'
    home.mkdir(parents=True)
    (home / 'config.yaml').write_bytes(b'public synthetic profile existence marker')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'unrelated-launch-home'))

    def inventory():
        return {str(path.relative_to(tmp_path)): (path.stat().st_mode,
                    path.read_bytes() if path.is_file() else None)
                for path in tmp_path.rglob('*')}

    before = inventory()
    for family in ('plugins', 'secure-vault', 'update', 'pm'):
        arguments = [family, help_flag]
        for prefix in (['-p', 'Work'], ['--profile=work']):
            selected = profile_arguments(home, [*prefix, *arguments])
            assert selected == arguments
            assert checked_arguments(selected) == arguments
        for foreign in ('default', 'other'):
            with pytest.raises(ValueError, match='cannot select another profile'):
                profile_arguments(home, ['--profile=' + foreign, *arguments])
    assert inventory() == before
