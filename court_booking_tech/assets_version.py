"""App version + a content-derived cache-buster for the portal and desk assets.

The asset links were bare ``/assets`` paths, so a returning browser (and a CDN)
kept the old stylesheet after a deploy: on 2026-09-03 an iPhone rendered the
2026-09-01 marketplace markup with the pre-09-01 CSS. Every link now carries
``?v=<app version>-<hash of the served files>``, so a deploy is a new URL.

No ``frappe`` import here on purpose: ``hooks.py`` evaluates ``cbt_asset_url``
at import time, before any site is connected, and a raise inside
``frappe.get_hooks`` would take the whole bench down with it.
"""

import hashlib
import os
from pathlib import Path

from court_booking_tech import __version__

PUBLIC_DIR = Path(__file__).resolve().parent / "public"

# Every file a ?v= is stamped on. A change to ANY of them changes the hash.
# Desk scripts pulled in with frappe.require are NOT here: frappe's own loader
# stamps those (frappe/public/js/frappe/assets.js).
ASSET_FILES = (
	"css/cbt_theme.css",
	"css/cbt_portal.css",
	"js/cbt_time_format.js",
	"js/cbt_portal.js",
	"js/cbt_time_control.js",
	"js/cbt_payment_channels.js",
)

_cache: tuple = ()  # (stat key, computed value)


def content_hash(paths) -> str:
	"""Eight hex chars over the files' bytes, name- and length-separated.

	Never raises: a missing file hashes as "missing" so the page still renders
	and ``test_app_version`` is what names the file.
	"""
	digest = hashlib.sha256(usedforsecurity=False)
	for path in paths:
		path = Path(path)
		digest.update(path.name.encode())
		try:
			data = path.read_bytes()
		except OSError:
			digest.update(b":missing")
			continue
		digest.update(f":{len(data)}:".encode())
		digest.update(data)
	return digest.hexdigest()[:8]


def _stat_key() -> tuple:
	key = []
	for rel in ASSET_FILES:
		try:
			st = os.stat(PUBLIC_DIR / rel)
			key.append((rel, st.st_mtime_ns, st.st_size))
		except OSError:
			key.append((rel, None, None))
	return tuple(key)


def asset_version() -> str:
	"""``<__version__>-<hash>``, recomputed only when a served file's mtime or
	size moves. Five stats per call; the dev server does not restart on a CSS
	edit, so a plain lru_cache would serve the stale stamp locally."""
	global _cache
	key = _stat_key()
	if not _cache or _cache[0] != key:
		_cache = (key, f"{__version__}-{content_hash(PUBLIC_DIR / rel for rel in ASSET_FILES)}")
	return _cache[1]


# ---- Jinja globals (hooks.py: jinja.methods) --------------------------------
def cbt_app_version() -> str:
	return __version__


def cbt_asset_version() -> str:
	return asset_version()


def cbt_asset_url(path: str) -> str:
	return f"{path}?v={asset_version()}"
