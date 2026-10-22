#!/usr/bin/env python3
"""Kennzahlen aus Buffer holen (läuft in GitHub Actions) und in metriken.json ablegen.

Aufruf: python metriken.py [--schema]
  --schema  schreibt zusätzlich die Felder der Buffer-Typen (Post, Channel, PostMetric, Query) in metriken.json
Braucht BUFFER_API_KEY (persönlicher Schlüssel). Verbraucht pro Lauf etwa 10-20 Anfragen.
"""
import json, os, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
ZIEL = os.path.join(BASE, 'metriken.json')

def buffer(query, variables=None):
    req = urllib.request.Request('https://api.buffer.com', data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
                                 headers={'Authorization': f"Bearer {os.environ['BUFFER_API_KEY'].strip()}", 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'HTTP {e.code}: {e.read().decode(errors="replace")[:600]}')
    if res.get('errors'): raise RuntimeError(json.dumps(res['errors'], ensure_ascii=False)[:600])
    return res['data']

def typ_felder(name):
    q = '''query($n: String!) { __type(name: $n) { kind fields { name args { name } type { kind name ofType { kind name ofType { kind name } } } }
           inputFields { name type { kind name ofType { kind name ofType { kind name } } } } enumValues { name } } }'''
    t = buffer(q, {'n': name})['__type']
    if not t: return None
    def tn(x):
        while x and x.get('name') is None: x = x.get('ofType')
        return (x or {}).get('name')
    if t.get('enumValues'): return [v['name'] for v in t['enumValues']]
    out = {}
    for f in (t.get('fields') or t.get('inputFields') or []):
        out[f['name']] = tn(f['type']) + (f" ({', '.join(a['name'] for a in f.get('args') or [])})" if f.get('args') else '')
    return out

def main():
    schema = '--schema' in sys.argv
    erg = {'stand': datetime.now(timezone.utc).isoformat(timespec='minutes'), 'fehler': [], 'kanaele': {}, 'beitraege': {}, 'summen_30_tage': {}}
    if schema:
        erg['schema'] = {}
        for n in ['Query', 'Post', 'Channel', 'PostMetric', 'PostsInput', 'PostsFiltersInput', 'PostStatus', 'AggregatedPostMetricsInput', 'PostMetricType']:
            try: erg['schema'][n] = typ_felder(n)
            except Exception as e: erg['schema'][n] = f'nicht lesbar: {e}'
    org = buffer('query { account { organizations { id name } } }')['account']['organizations'][0]
    ch = buffer('query($i: ChannelsInput!) { channels(input: $i) { id service name } }', {'i': {'organizationId': org['id']}})['channels']
    kanal = {c['id']: c['service'].lower() for c in ch}
    erg['kanaele'] = {c['service'].lower(): {'id': c['id'], 'name': c['name']} for c in ch}

    # Buffer-IDs aus status.json den eigenen Beitrags-IDs zuordnen
    status = json.load(open(os.path.join(BASE, 'status.json'))) if os.path.exists(os.path.join(BASE, 'status.json')) else {}
    zu_id = {}
    for bid, st in status.items():
        for dienst, pid in (st.get('buffer') or {}).items(): zu_id[pid] = (bid, dienst)

    q = '''query($i: PostsInput!, $n: Int, $a: String) { posts(input: $i, first: $n, after: $a) {
             edges { node { id text dueAt channelId metrics { type name value unit } metricsUpdatedAt } }
             pageInfo { endCursor hasNextPage } } }'''
    for cid, dienst in kanal.items():
        nach, seiten = None, 0
        while seiten < 10:
            try:
                d = buffer(q, {'i': {'organizationId': org['id'], 'filter': {'status': ['sent'], 'channelIds': [cid]}}, 'n': 50, 'a': nach})['posts']
            except Exception as e:
                erg['fehler'].append(f'{dienst} posts: {e}'); break
            for ed in d['edges']:
                p = ed['node']; bid, _ = zu_id.get(p['id'], (None, None))
                key = bid or f"unbekannt:{p['id']}"
                eintrag = erg['beitraege'].setdefault(key, {})
                eintrag[dienst] = {'buffer_id': p['id'], 'gesendet': p.get('dueAt'), 'aktualisiert': p.get('metricsUpdatedAt'),
                                   'werte': {m['type']: m['value'] for m in (p.get('metrics') or [])}}
                if not bid: eintrag['text'] = (p.get('text') or '')[:80]
            seiten += 1
            if not d['pageInfo']['hasNextPage']: break
            nach = d['pageInfo']['endCursor']

    jetzt = datetime.now(timezone.utc); start = jetzt - timedelta(days=30)
    qa = '''query($i: AggregatedPostMetricsInput!) { aggregatedPostMetrics(input: $i) { metrics { type value unit } metricsUpdatedAt } }'''
    for dienst, k in erg['kanaele'].items():
        try:
            a = buffer(qa, {'i': {'organizationId': org['id'], 'startDateTime': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                  'endDateTime': jetzt.strftime('%Y-%m-%dT%H:%M:%SZ'), 'channelIds': [k['id']]}})['aggregatedPostMetrics']
            erg['summen_30_tage'][dienst] = {m['type']: m['value'] for m in a['metrics']}
        except Exception as e:
            erg['fehler'].append(f'{dienst} summen: {e}')

    json.dump(erg, open(ZIEL, 'w'), ensure_ascii=False, indent=1, sort_keys=True)
    print('Beiträge mit Kennzahlen:', len(erg['beitraege']))
    for dienst, s in erg['summen_30_tage'].items(): print(f'  {dienst} (30 Tage):', s)
    if erg['fehler']: print('Fehler:\n  ' + '\n  '.join(erg['fehler']))

if __name__ == '__main__':
    main()
