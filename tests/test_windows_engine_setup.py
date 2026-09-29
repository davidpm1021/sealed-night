"""Run prerequisite checks in Windows PowerShell 5.1, the user's launcher shell."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell regression')
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'setup-xmage.ps1'


def check_setup(tmp_path, *, maven_java=21, maven_exit=0):
    # -version deliberately writes to stderr like the real Java executable.
    # --version writes to stdout. No network or engine checkout is needed.
    (tmp_path / 'java.cmd').write_text(
        '@echo off\nif "%~1"=="--version" (\n'
        ' echo openjdk 21.0.12.1 2026-08-18 LTS\n'
        ') else (\n echo openjdk version "21.0.12.1" 1>&2\n)\nexit /b 0\n'
    )
    (tmp_path / 'mvn.cmd').write_text(
        f'@echo off\necho Apache Maven 3.9.16\necho Java version: {maven_java}.0.1, vendor: Test\nexit /b {maven_exit}\n'
    )
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH']}
    return subprocess.run(
        [shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
         '-File', str(SCRIPT), '-CheckOnly'],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_java_stdout_version_check_works_in_windows_powershell(tmp_path):
    result = check_setup(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'XMage prerequisites passed.' in result.stdout


def test_maven_using_old_java_reports_java_home_fix(tmp_path):
    result = check_setup(tmp_path, maven_java=17)
    assert result.returncode != 0
    assert 'Set JAVA_HOME to your Java 21 JDK' in result.stderr


def test_maven_failure_does_not_claim_prerequisites_passed(tmp_path):
    result = check_setup(tmp_path, maven_exit=7)
    assert result.returncode != 0
    assert 'Maven could not start' in result.stderr
    assert 'prerequisites passed' not in result.stdout
