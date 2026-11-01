"""Private MCP crash evidence must not rewrite protocol traffic or retry tools."""
import json
import signal
import subprocess
import sys

import pytest

from r3_orchestrator import mcp_process


def command(directory, *child):
    return [sys.executable, mcp_process.__file__, '--directory', str(directory), '--', *child]


def test_protocol_bytes_stderr_and_exit_are_preserved_without_retry(tmp_path):
    directory = tmp_path / 'diagnostics'
    counter = tmp_path / 'invocations.txt'
    script = ('import sys; from pathlib import Path; '
              f'p=Path({str(counter)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x"); '
              'sys.stdout.buffer.write(sys.stdin.buffer.read()); '
              'sys.stderr.write("synthetic child failure\\n"); sys.exit(7)')
    payload = b'{"jsonrpc":"2.0","id":1}\n\x00\xff\n'
    result = subprocess.run(command(directory, sys.executable, '-c', script), input=payload,
                            capture_output=True, timeout=15)
    assert result.returncode == 7
    assert result.stdout == payload
    assert result.stderr == b'synthetic child failure\n'
    assert counter.read_text() == 'x'
    assert (directory / 'stderr.log').read_bytes() == result.stderr
    state = json.loads((directory / 'process.json').read_text())
    assert state['returncode'] == 7 and state['stderr_complete']
    assert state['child_pid'] != state['wrapper_pid']
    assert state['stderr_bytes'] == len(result.stderr) and not state['stderr_truncated']
    assert not (directory / 'stderr.log').stat().st_mode & 0o077
    assert not (directory / 'process.json').stat().st_mode & 0o077


def test_stderr_capture_is_bounded_but_continues_draining(tmp_path):
    directory = tmp_path / 'diagnostics'
    size = mcp_process.STDERR_LIMIT + 123
    result = subprocess.run(command(directory, sys.executable, '-c',
        f'import sys; sys.stderr.buffer.write(b"x"*{size}); print("completed")'),
        capture_output=True, timeout=15)
    assert result.returncode == 0 and result.stdout == b'completed\n'
    assert len(result.stderr) == size
    assert (directory / 'stderr.log').stat().st_size == mcp_process.STDERR_LIMIT
    state = json.loads((directory / 'process.json').read_text())
    assert state['stderr_bytes'] == size and state['stderr_truncated']


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX MCP process signals')
def test_termination_is_forwarded_and_signal_exit_recorded(tmp_path):
    directory = tmp_path / 'diagnostics'
    process = subprocess.Popen(command(directory, sys.executable, '-c',
        'import time; print("ready",flush=True); time.sleep(60)'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdout.readline() == b'ready\n'
        process.terminate()
        assert process.wait(timeout=10) == 128 + signal.SIGTERM
        state = json.loads((directory / 'process.json').read_text())
        assert state['returncode'] == -signal.SIGTERM
        assert state['forwarded_signals'] == [signal.SIGTERM]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_launch_error_is_recorded_without_a_child(tmp_path):
    directory = tmp_path / 'diagnostics'
    result = subprocess.run(command(directory, str(tmp_path / 'missing-executable')),
                            capture_output=True, timeout=10)
    assert result.returncode == 127 and not result.stdout
    state = json.loads((directory / 'process.json').read_text())
    assert state['returncode'] == 127 and state['launch_error']
    assert 'child_pid' not in state
