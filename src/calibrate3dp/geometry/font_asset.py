"""Pinned, redistributable physical-label font identity."""

import hashlib
from pathlib import Path

FONT_ASSET_ID = "ibm-plex-mono-semibold-2017"
FONT_RELATIVE_PATH = "assets/fonts/IBMPlexMono-SemiBold.ttf"
FONT_SHA256 = "f04d7c488ddf7d1fa99f2574efc3406ea4cbe17bb1af3a1ab960f84d0c96a172"
FONT_STYLE = "IBM Plex Mono SemiBold"


def font_asset_path() -> Path:
    return Path(__file__).resolve().parents[1] / FONT_RELATIVE_PATH


def is_pinned_font_available() -> bool:
    path = font_asset_path()
    if not path.is_file():
        return False
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest == FONT_SHA256
