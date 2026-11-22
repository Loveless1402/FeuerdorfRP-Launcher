"""Opt-in public xREALM discovery. Only literal same-origin ZIP links are accepted."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, unquote
import requests

class ZipLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = dict(attrs).get('href', '')
        if href:
            self.links.append(href)

def discover(page_url):
    origin = urlparse(page_url)
    if origin.scheme not in ('http', 'https') or not origin.hostname:
        raise ValueError('Ungültige xREALM-Adresse')
    response = requests.get(page_url, timeout=20)
    response.raise_for_status()
    parser = ZipLinks()
    parser.feed(response.text)
    found = {}
    for href in parser.links:
        url = urljoin(page_url, href)
        parsed = urlparse(url)
        if parsed.scheme != origin.scheme or parsed.netloc != origin.netloc:
            continue
        name = unquote(parsed.path.rsplit('/', 1)[-1])
        if not name.lower().endswith('.zip') or '/' in name or '\\' in name or name in ('.', '..'):
            continue
        found[name] = url
    return found
