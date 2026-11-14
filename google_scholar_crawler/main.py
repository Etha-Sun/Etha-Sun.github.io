import html as html_lib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

RESULTS_DIR = Path('results')
HISTORY_FILE = RESULTS_DIR / 'gs_history.json'
PAPERS_FILE = Path('papers.json')
PREVIOUS_DIR = Path('../previous_stats')
USER_AGENT_MAILTO = 'CitationBot/1.0 (mailto:sunyuechun@mail.ustc.edu.cn)'


def load_json(path: Path):
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def load_previous_data():
    return load_json(PREVIOUS_DIR / 'gs_data.json')


def load_history():
    return load_json(HISTORY_FILE) or load_json(PREVIOUS_DIR / 'gs_history.json') or []


def load_paper_config():
    return load_json(PAPERS_FILE) or {'papers': []}


def try_fetch_google_scholar(author_id: str):
    url = f'https://scholar.google.com/citations?user={author_id}&hl=en&pagesize=100'
    html = None

    try:
        from curl_cffi import requests as curl_requests
        response = curl_requests.get(url, impersonate='chrome124', timeout=30)
        if response.status_code == 200 and 'gsc_a_tr' in response.text:
            html = response.text
    except Exception as exc:
        print(f'curl_cffi Scholar fetch failed: {exc}')

    if html is None:
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
        }
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200 and 'gsc_a_tr' in response.text:
            html = response.text
        else:
            raise RuntimeError(
                f'Google Scholar blocked or returned unexpected page '
                f'(status={response.status_code}, len={len(response.text)})'
            )

    soup = BeautifulSoup(html, 'html.parser')
    name_ele = soup.select_one('#gsc_prf_in')
    name = name_ele.get_text(strip=True) if name_ele else author_id

    citedby = 0
    for row in soup.select('#gsc_rsb_st tbody tr'):
        cells = [c.get_text(strip=True) for c in row.find_all(['th', 'td'])]
        if cells and cells[0].lower().startswith('citations'):
            try:
                citedby = int(cells[1])
            except (ValueError, IndexError):
                pass
            break

    publications = {}
    for row in soup.select('#gsc_a_b .gsc_a_tr'):
        title_ele = row.select_one('a.gsc_a_at')
        cite_ele = row.select_one('a.gsc_a_ac')
        if not title_ele:
            continue
        href = title_ele.get('href', '')
        match = re.search(r'citation_for_view=([^&]+)', href)
        if not match:
            continue
        pub_id = html_lib.unescape(match.group(1))
        title = html_lib.unescape(title_ele.get_text(' ', strip=True))
        cite_text = cite_ele.get_text(strip=True) if cite_ele else ''
        num_citations = int(cite_text) if cite_text.isdigit() else 0
        publications[pub_id] = {
            'author_pub_id': pub_id,
            'num_citations': num_citations,
            'bib': {'title': title},
        }

    return {
        'name': name,
        'scholar_id': author_id,
        'citedby': citedby,
        'publications': publications,
        'source': 'google_scholar',
        'updated': datetime.now(timezone.utc).isoformat(),
    }


def fetch_openalex_work_by_arxiv(arxiv_id: str):
    headers = {'User-Agent': USER_AGENT_MAILTO}
    doi_url = f'https://api.openalex.org/works/https://doi.org/10.48550/arXiv.{arxiv_id}'
    response = requests.get(doi_url, headers=headers, timeout=30)
    if response.status_code == 200:
        return response.json()
    response = requests.get(
        'https://api.openalex.org/works',
        params={'filter': f'ids.arxiv:{arxiv_id}'},
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    results = response.json().get('results', [])
    return results[0] if results else None


def build_openalex_fallback(author_id: str, paper_config: dict, previous: dict):
    # Prefer the last successful Google Scholar snapshot when available.
    # OpenAlex counts often lag Scholar and should only fill gaps.
    prev_source = (previous or {}).get('source')
    if previous and previous.get('publications') and prev_source in {
        'google_scholar', 'google_scholar_cached'
    }:
        reused = dict(previous)
        reused['updated'] = datetime.now(timezone.utc).isoformat()
        reused['source'] = 'google_scholar_cached'
        reused['note'] = (
            'Google Scholar blocked automated access; '
            'reusing the last successful Google Scholar snapshot.'
        )
        return reused

    publications = {}
    total = 0
    for paper in paper_config.get('papers', []):
        pub_id = paper.get('scholar_pub_id')
        arxiv = paper.get('arxiv')
        title = paper.get('title')
        citations = 0
        if arxiv:
            try:
                work = fetch_openalex_work_by_arxiv(arxiv)
                time.sleep(0.2)
                if work:
                    citations = work.get('cited_by_count', 0) or 0
                    title = work.get('display_name') or title
            except Exception as exc:
                print(f'OpenAlex fetch failed for {arxiv}: {exc}')
                if previous:
                    prev_pub = previous.get('publications', {}).get(pub_id or '', {})
                    citations = prev_pub.get('num_citations', 0) or 0
        elif previous and pub_id:
            citations = (previous.get('publications', {}).get(pub_id, {}) or {}).get('num_citations', 0) or 0

        if not pub_id:
            # Keep a stable synthetic id for papers not yet on Scholar.
            pub_id = f"{author_id}:OPENALEX:{paper.get('key')}"

        publications[pub_id] = {
            'author_pub_id': pub_id,
            'num_citations': citations,
            'bib': {'title': title},
        }
        total += citations

    return {
        'name': previous.get('name') if previous else 'Yuechun Sun',
        'scholar_id': author_id,
        'citedby': total,
        'publications': publications,
        'source': 'openalex_fallback',
        'updated': datetime.now(timezone.utc).isoformat(),
        'note': 'Google Scholar blocked automated access; using OpenAlex citation counts as fallback.',
    }


def attach_deltas(author: dict, previous: dict):
    previous = previous or {}
    previous_pubs = previous.get('publications', {})
    previous_paper_stats = previous.get('paper_stats', {})
    previous_total = previous.get('citedby')

    paper_stats = {}
    for pub_id, pub in author['publications'].items():
        citations = pub.get('num_citations', 0) or 0
        prev_citations = None
        if pub_id in previous_paper_stats:
            prev_citations = previous_paper_stats[pub_id].get('num_citations')
        elif pub_id in previous_pubs:
            prev_citations = previous_pubs[pub_id].get('num_citations')
        delta = None if prev_citations is None else citations - (prev_citations or 0)
        paper_stats[pub_id] = {
            'title': pub.get('bib', {}).get('title', ''),
            'num_citations': citations,
            'previous_citations': prev_citations,
            'delta': delta,
        }

    author['paper_stats'] = paper_stats
    author['citedby_delta'] = None if previous_total is None else (author.get('citedby', 0) - previous_total)
    return author


def write_outputs(author: dict):
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
        'source': author.get('source'),
        'citedby': author.get('citedby', 0),
        'citedby_delta': author.get('citedby_delta'),
        'publications': {
            pub_id: {
                'title': stats['title'],
                'num_citations': stats['num_citations'],
                'delta': stats['delta'],
            }
            for pub_id, stats in author.get('paper_stats', {}).items()
        },
    })
    history = history[-400:]
    with open(HISTORY_FILE, 'w', encoding='utf-8') as outfile:
        json.dump(history, outfile, ensure_ascii=False, indent=2)

    with open(RESULTS_DIR / 'gs_paper_deltas.json', 'w', encoding='utf-8') as outfile:
        json.dump({
            'updated': author['updated'],
            'source': author.get('source'),
            'citedby': author.get('citedby', 0),
            'citedby_delta': author.get('citedby_delta'),
            'papers': author.get('paper_stats', {}),
        }, outfile, ensure_ascii=False, indent=2)


def main():
    author_id = os.environ['GOOGLE_SCHOLAR_ID']
    paper_config = load_paper_config()
    previous = load_previous_data() or {}

    try:
        author = try_fetch_google_scholar(author_id)
        print('Fetched Google Scholar successfully.')
    except Exception as exc:
        print(f'Google Scholar fetch failed: {exc}')
        print('Falling back to OpenAlex for tracked papers.')
        author = build_openalex_fallback(author_id, paper_config, previous)

    author = attach_deltas(author, previous)
    write_outputs(author)

    print(json.dumps({
        'name': author.get('name'),
        'source': author.get('source'),
        'citedby': author.get('citedby'),
        'citedby_delta': author.get('citedby_delta'),
        'papers': author.get('paper_stats'),
        'note': author.get('note'),
    }, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
