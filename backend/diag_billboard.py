import urllib.request
from bs4 import BeautifulSoup, NavigableString
from crawler.content_processor import ContentProcessor

req = urllib.request.Request(
    'https://detailed.com/50/',
    headers={'User-Agent': 'Mozilla/5.0'},
)
html = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', errors='replace')
soup = BeautifulSoup(html, 'html.parser')

# Find the Billboard heading.
billboard_heading = None
for h in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
    if 'Billboard' in h.get_text(strip=True):
        billboard_heading = h
        break

print('=== Billboard heading ===')
print('tag:', billboard_heading.name if billboard_heading else None)
print('text:', billboard_heading.get_text(strip=True) if billboard_heading else None)
print('parent chain (tag, classes):')
p = billboard_heading
for _ in range(5):
    p = p.parent
    if not p:
        break
    print('  ', p.name, p.get('class'), p.get('role'))

print()
print('=== Text nodes after the heading (up to 30) ===')
count = 0
for node in billboard_heading.next_elements:
    if count >= 30:
        break
    if isinstance(node, NavigableString):
        text = str(node).strip()
        if not text:
            continue
        parent = node.parent
        in_skip = ContentProcessor._in_skipped_region(parent) if parent else False
        in_other = ContentProcessor._inside_other_extractor_subtree(node)
        print(
            repr(text[:80]),
            '| parent:', parent.name if parent else None,
            '| class:', parent.get('class') if parent else None,
            '| skip_region:', in_skip,
            '| other_subtree:', in_other,
        )
        count += 1