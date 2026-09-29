#!/usr/bin/env python3
"""Steuerung des Kanals „Der eigene Ratgeber“ (läuft in GitHub Actions).

Befehle:
  plan [--tage 7] [--max 3]  schreibt faellig.json: freigegebene, noch nicht geplante Beiträge der nächsten Tage
  produzieren SITE           rendert faellig.json nach SITE/m/ und räumt veröffentlichte Medien auf
  veroeffentlichen BASISURL  plant die gerenderten Beiträge in Buffer ein und schreibt status.json
  buffer-test                zeigt Organisation, verbundene Kanäle und die Metadaten-Felder der Buffer-API

Freigabe: Feld "freigegeben": true je Beitrag in warteschlange/charge-XX.json (übernommen von der Freigabe-Seite).
Umgebungsvariablen: BUFFER_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
optional KANAL_PAUSE=1 (nichts veröffentlichen), BUFFER_ENTWURF=1 (nur als Entwurf in Buffer ablegen).
"""
import json, os, re, sys, glob, argparse, subprocess, urllib.request, urllib.error
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(BASE, 'status.json')
TAGE = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

# ---------- Hilfen ----------
def lade_chargen():
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'warteschlange', 'charge-*.json'))):
        c = json.load(open(f))
        tz = ZoneInfo(c.get('zeitzone', 'Europe/Berlin'))
        start = date.fromisoformat(c['start'])
        for b in c['beitraege']:
            d = start + timedelta(days=b['tag'] - 1)
            hh, mm = map(int, c['uhrzeit'][b['format']].split(':'))
            b['_zeit'] = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
            b['_charge'] = c['charge']
            out.append(b)
    return out

def status():
    return json.load(open(STATUS)) if os.path.exists(STATUS) else {}

def speichere_status(s):
    json.dump(s, open(STATUS, 'w'), ensure_ascii=False, indent=1, sort_keys=True)

def gh(method, path, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f"Bearer {os.environ['GITHUB_TOKEN']}", 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or 'null')

def issue_titel(charge): return f'Freigabe Charge {charge:02d}'

def freigegeben(charge):
    """Liest das Freigabe-Issue: angehakte IDs, oder alle bei „ALLE freigeben“."""
    for st in ('open', 'closed'):
        for it in gh('GET', f'/issues?state={st}&per_page=100'):
            if it['title'] == issue_titel(charge):
                body = it.get('body') or ''
                if re.search(r'- \[[xX]\] \*\*ALLE freigeben\*\*', body): return 'ALLE', body
                return set(re.findall(r'- \[[xX]\] `([A-Z0-9-]+)`', body)), body
    return set(), ''

# ---------- Befehle ----------
def cmd_freigabe_issue(a):
    beitraege = [b for b in lade_chargen() if b['_charge'] == a.charge]
    ids, body = freigegeben(a.charge)
    if body:
        print('Issue existiert bereits.'); return
    zeilen = [f'Hake an, was veröffentlicht werden darf. Nur angehakte Beiträge gehen raus (frühestens 7 Tage vor Termin).',
              '', '- [ ] **ALLE freigeben**', '']
    for b in beitraege:
        z = b['_zeit']; art = 'Video' if b['format'] == 'video' else 'Karussell'
        zeilen.append(f"- [ ] `{b['id']}` · {TAGE[z.weekday()]} {z:%d.%m.} · {art} · {b['serie']} #{b['serie_nummer']} · „{b['hook']}“")
    zeilen += ['', 'Vorschau aller Texte: Datei `warteschlange/charge-%02d.json` oder die Freigabe-Übersicht.' % a.charge,
               'Pause für alles: Repository-Variable `KANAL_PAUSE` auf `1` setzen.']
    it = gh('POST', '/issues', {'title': issue_titel(a.charge), 'body': '\n'.join(zeilen)})
    print('Issue angelegt:', it['html_url'])

def cmd_plan(a):
    s = status(); jetzt = datetime.now(ZoneInfo('Europe/Berlin'))
    faellig = []
    for b in sorted(lade_chargen(), key=lambda b: b['_zeit']):
        if b['id'] in s and s[b['id']].get('buffer'): continue
        if b['_zeit'] < jetzt + timedelta(minutes=30): continue          # zu spät, wird übersprungen
        if b['_zeit'] > jetzt + timedelta(days=a.tage): continue
        if not b.get('freigegeben'): continue            # nur auf der Freigabe-Seite freigegebene Beiträge
        faellig.append(b['id'])
    faellig = faellig[:a.max]
    json.dump(faellig, open(os.path.join(BASE, 'faellig.json'), 'w'))
    print('Fällig:', faellig or 'nichts')
    if 'GITHUB_OUTPUT' in os.environ:
        open(os.environ['GITHUB_OUTPUT'], 'a').write(f"anzahl={len(faellig)}\n")

def cmd_produzieren(a):
    ids = json.load(open(os.path.join(BASE, 'faellig.json')))
    alle = {b['id']: b for b in lade_chargen()}; s = status()
    m = os.path.join(a.site, 'm'); os.makedirs(m, exist_ok=True)
    # Aufräumen: Medien von Beiträgen, die seit mehr als 2 Tagen veröffentlicht sind
    jetzt = datetime.now(ZoneInfo('Europe/Berlin'))
    for f in os.listdir(m):
        bid = f.split('_')[0].rsplit('.', 1)[0]
        if bid in alle and alle[bid]['_zeit'] < jetzt - timedelta(days=2): os.remove(os.path.join(m, f))
    open(os.path.join(a.site, 'index.html'), 'w').write('<!doctype html><meta charset="utf-8"><title>Der eigene Ratgeber</title>')
    open(os.path.join(a.site, '.nojekyll'), 'w').write('')
    for bid in ids:
        vorh = s.get(bid, {}).get('medien')
        if vorh and all(os.path.exists(os.path.join(m, f)) for f in vorh):
            print('schon gerendert:', bid); continue
        b = {k: v for k, v in alle[bid].items() if not k.startswith('_')}
        jf = os.path.join(BASE, f'_{bid}.json'); json.dump(b, open(jf, 'w'), ensure_ascii=False)
        if b['format'] == 'video':
            subprocess.run([sys.executable, os.path.join(BASE, 'render.py'), jf, 'auto', os.path.join(m, f'{bid}.mp4'), '--stimme', 'elevenlabs'], check=True)
            dateien = [f'{bid}.mp4']
        else:
            tmp = os.path.join(BASE, f'_{bid}'); os.makedirs(tmp, exist_ok=True)
            subprocess.run([sys.executable, os.path.join(BASE, 'carousel.py'), jf, 'auto', tmp], check=True)
            dateien = []
            for k, p in enumerate(sorted(glob.glob(os.path.join(tmp, '*.png'))), 1):
                ziel = f'{bid}_{k:02d}.jpg'
                from PIL import Image
                Image.open(p).convert('RGB').save(os.path.join(m, ziel), quality=92); dateien.append(ziel)
        s.setdefault(bid, {})['medien'] = dateien
        s[bid]['gerendert'] = jetzt.isoformat(timespec='minutes')
        print('fertig:', bid, dateien)
    speichere_status(s)

# ---------- Buffer ----------
def buffer(query, variables=None):
    req = urllib.request.Request('https://api.buffer.com', data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
                                 headers={'Authorization': f"Bearer {os.environ['BUFFER_API_KEY'].strip()}", 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'Buffer-Fehler {e.code}: {e.read().decode(errors="replace")[:800]}')
    if res.get('errors'): raise RuntimeError('Buffer-Fehler: ' + json.dumps(res['errors'], ensure_ascii=False)[:800])
    return res['data']

def kanaele():
    org = buffer('query { account { organizations { id name } } }')['account']['organizations'][0]
    ch = buffer('query($i: ChannelsInput!) { channels(input: $i) { id service name } }', {'i': {'organizationId': org['id']}})['channels']
    return org, {c['service'].lower(): c for c in ch}

MUT = '''mutation($input: CreatePostInput!) { createPost(input: $input) {
  ... on PostActionSuccess { post { id dueAt } }
  ... on MutationError { message } } }'''

def texte(b):
    ht = lambda l: ' '.join(l)
    ig = f"{b['instagram_caption']}\n\n{ht(b['instagram_hashtags'])}"
    tt = f"{b['tiktok_text']} {ht(b['tiktok_hashtags'])}"
    yt = f"{b['youtube_beschreibung']}\n\n{b['angepinnter_kommentar']}\n\n{ht(b['tiktok_hashtags'])} #shorts\nSprecherstimme KI-generiert."
    if b.get('quelle'): yt += f"\nQuelle: {b['quelle']}"
    return ig, tt, yt

def cmd_veroeffentlichen(a):
    if os.environ.get('KANAL_PAUSE') == '1': print('KANAL_PAUSE=1 – nichts veröffentlicht.'); return
    ids = json.load(open(os.path.join(BASE, 'faellig.json')))
    if not ids: print('Nichts zu veröffentlichen.'); return
    alle = {b['id']: b for b in lade_chargen()}; s = status()
    org, ch = kanaele(); print('Kanäle:', {k: v['name'] for k, v in ch.items()})
    entwurf = os.environ.get('BUFFER_ENTWURF') == '1'
    fehler = []
    for bid in ids:
        b = alle[bid]; st = s.get(bid, {})
        urls = [f"{a.basis.rstrip('/')}/m/{f}" for f in st.get('medien', [])]
        if not urls: fehler.append(f'{bid}: keine Medien'); continue
        for u in urls:   # Medien müssen öffentlich erreichbar sein
            try: urllib.request.urlopen(urllib.request.Request(u, method='HEAD'), timeout=30)
            except Exception as e: raise RuntimeError(f'Medium nicht erreichbar: {u} ({e})')
        ig, tt, yt = texte(b); due = b['_zeit'].astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        ziele = []
        if b['format'] == 'video':
            asset = [{'video': {'url': urls[0], 'metadata': {'thumbnailOffset': 300}}}]
            ziele += [('instagram', ig, asset, {'instagram': {'type': 'reel', 'shouldShareToFeed': True, 'firstComment': b['angepinnter_kommentar'], 'isAiGenerated': True}}),
                      ('tiktok', tt, asset, {'tiktok': {'isAiGenerated': True}}),
                      ('youtube', yt, asset, {'youtube': {'title': b['youtube_titel'][:100], 'privacy': 'public', 'madeForKids': False,
                                                          'notifySubscribers': True, 'embeddable': True, 'isAiGenerated': True}})]
        else:
            asset = [{'image': {'url': u}} for u in urls[:10]]
            ziele += [('instagram', ig, asset, {'instagram': {'type': 'carousel', 'shouldShareToFeed': True, 'firstComment': b['angepinnter_kommentar'], 'isAiGenerated': False}})]
        st.setdefault('buffer', {})
        for dienst, text, assets, meta in ziele:
            if dienst not in ch: print(f'  {dienst}: nicht in Buffer verbunden – übersprungen'); continue
            if dienst in st['buffer']: continue
            inp = {'channelId': ch[dienst]['id'], 'text': text, 'assets': assets, 'schedulingType': 'automatic',
                   'mode': 'customScheduled', 'dueAt': due, 'metadata': meta, 'aiAssisted': True}
            if entwurf: inp['saveToDraft'] = True
            try:
                r = buffer(MUT, {'input': inp})['createPost']
            except RuntimeError as e:
                if 'metadata' in str(e).lower() or 'field' in str(e).lower():
                    print(f'  {dienst}: Metadaten abgelehnt, zweiter Versuch ohne Zusatzfelder:', str(e)[:300])
                    inp.pop('metadata'); r = buffer(MUT, {'input': inp})['createPost']
                else: raise
            if 'post' in r: st['buffer'][dienst] = r['post']['id']; print(f'  {bid} → {dienst}: geplant für {due}')
            else: fehler.append(f"{bid} {dienst}: {r.get('message')}")
        s[bid] = st; speichere_status(s)
    if fehler:
        print('FEHLER:\n' + '\n'.join(fehler)); sys.exit(1)

def cmd_buffer_test(a):
    org, ch = kanaele()
    print('Organisation:', org['name'], org['id'])
    for k, v in ch.items(): print(f'  Kanal {k}: {v["name"]} ({v["id"]})')
    def typ_name(t):
        while t and t.get('name') is None: t = t.get('ofType')
        return (t or {}).get('name')
    q = 'query($n: String!) { __type(name: $n) { kind inputFields { name type { kind name ofType { kind name ofType { kind name } } } } enumValues { name } } }'
    gesehen, offen = set(), ['CreatePostInput', 'PostInputMetaData', 'AssetInput']
    while offen:
        n = offen.pop(0)
        if n in gesehen or n in ('String', 'Boolean', 'Int', 'Float', 'ID', 'DateTime'): continue
        gesehen.add(n)
        try: t = buffer(q, {'n': n})['__type']
        except Exception as e: print(n, 'nicht lesbar:', e); continue
        if not t: continue
        if t.get('enumValues'): print(f'{n} (Auswahl): ' + ', '.join(v['name'] for v in t['enumValues'])); continue
        felder = [(f['name'], typ_name(f['type'])) for f in t.get('inputFields') or []]
        print(f'{n}: ' + ', '.join(f'{a}:{b}' for a, b in felder))
        if n in ('CreatePostInput', 'PostInputMetaData', 'AssetInput') or n.lower().startswith(('instagram', 'tiktok', 'youtube', 'video', 'image')) or n in ('ShareMode', 'SchedulingType'):
            offen += [b for a, b in felder if b]

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('freigabe-issue'); p.add_argument('charge', type=int)
    p = sp.add_parser('plan'); p.add_argument('--tage', type=int, default=7); p.add_argument('--max', type=int, default=3)
    p = sp.add_parser('produzieren'); p.add_argument('site')
    p = sp.add_parser('veroeffentlichen'); p.add_argument('basis')
    sp.add_parser('buffer-test')
    a = ap.parse_args()
    {'freigabe-issue': cmd_freigabe_issue, 'plan': cmd_plan, 'produzieren': cmd_produzieren,
     'veroeffentlichen': cmd_veroeffentlichen, 'buffer-test': cmd_buffer_test}[a.cmd](a)
