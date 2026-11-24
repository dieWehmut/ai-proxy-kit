#!/usr/bin/env python3
"""Export a deterministic, credential-free OpenClaw profile for review.

The runtime configuration contains live QQ, gateway and provider credentials.
This exporter preserves the configuration shape and non-secret settings while
replacing credential values, then rejects output that still looks like a
secret. The result is suitable for a public Git branch, but is not a drop-in
openclaw.json because the redacted values (and .env secrets) are intentional.
"""
import argparse
import copy
import json
import pathlib
import re
import sys

REDACTED = "<redacted>"
SENSITIVE_EXACT = {
    "token",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "apikey",
    "password",
    "secret",
    "clientsecret",
    "cookie",
    "authorization",
}
SENSITIVE_SUFFIXES = ("token", "secret", "password", "apikey", "cookie")
LEAK_PATTERNS = (
    re.compile(r"^sk-[A-Za-z0-9_-]{16,}$"),
    re.compile(r"^eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$"),
    re.compile(r"^[A-Fa-f0-9]{32,}$"),
)


def _is_sensitive_key(key):
    lowered = key.lower()
    return lowered in SENSITIVE_EXACT or lowered.endswith(SENSITIVE_SUFFIXES)


def sanitize(data):
    """Return a deep-copied config with credential values removed."""
    if not isinstance(data, dict):
        raise ValueError("OpenClaw configuration must be a JSON object")
    result = copy.deepcopy(data)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and _is_sensitive_key(key):
                    node[key] = REDACTED
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(result)
    # Timestamps are not secrets, but make profile comparison noisy on every
    # wizard/doctor invocation. Keep semantic version metadata.
    wizard = result.get("wizard")
    if isinstance(wizard, dict):
        wizard.pop("lastRunAt", None)
    validate_no_obvious_leaks(result)
    return result


def _paths_with_obvious_leaks(node, path=()):
    leaks = []
    if isinstance(node, dict):
        for key, value in node.items():
            leaks.extend(_paths_with_obvious_leaks(value, (*path, str(key))))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            leaks.extend(_paths_with_obvious_leaks(value, (*path, str(index))))
    elif isinstance(node, str) and node != REDACTED:
        if any(pattern.fullmatch(node) for pattern in LEAK_PATTERNS):
            leaks.append(".".join(path))
    return leaks


def validate_no_obvious_leaks(data):
    """Raise if a credential-shaped string survived sanitization."""
    leaks = _paths_with_obvious_leaks(data)
    if leaks:
        raise ValueError("credential-shaped values remain at: " + ", ".join(leaks))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path, help="runtime openclaw.json to sanitize")
    parser.add_argument("destination", type=pathlib.Path, help="public profile JSON to write")
    args = parser.parse_args()

    if args.source.resolve() == args.destination.resolve():
        raise ValueError("refusing to overwrite the runtime configuration")
    source = json.loads(args.source.read_text(encoding="utf-8"))
    profile = sanitize(source)
    payload = json.dumps(profile, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(payload, encoding="utf-8")
    print(f"Wrote credential-free profile to {args.destination}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
