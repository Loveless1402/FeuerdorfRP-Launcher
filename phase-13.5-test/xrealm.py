"""Read only active downloadable ZIPs from xREALM's public LS25 mod table."""
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
import requests

def discover_active(page_url, html=None):
    origin = urlparse(page_url)
    if origin.scheme not in ('http', 'https') or not origin.hostname:
        raise ValueError('Invalid xREALM page URL')
    if html is None:
        response = requests.get(page_url, timeout=(10, 35))
        response.raise_for_status()
        html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    rows = soup.select('.table-grid.table2 > .container-row.grid-row')
    if not rows:
        raise ValueError('No mod rows found; existing manifest remains unchanged')
    active = {}
    for row in rows:
        if not row.find('b', string='Active'):
            continue
        if 'col-lg-visible' in row.get('class', []):
            continue
        status = None
        for label in row.find_all('b'):
            if label.get_text(' ', strip=True) == 'Active':
                parent = label.parent.parent
                if parent:
                    status_col = next((div for div in parent.find_all('div', recursive=False)
                                       if 'col-lg-12' in div.get('class', [])), None)
                    if status_col:
                        status = status_col.get_text(' ', strip=True)
                break
        if status not in ('Yes', 'No'):
            raise ValueError('Unknown activation status; refusing incomplete manifest')
        if status == 'No':
            continue
        links = []
        for link in row.select('a[title^="Download "]'):
            name = link.get('title', '')[9:]
            url = urljoin(page_url, link.get('href', ''))
            parsed = urlparse(url)
            decoded = unquote(parsed.path.rsplit('/', 1)[-1])
            if (parsed.scheme == origin.scheme and parsed.netloc == origin.netloc
                and name == decoded and name.lower().endswith('.zip')
                and name not in ('.', '..') and '/' not in name and '\\' not in name):
                links.append((name, url))
        if not links:
            continue  # Active entries without ZIP link cannot be distributed
        name, url = links[0]
        if name in active and active[name] != url:
            raise ValueError('Conflicting download URLs for ' + name)
        active[name] = url
    if not active:
        raise ValueError('No active downloadable mods; existing manifest remains unchanged')
    return active

def discover(page_url):
    return discover_active(page_url)
