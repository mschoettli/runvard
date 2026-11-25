"""Preserve the local data, environment, and systemd service during upgrades."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def records(text):
    """Keep complete assignments together, including quoted continuation lines."""
    lines = iter(text.splitlines(keepends=True))
    for raw in lines:
        match = re.match(r'^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=', raw)
        if not match:
            yield None, raw, None
            continue
        while True:
            value = raw[match.end():].strip()
            if value.startswith('"'):
                quoted = re.fullmatch(r'"((?:\\.|[^"\\])*)"(.*)', value, re.S)
                if quoted:
                    decoded = re.sub(r'\\(["\\$`\n])', lambda m: '' if m[1] == '\n' else m[1], quoted[1]) + quoted[2]
                    break
            elif value.startswith("'"):
                quoted = re.fullmatch(r"'([^']*)'(.*)", value, re.S)
                if quoted:
                    decoded = quoted[1] + quoted[2]
                    break
            elif (len(value) - len(value.rstrip('\\'))) % 2 == 0:
                # systemd treats quotes inside an unquoted value as literal.
                decoded = re.sub(r'\\(.)', lambda m: '' if m[1] == '\n' else m[1], value, flags=re.S)
                break
            continuation = next(lines, None)
            if continuation is None:
                raise ValueError(f'Invalid assignment for {match[1]}')
            raw += continuation
        yield match[1], raw, decoded


def replace_file(path, text, mode):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as tmp:
        try:
            tmp.write(text)
            tmp.flush()
            os.fchmod(tmp.fileno(), mode)
            os.fsync(tmp.fileno())
            os.replace(tmp.name, path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def merge_environment(path, entries, port, lang, default_data_dir):
    output = []
    data_dir_exists = False
    for key, raw, value in entries:
        # Bootstrap credentials have been migrated into the account database.
        if key in {'RUNVARD_PORT', 'RUNVARD_LANG', 'RUNVARD_USER', 'RUNVARD_PASS'}:
            continue
        if key == 'RUNVARD_DATA_DIR':
            data_dir_exists = bool(value)
            if not data_dir_exists:
                continue
        output.append(raw)
    if output and not output[-1].endswith('\n'):
        output.append('\n')
    if not data_dir_exists:
        output.append(f'RUNVARD_DATA_DIR={default_data_dir}\n')
    output.extend([f'RUNVARD_PORT={port}\n', f'RUNVARD_LANG={lang}\n'])
    replace_file(path.resolve(), ''.join(output), 0o600)


def configure_service(path, template, previous_port, chosen_port):
    # Preserve systemd aliases and masks, including links with missing targets.
    if path.is_symlink():
        return
    if not path.exists():
        replace_file(path, Path(template).read_text(), 0o644)
        return
    text = path.read_text()
    old_start = ('ExecStart=/opt/runvard/venv/bin/uvicorn server:app '
                 f'--host 0.0.0.0 --port {previous_port} --workers 1')
    port_pattern = f'--port {previous_port}'
    lines = []
    for line in text.splitlines(keepends=True):
        # Migrate our exact legacy command to use the EnvironmentFile port.
        # On an explicit port change also keep a customized uvicorn command in sync.
        if line.rstrip('\n') == old_start or (
            previous_port != chosen_port and line.startswith('ExecStart=/opt/runvard/venv/bin/uvicorn ')
        ):
            line = re.sub(re.escape(port_pattern) + r'(?=\s|$)', '--port ${RUNVARD_PORT}', line, count=1)
        lines.append(line)
    updated = ''.join(lines)
    if updated != text:
        replace_file(path, updated, path.stat().st_mode & 0o777)


def storage_paths(path):
    """Include each symlink along a configured path as well as its final target."""
    path = Path(os.path.abspath(path))
    seen = set()
    while path not in seen:
        seen.add(path)
        for part in (*reversed(path.parents), path):
            if part.is_symlink():
                yield part
                path = Path(os.path.abspath(part.parent / os.readlink(part) / path.relative_to(part)))
                break
        else:
            yield path
            return
    raise ValueError(f'Symlink loop in {path}')


def sync_program(source, installation, data_dir):
    installation = Path(installation).resolve()
    protected = set(storage_paths(data_dir))
    protected.update(storage_paths(installation / 'data' / 'runvard.env'))
    excluded = ['data', 'venv', '.venv', '.git', '.pytest_cache', '__pycache__', '*.pyc', '*.bak*', 'tests']
    for path in sorted(protected):
        if installation in path.parents:
            name = path.relative_to(installation).as_posix()
            excluded.append('/' + re.sub(r'([?*\[\\])', r'\\\1', name))
    command = ['rsync', '-a']
    if installation not in protected:
        command.append('--delete')
    for pattern in excluded:
        command.extend(['--exclude', pattern])
    subprocess.run([*command, str(source) + '/', str(installation) + '/'], check=True)


if __name__ == '__main__':
    command, filename, *args = sys.argv[1:]
    path = Path(filename)
    if command == 'sync':
        sync_program(path, *args)
    elif command == 'service':
        configure_service(path, *args)
    else:
        entries = list(records(path.read_text() if path.exists() else ''))
        if command == 'get':
            values = {key: value for key, _, value in entries}
            print(values.get(args[0], ''))
        elif command == 'merge':
            merge_environment(path, entries, *args)
        else:
            raise SystemExit(2)
