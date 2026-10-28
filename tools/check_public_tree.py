"""Reject generated content and credentials from the public Git tree."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {'.py', '.c', '.cpp', '.h', '.cs', '.cjs', '.java', '.md', '.json',
              '.ini', '.uproject', '.yaml', '.yml', '.toml'}
ROOT_FILES = {'.gitignore', '.gitattributes', 'LICENSE'}
BLOCKED = ('local/', 'evidence/', '.tools/', 'unreal/Content/', 'unreal/Saved/',
           'unreal/Intermediate/', 'unreal/Binaries/', 'unreal/DerivedDataCache/')
SECRETS = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|'
                     rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def main():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    failures = []
    for name in filter(None, names):
        path = ROOT/name
        if name.startswith(BLOCKED) or (path.suffix not in EXTENSIONS and name not in ROOT_FILES):
            failures.append((name, 'not an approved public source/documentation file'))
            continue
        raw = path.read_bytes()
        if b'\0' in raw or SECRETS.search(raw):
            failures.append((name, 'binary data or credential pattern'))
    for name, reason in failures:
        print(f'{name}: {reason}')
    if not failures:
        print('Public source tree check passed.')
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
