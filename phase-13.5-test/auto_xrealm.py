"""Conservative background refresh of public xREALM ZIP metadata.

No player data is removed. The last good manifest is kept if any scan fails.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlparse

import requests
from xrealm import discover_active

LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_STATE = {'last_success': None, 'last_error': None, 'running': False, 'active_count': None}


def state():
    with _LOCK:
        return dict(_STATE)


def _set(**kwargs):
    with _LOCK:
        _STATE.update(kwargs)


def _verify_and_hash(name, url, page_url, max_bytes):
    origin = urlparse(page_url)
    digest = hashlib.sha256()
    size = 0
    with requests.get(url, stream=True, timeout=(15, 90), allow_redirects=True) as response:
        response.raise_for_status()
        target = urlparse(response.url)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            raise ValueError('Fremde Download-Weiterleitung: ' + name)
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError('Datei überschreitet Größenlimit: ' + name)
                digest.update(chunk)
    if size == 0:
        raise ValueError('Leere ZIP-Datei: ' + name)
    return {'filename': name, 'size': size, 'sha256': digest.hexdigest(), 'download_url': url}


def refresh(page_url, manifest_path, *, full=False):
    """Atomically publish only a complete, verified list of active downloadable mods.

    Existing entries are reused on quick scans. A periodic full scan re-hashes
    every ZIP, detecting even same-size replacements.
    """
    manifest_path = Path(manifest_path)
    links = discover_active(page_url)
    existing = {}
    if manifest_path.is_file():
        old = json.loads(manifest_path.read_text(encoding='utf-8'))
        existing = {item['filename']: item for item in old.get('mods', [])
                    if isinstance(item, dict) and isinstance(item.get('filename'), str)}
    max_bytes = int(os.getenv('XREALM_MAX_MOD_BYTES', str(3 * 1024 ** 3)))
    mods = []
    for name, url in sorted(links.items()):
        cached = existing.get(name)
        if (not full and cached and cached.get('download_url') == url
            and isinstance(cached.get('size'), int) and cached['size'] > 0
            and isinstance(cached.get('sha256'), str) and len(cached['sha256']) == 64
            and all(c in '0123456789abcdef' for c in cached['sha256'].lower())):
            # A quick scan detects additions, removals and URL changes.
            # Same-URL replacements are detected by the periodic full scan.
            mods.append(cached)
        else:
            mods.append(_verify_and_hash(name, url, page_url, max_bytes))
    if not mods:
        raise ValueError('Keine aktiven Mods erkannt; bisheriges Manifest bleibt erhalten')
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.xrealm-manifest-', suffix='.json', dir=manifest_path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump({'mods': mods}, output, ensure_ascii=False, indent=2)
        os.replace(temp, manifest_path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    _set(last_success=time.time(), last_error=None, active_count=len(mods))
    LOG.info('xREALM manifest updated: %d active downloadable mods', len(mods))
    return len(mods)


def start_worker(page_url, manifest_path):
    interval = max(300, int(os.getenv('XREALM_REFRESH_SECONDS', '1800')))
    full_interval = max(interval, int(os.getenv('XREALM_FULL_VERIFY_SECONDS', '86400')))

    def run():
        last_full = time.monotonic()
        while True:
            _set(running=True)
            try:
                now = time.monotonic()
                full = now - last_full >= full_interval
                refresh(page_url, manifest_path, full=full)
                if full:
                    last_full = time.monotonic()
            except Exception as exc:
                LOG.exception('xREALM refresh failed; last good manifest preserved')
                _set(last_error=str(exc))
            finally:
                _set(running=False)
            time.sleep(interval)

    threading.Thread(target=run, name='xrealm-manifest-refresh', daemon=True).start()
