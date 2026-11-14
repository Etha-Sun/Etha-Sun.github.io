from scholarly import scholarly
import json
from datetime import datetime, timezone
import os
from pathlib import Path

RESULTS_DIR = Path('results')
HISTORY_FILE = RESULTS_DIR / 'gs_history.json'
PREVIOUS_DATA_CANDIDATES = [
    Path('../previous_stats/gs_data.json'),
    Path('previous_gs_data.json'),
]


def load_previous_data():
    for path in PREVIOUS_DATA_CANDIDATES:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    return None


def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    previous_history = Path('../previous_stats/gs_history.json')
    if previous_history.exists():
        with open(previous_history, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


author_id = os.environ['GOOGLE_SCHOLAR_ID']
author: dict = scholarly.search_author_id(author_id)
scholarly.fill(author, sections=['basics', 'indices', 'counts', 'publications'])
author['updated'] = datetime.now(timezone.utc).isoformat()
publications = {v['author_pub_id']: v for v in author['publications']}
author['publications'] = publications

previous = load_previous_data() or {}
previous_pubs = previous.get('publications', {}) if isinstance(previous, dict) else {}
previous_total = previous.get('citedby')

paper_stats = {}
for pub_id, pub in publications.items():
    title = pub.get('bib', {}).get('title', '')
    citations = pub.get('num_citations', 0) or 0
    prev_citations = None
    if pub_id in previous_pubs:
        prev_citations = previous_pubs[pub_id].get('num_citations', 0) or 0
    delta = None if prev_citations is None else citations - prev_citations
    paper_stats[pub_id] = {
        'title': title,
        'num_citations': citations,
        'previous_citations': prev_citations,
        'delta': delta,
    }

author['paper_stats'] = paper_stats
author['citedby_delta'] = None if previous_total is None else (author.get('citedby', 0) - previous_total)

print(json.dumps({
    'name': author.get('name'),
    'citedby': author.get('citedby'),
    'citedby_delta': author.get('citedby_delta'),
    'papers': paper_stats,
}, indent=2, ensure_ascii=False))

RESULTS_DIR.mkdir(exist_ok=True)
with open(RESULTS_DIR / 'gs_data.json', 'w', encoding='utf-8') as outfile:
    json.dump(author, outfile, ensure_ascii=False)

shieldio_data = {
    'schemaVersion': 1,
    'label': 'citations',
    'message': f"{author.get('citedby', 0)}",
    'color': '9cf',
    'labelColor': 'f6f6f6',
}
with open(RESULTS_DIR / 'gs_data_shieldsio.json', 'w', encoding='utf-8') as outfile:
    json.dump(shieldio_data, outfile, ensure_ascii=False)

history = load_history()
history.append({
    'updated': author['updated'],
    'citedby': author.get('citedby', 0),
    'citedby_delta': author.get('citedby_delta'),
    'publications': {
        pub_id: {
            'title': stats['title'],
            'num_citations': stats['num_citations'],
            'delta': stats['delta'],
        }
        for pub_id, stats in paper_stats.items()
    },
})
# Keep roughly one year of daily snapshots.
history = history[-400:]
with open(HISTORY_FILE, 'w', encoding='utf-8') as outfile:
    json.dump(history, outfile, ensure_ascii=False, indent=2)

with open(RESULTS_DIR / 'gs_paper_deltas.json', 'w', encoding='utf-8') as outfile:
    json.dump({
        'updated': author['updated'],
        'citedby': author.get('citedby', 0),
        'citedby_delta': author.get('citedby_delta'),
        'papers': paper_stats,
    }, outfile, ensure_ascii=False, indent=2)
