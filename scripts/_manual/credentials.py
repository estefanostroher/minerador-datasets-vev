"""Read only the explicitly authorized token key; never execute dotenv content."""

import getpass
import os
import re
import sys

from .errors import InvalidGitHubToken, MissingGitHubToken


def load_token(root, *, environ=None, interactive=None):
    environ = os.environ if environ is None else environ
    token = environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    for name in (".env", ".env.example"):
        path = root / name
        if not path.is_file():
            continue
        found = None
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeError:
            raise InvalidGitHubToken() from None
        for line in lines:
            if not re.match(r"^\s*(?:export\s+)?GITHUB_TOKEN\b", line):
                continue
            match = re.fullmatch(r"\s*(?:export\s+)?GITHUB_TOKEN\s*=\s*(.*?)\s*", line)
            if not match or found is not None:
                raise InvalidGitHubToken()
            value = match[1]
            if value.startswith(("'", '"')):
                quoted = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
                if not quoted:
                    raise InvalidGitHubToken()
                found = quoted[2]
            else:
                found = re.split(r"\s+#", value, maxsplit=1)[0].strip()
                if re.search(r"\s|['\"`$]", found):
                    raise InvalidGitHubToken()
        if found is not None:
            if not found.strip():
                raise InvalidGitHubToken()
            return found.strip()
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stderr.isatty()
    if interactive:
        token = getpass.getpass("GITHUB_TOKEN (oculto): ").strip()
        if token:
            return token
    raise MissingGitHubToken()
