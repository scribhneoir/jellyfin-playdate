"""Shared raw .env loading (matching Compose env_file format: raw)."""
import os
import pathlib


def read_env(path):
    result = {}
    if pathlib.Path(path).exists():
        for line in pathlib.Path(path).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                key, separator, value = line.partition('=')
                if not separator or not key.strip().replace('_', '').isalnum():
                    raise ValueError('Invalid .env entry; use NAME=value without shell syntax')
                result[key.strip()] = value
    return result


def settings(path='.env'):
    return read_env(path) | dict(os.environ)
