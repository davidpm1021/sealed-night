"""Exercise real Git long-path and incomplete-checkout recovery in PowerShell 5.1."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows checkout regression')
HELPER = Path(__file__).resolve().parents[1] / 'scripts' / 'xmage-checkout.ps1'


def git(*args):
    return subprocess.run(['git', *map(str, args)], check=True, capture_output=True, text=True).stdout.strip()


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize('incomplete', [False, True])
def test_long_path_checkout_and_safe_retry(tmp_path, incomplete):
    source = tmp_path / 'source'
    git('init', source)
    git('-C', source, 'config', 'core.longpaths', 'true')
    relative = Path('sample-decks') / ('a' * 70) / ('b' * 70) / ('c' * 60 + '.dck')
    original = Path('\\\\?\\' + str(source / relative))
    original.parent.mkdir(parents=True)
    original.write_text('test deck')
    git('-C', source, 'add', '.')
    git('-C', source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
    revision = git('-C', source, 'rev-parse', 'HEAD')
    destination = tmp_path / 'engine'
    if incomplete:
        git('clone', '--no-checkout', source, destination)
        (destination / 'preserve-me.txt').write_text('existing user content')

    command = (
        "$ErrorActionPreference = 'Stop'; . " + ps_quote(HELPER)
        + '; Initialize-XMageCheckout -Directory ' + ps_quote(destination)
        + ' -Revision ' + ps_quote(revision) + ' -Repository ' + ps_quote(source)
    )
    for _ in range(2):
        result = subprocess.run(
            [shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert git('-C', destination, 'rev-parse', 'HEAD') == revision
        assert git('-C', destination, 'config', '--local', 'core.longpaths') == 'true'
        assert len(str(destination / relative)) > 260
        assert Path('\\\\?\\' + str(destination / relative)).read_text() == 'test deck'

    backups = list(tmp_path.glob('engine.incomplete-*'))
    assert len(backups) == int(incomplete)
    if incomplete:
        assert (backups[0] / 'preserve-me.txt').read_text() == 'existing user content'
    # The repaired clone is independent of its backup's object database.
    assert not (destination / '.git' / 'objects' / 'info' / 'alternates').exists()
