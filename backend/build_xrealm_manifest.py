"""Generate SHA-256 metadata from the actual public xREALM downloads.
Run only by the server administrator. Do not run on player machines.
"""
import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse
import requests
from xrealm import discover

p = argparse.ArgumentParser(description='Build authoritative xREALM manifest from downloaded ZIP bytes')
p.add_argument('--page', default='http://46.4.129.30:20721/mods.html')
p.add_argument('--output', default=str(Path(__file__).resolve().parent / 'xrealm_manifest.json'))
p.add_argument('--cache', default='', help='Optional directory to keep downloaded ZIPs for re-use')
p.add_argument('--active-list', default='', help='Optional text file: one verified active ZIP filename per line')
a = p.parse_args()
links = discover(a.page)
if not links:
    raise SystemExit('Keine öffentlichen ZIP-Links gefunden.')
if a.active_list:
    active = {line.strip() for line in Path(a.active_list).read_text(encoding='utf-8-sig').splitlines() if line.strip() and not line.lstrip().startswith('#')}
    missing = active - links.keys()
    if missing:
        raise SystemExit('Aktive Mods ohne öffentlichen Link: ' + ', '.join(sorted(missing)))
    links = {name: url for name, url in links.items() if name in active}
else:
    print('ACHTUNG: Ohne --active-list werden alle öffentlich verlinkten ZIPs aufgenommen. Links allein beweisen nicht den Aktiv-Status.')
cache = Path(a.cache).resolve() if a.cache else None
if cache:
    cache.mkdir(parents=True, exist_ok=True)
mods = []
errors = []
origin = urlparse(a.page)
for index, (name, url) in enumerate(sorted(links.items()), 1):
    print(f'[{index}/{len(links)}] Prüfe xREALM-Datei: {name}', flush=True)
    try:
        if Path(name).name != name or not name.lower().endswith('.zip'):
            raise ValueError('Unsicherer Dateiname')
        destination = cache / name if cache else None
        if destination is None or not destination.is_file():
            fd, temporary = tempfile.mkstemp(prefix='xrealm-', suffix='.part', dir=str(cache) if cache else None)
            os.close(fd)
            try:
                with requests.get(url, stream=True, timeout=(20, 120), allow_redirects=True) as response:
                    response.raise_for_status()
                    resolved = urlparse(response.url)
                    if resolved.scheme != origin.scheme or resolved.netloc != origin.netloc:
                        raise ValueError('Download-Weiterleitung zu fremdem Server verweigert')
                    with open(temporary, 'wb') as output:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                output.write(chunk)
                if destination:
                    os.replace(temporary, destination)
            except Exception:
                if os.path.exists(temporary):
                    os.unlink(temporary)
                raise
            path = destination or Path(temporary)
            # If no cache was requested, keep the temporary file until hashing is done.
        else:
            path = destination
        if not path.is_file():
            raise ValueError('Download-Datei fehlt')
        try:
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise ValueError('ZIP-CRC-Fehler: ' + bad)
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    digest.update(block)
            mods.append({'filename': name, 'size': path.stat().st_size, 'sha256': digest.hexdigest(), 'download_url': url})
        finally:
            if not cache:
                path.unlink(missing_ok=True)
    except Exception as exc:
        errors.append(f'{name}: {exc}')
        print('FEHLER:', errors[-1], flush=True)
if errors:
    raise SystemExit(f'{len(errors)} Downloads/ZIP-Prüfungen fehlgeschlagen. Altes Manifest bleibt unverändert; Fehler beheben und erneut ausführen.')
if not mods:
    raise SystemExit('Keine Mods gefunden; Manifest bleibt unverändert.')
output = Path(a.output)
output.parent.mkdir(parents=True, exist_ok=True)
fd, temp = tempfile.mkstemp(prefix='manifest-', suffix='.tmp', dir=output.parent)
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        json.dump({'mods': mods}, file, ensure_ascii=False, indent=2)
    os.replace(temp, output)
finally:
    if os.path.exists(temp):
        os.unlink(temp)
print(f'Fertig: {len(mods)} xREALM-Dateien geprüft. Manifest: {output}')
