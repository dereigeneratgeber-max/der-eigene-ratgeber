#!/usr/bin/env python3
"""Render-Skript „Der eigene Ratgeber“: Autor-JSON -> 9:16-Video (1080x1920).

Aufruf: python3 render.py beitrag.json stil ausgabe.mp4 [--stimme de-thorsten-low]
Stile: himmel | cover | klar | tusche
Stimme: ElevenLabs (ein Aufruf je Video, mit Wort-Zeitstempeln); Rückfall: Piper (kostenlos, lokal).
Grafik: Pillow, Video: FFmpeg.
"""
import json, re, subprocess, sys, os, wave, math, argparse, tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
BASE = os.path.dirname(os.path.abspath(__file__))
FONT = lambda n: os.path.join(BASE, 'fonts', n) if os.path.exists(os.path.join(BASE, 'fonts', n)) else os.path.join(BASE, n)
NAVY = (31, 47, 74); SKY = (131, 177, 221); SKY_L = (227, 237, 247); WHITE = (255, 255, 255)
RED = (214, 69, 52); GREY = (96, 110, 130)
PAUSE = 0.38          # Pause zwischen Sätzen (s, nur Piper)
HOOK_HOLD = 0.45      # Aussage wirkt kurz nach
# Sicherer Bereich: rechts liegen bei TikTok/Reels die Buttons, unten die Beschreibung
CX = 505              # Mittelachse für Text (leicht links der Bildmitte)
CAP_W = 660           # max. Breite eines Untertitel-Blocks
HOOK_W = 720          # max. Breite der Aussage
END_CARD = 2.4        # Schlusstafel mit Buch (nur bei Buchhinweis)

# ---------- Text und Stimme ----------
def saetze(text):
    parts = re.split(r'(?<=[.!?…“])\s+(?=[A-ZÄÖÜ„"])', text.strip())   # auch nach Zitat-Ende „…“
    return [p.strip() for p in parts if p.strip()]

def tts(text, voice, out, length_scale=1.12):
    p = subprocess.run([sys.executable, '-m', 'piper', '-m', voice, '-f', out,
                        '--length-scale', str(length_scale), '--sentence-silence', '0'],
                       input=text.encode(), capture_output=True)
    if p.returncode: raise RuntimeError(p.stderr.decode()[-500:])

# Stimmprofile: 'alt' = bisherige Fassung, 'A' = lebendiger (v2), 'B' = eleven_v3 mit Audio-Tags wie [thoughtful]
PROFILE = {
    'alt': {'model': 'eleven_multilingual_v2', 'stability': 0.55, 'similarity': 0.8, 'style': 0.15, 'speed': 1.1, 'hook_hold': 0.45},
    'A':   {'model': 'eleven_multilingual_v2', 'stability': 0.35, 'similarity': 0.8, 'style': 0.35, 'speed': 1.05, 'hook_hold': 0.0},
    'B':   {'model': 'eleven_v3', 'stability': 0.5, 'similarity': 0.8, 'hook_hold': 0.0},
}
TAG = re.compile(r'\[[^\]]*\]\s*')

def stimmprofil(beitrag):
    p = beitrag.get('stimme') or os.environ.get('ELEVENLABS_PROFIL') or 'B'   # Standard seit 20.10.: B
    prof = dict(PROFILE.get(p, PROFILE['alt'])) if isinstance(p, str) else {**PROFILE['alt'], **p}
    if isinstance(p, str) and p == 'alt' and os.environ.get('ELEVENLABS_SPEED'): prof['speed'] = float(os.environ['ELEVENLABS_SPEED'])
    return prof

def _el_request(text, prof):
    import base64, urllib.request, urllib.error
    key = os.environ['ELEVENLABS_API_KEY'].strip(); voice = os.environ['ELEVENLABS_VOICE_ID'].strip()
    vs = {'stability': prof['stability'], 'similarity_boost': prof['similarity'], 'use_speaker_boost': True}
    if prof.get('style') is not None: vs['style'] = prof['style']
    if prof.get('speed') is not None: vs['speed'] = prof['speed']
    body = json.dumps({'text': text, 'model_id': prof['model'], 'voice_settings': vs}).encode()
    def call(pfad):
        req = urllib.request.Request(f'https://api.elevenlabs.io/v1/text-to-speech/{voice}{pfad}?output_format=mp3_44100_128',
                                     data=body, headers={'xi-api-key': key, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=240) as r: return r.read()
    try:
        res = json.loads(call('/with-timestamps'))
        return base64.b64decode(res['audio_base64']), (res.get('alignment') or res.get('normalized_alignment'))
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors='replace')[:600]
        if not prof['model'].startswith('eleven_v3'): raise RuntimeError(f'ElevenLabs-Fehler {e.code}: {msg}')
        print('Zeitstempel nicht verfügbar, Rückfall ohne Zeitstempel:', e.code, msg[:200])
    try: return call(''), None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'ElevenLabs-Fehler {e.code}: {e.read().decode(errors="replace")[:600]}')

def _words_from_alignment(al):
    words, cur, t0, t1, tag = [], '', None, None, False
    for ch, a, b in zip(al['characters'], al['character_start_times_seconds'], al['character_end_times_seconds']):
        if ch == '[': tag = True; continue
        if tag:
            if ch == ']': tag = False
            continue
        if ch.isspace():
            if cur: words.append((cur, t0, t1)); cur = ''
        else:
            if not cur: t0 = a
            cur += ch; t1 = b
    if cur: words.append((cur, t0, t1))
    return words

def read_wav(f):
    with wave.open(f) as w:
        sr = w.getframerate(); a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    return a, sr

def nur_satzzeichen(w_): return not re.search(r'\w', w_)

def anzeige_woerter(words):
    out = []
    for w_ in words:
        if nur_satzzeichen(w_[0]):
            if '…' in w_[0] and out and out[-1][3] == w_[3]: out[-1] = (out[-1][0] + '…',) + out[-1][1:]
            continue
        out.append(w_)
    return out

def anzeige_satz(s_): return ' '.join(w_[0] for w_ in anzeige_woerter([(w_, 0, 0, 0) for w_ in s_.split()]))

def build_audio(sentences, n_hook, voice, tmp, prof=None, sprechtext=None):
    """Sprechtext -> (Tonspur, sr, Wortliste [(wort, start, ende, satzindex)], Satz-Zeiten, Dauer).
    ElevenLabs: EIN Aufruf für den ganzen Text (natürliche Betonung, wenige Anfragen). Nach der Aussage wird eine Pause eingefügt."""
    prof = prof or PROFILE['alt']
    toks = [(w_, i) for i, s in enumerate(sentences) for w_ in s.split()]
    words = []
    if voice == 'elevenlabs':
        txt = ' '.join((sprechtext or ' '.join(sentences)).split())
        if not prof['model'].startswith('eleven_v3'): txt = TAG.sub('', txt)   # nur v3 versteht Audio-Tags
        try: mp3, al = _el_request(txt, prof)
        except RuntimeError as e:
            if not prof['model'].startswith('eleven_v3'): raise
            print('eleven_v3 nicht verfügbar, Rückfall auf Profil A:', str(e)[:300])
            mp3, al = _el_request(TAG.sub('', txt), PROFILE['A'])
        open(os.path.join(tmp, 'v.mp3'), 'wb').write(mp3)
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', os.path.join(tmp, 'v.mp3'), '-ac', '1', '-ar', '44100', os.path.join(tmp, 'v.wav')], check=True)
        a, sr = read_wav(os.path.join(tmp, 'v.wav'))
        wl = _words_from_alignment(al) if al else []
        if len(wl) != len(toks):   # Sicherheitsnetz: proportional verteilen
            d = len(a) / sr; tot = sum(len(w_) + 1 for w_, _ in toks); t = 0
            wl = []
            for w_, _ in toks:
                dt = d * (len(w_) + 1) / tot; wl.append((w_, t, t + dt)); t += dt
        words = [(toks[k][0], wl[k][1], wl[k][2], toks[k][1]) for k in range(len(toks))]
    elif voice == 'stumm':   # Test ohne Stimme: Stille, Wörter gleichmäßig verteilt (2,6 Wörter/s)
        sr, t = 44100, 0.0
        for w_, i in toks:
            dt = 0.18 + 0.045 * len(w_); words.append((w_, t, t + dt, i)); t += dt
        a = np.zeros(int((t + 0.3) * sr), np.float32)
    else:   # Piper, Satz für Satz (kostenloser Rückfall)
        parts, t, sr = [], 0.0, None
        for i, s in enumerate(sentences):
            f = os.path.join(tmp, f's{i}.wav'); tts(s, voice, f)
            x, sr = read_wav(f); d = len(x) / sr
            ws = s.split(); tot = sum(len(w_) + 1 for w_ in ws); tt = t
            for w_ in ws:
                dt = d * (len(w_) + 1) / tot; words.append((w_, tt, tt + dt, i)); tt += dt
            parts += [x, np.zeros(int(0.28 * sr))]; t += d + 0.28
        a = np.concatenate(parts)
    words = anzeige_woerter(words)   # „…“ an das Wort davor hängen, einzelne „–“ nicht als Untertitel zeigen
    # Pause nach der Aussage einfügen (nur im Profil 'alt'; sonst entsteht die Pause beim Sprechen)
    lead = 0.15
    hook_last = max(k for k, w_ in enumerate(words) if w_[3] < n_hook)
    if HOOK_HOLD > 0 and hook_last + 1 < len(words):
        cut = (words[hook_last][2] + words[hook_last + 1][1]) / 2
        ci = int(cut * sr)
        a = np.concatenate([a[:ci], np.zeros(int(HOOK_HOLD * sr)), a[ci:]])
        words = [(w_, s0 + (HOOK_HOLD if k > hook_last else 0), s1 + (HOOK_HOLD if k > hook_last else 0), i) for k, (w_, s0, s1, i) in enumerate(words)]
    a = np.concatenate([np.zeros(int(lead * sr)), a])
    words = [(w_, s0 + lead, s1 + lead, i) for w_, s0, s1, i in words]
    timing = []
    for i in range(len(sentences)):
        ws = [w_ for w_ in words if w_[3] == i]; timing.append((ws[0][1], ws[-1][2]))
    return a, sr, words, timing

def pad_music(n, sr):
    """Leiser, selbst erzeugter Klangteppich (keine Lizenzfrage)."""
    t = np.arange(n) / sr
    chord = [110.0, 164.81, 220.0, 277.18]  # A-Dur-Fläche, sehr leise
    m = sum(np.sin(2 * np.pi * f * t + i) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.05 * t + i)) for i, f in enumerate(chord))
    m *= 0.012
    fade = np.minimum(1, t / 2.0) * np.minimum(1, (t[-1] - t) / 2.0)
    return m * fade

def write_wav(a, sr, f):
    a = a / max(1e-6, np.abs(a).max()) * 0.89
    with wave.open(f, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((a * 32767).astype(np.int16).tobytes())

# ---------- Grafik ----------
def font(name, size, var=None):
    f = ImageFont.truetype(FONT(name), size)
    if var:
        try: f.set_variation_by_name(var)
        except Exception: pass
    return f

def wrap(draw, text, f, maxw):
    words, lines, cur = text.split(), [], ''
    for w_ in words:
        t = (cur + ' ' + w_).strip()
        if draw.textlength(t, font=f) <= maxw: cur = t
        else: lines.append(cur); cur = w_
    if cur: lines.append(cur)
    return lines

PAPER = (244, 247, 251)
GOLD = (201, 164, 92); CREAM = (236, 224, 196); NIGHT = (11, 15, 24)
PAL = {
    'himmel': dict(ink=NAVY, acc=RED, dimbg=PAPER, label=NAVY, name=NAVY, endbg=(255, 255, 255), endink=NAVY, endink2=GREY),
    'cover':  dict(ink=NAVY, acc=RED, dimbg=PAPER, label=NAVY, name=NAVY, endbg=(255, 255, 255), endink=NAVY, endink2=GREY),
    'klar':   dict(ink=NAVY, acc=RED, dimbg=PAPER, label=NAVY, name=NAVY, endbg=(255, 255, 255), endink=NAVY, endink2=GREY),
    'tusche': dict(ink=CREAM, acc=GOLD, dimbg=NIGHT, label=GOLD, name=(150, 132, 100), endbg=NIGHT, endink=CREAM, endink2=GOLD),
}
def mixc(c, bg, a):
    a = max(0.0, min(1.0, a)); return tuple(int(bg[i] + (c[i] - bg[i]) * a) for i in range(3))

def ease(x): x = max(0, min(1, x)); return 1 - (1 - x) ** 3

def clouds_layer(seed=3):
    rng = np.random.default_rng(seed)
    w, h = W * 2, H
    acc = np.zeros((h, w), np.float32)
    for k, sc in enumerate([8, 16, 32, 64]):
        small = rng.random((h // (240 // (2 ** k)) + 2, w // (240 // (2 ** k)) + 2)).astype(np.float32)
        im = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
        acc += np.asarray(im, np.float32) / 255 / (2 ** k)
    acc /= acc.max()
    mask = np.clip((acc - 0.55) * 3.2, 0, 1)
    ys = np.linspace(0, 1, h)[:, None]
    mask *= (0.35 + 0.65 * ys)                      # mehr Wolken unten
    img = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(18))
    return np.asarray(img, np.float32) / 255

def sky_gradient():
    ys = np.linspace(0, 1, H)[:, None, None]
    top, bot = np.array(SKY, np.float32), np.array(SKY_L, np.float32)
    return np.broadcast_to(top * (1 - ys) + bot * ys, (H, W, 3)).copy()

class Background:
    def __init__(self, style):
        self.style = style
        if style == 'himmel':
            self.grad = sky_gradient(); self.cl = clouds_layer()
        elif style == 'cover':
            front = buchcover()
            sc = (H * 1.18) / front.height
            front = front.resize((int(front.width * sc), int(front.height * sc)), Image.LANCZOS)
            self.big = front.filter(ImageFilter.GaussianBlur(30))
        elif style == 'tusche':
            ys = np.linspace(0, 1, H)[:, None, None]
            top, bot = np.array((16, 24, 40), np.float32), np.array((7, 9, 14), np.float32)
            self.grad = np.broadcast_to(top * (1 - ys) + bot * ys, (H, W, 3)).copy()
            self.grad += np.random.default_rng(2).normal(0, 1.6, (H, W, 1)).astype(np.float32)
            self.cl = clouds_layer(seed=11)
            rng = np.random.default_rng(5)
            self.petals = [(rng.uniform(0, W), rng.uniform(-H, H), rng.uniform(6, 14), rng.uniform(18, 40), rng.uniform(0, 6.28)) for _ in range(14)]
        else:
            base = np.full((H, W, 3), (244, 248, 252), np.float32)
            noise = np.random.default_rng(1).normal(0, 2.2, (H, W, 1)).astype(np.float32)
            self.flat = np.clip(base + noise, 0, 255).astype(np.uint8)

    def frame(self, t):
        if self.style == 'himmel':
            off = int((t * 14) % W)
            c = self.cl[:, off:off + W, None]
            arr = self.grad * (1 - c * 0.85) + 255 * c * 0.85
            return Image.fromarray(arr.astype(np.uint8))
        if self.style == 'cover':
            z = 1 + 0.04 * (t / 40)
            bw, bh = self.big.size
            cw, ch = int(W / z * (bh / H) / 1.18), int(H / z / 1.18 * (bh / H) * 1.18 / 1.0)
            cw, ch = min(bw, int(bh * W / H / z)), min(bh, int(bh / z))
            x0 = int((bw - cw) / 2 + math.sin(t / 9) * 25); y0 = int((bh - ch) / 2)
            im = self.big.crop((x0, y0, x0 + cw, y0 + ch)).resize((W, H), Image.BILINEAR)
            veil = Image.new('RGB', (W, H), WHITE)
            return Image.blend(im, veil, 0.5)
        if self.style == 'tusche':
            off = int((t * 9) % W)
            c = self.cl[:, off:off + W, None]
            mist = np.array((120, 92, 60), np.float32)
            arr = self.grad * (1 - c * 0.22) + mist * c * 0.22
            im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert('RGBA')
            lay = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
            for x0, y0, r, v, ph in self.petals:
                y = (y0 + v * t) % (H + 200) - 100; x = x0 + 30 * math.sin(t / 3 + ph)
                d.ellipse((x - r, y - r * 0.6, x + r, y + r * 0.6), fill=(236, 224, 196, 60))
            im.alpha_composite(lay.filter(ImageFilter.GaussianBlur(1.2)))
            return im.convert('RGB')
        return Image.fromarray(self.flat)

def enso(img, cx, cy, R, prog, col=GOLD):
    """Pinselkreis (Enso) in Gold, zeichnet sich mit prog 0..1."""
    lay = Image.new('RGBA', img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    steps = int(260 * max(0, min(1, prog)))
    rng = np.random.default_rng(9)
    jit = rng.normal(0, 1.2, 300)
    for k in range(steps):
        u = k / 260; ang = math.radians(-60 + 320 * u)
        w = 22 * (1 - u) ** 0.6 * (0.8 + 0.2 * math.sin(u * 17)) + 3
        x = cx + (R + jit[k] * 2) * math.cos(ang); y = cy + (R + jit[k] * 2) * math.sin(ang)
        d.ellipse((x - w, y - w, x + w, y + w), fill=col + (int(200 * (0.55 + 0.45 * (1 - u))),))
    img.alpha_composite(lay.filter(ImageFilter.GaussianBlur(1.5)))

def buchcover():
    p = os.path.join(BASE, 'buchcover.jpg')
    if os.path.exists(p): return Image.open(p).convert('RGB')
    c = Image.open(os.path.join(BASE, 'cover-1.png')).convert('RGB')
    return c.crop((c.width // 2, 0, c.width, c.height))

def cap_v(f):
    """Sichtbare Oberkante (Großbuchstaben) und Grundlinie relativ zum Zeichenpunkt – für echte Mittigkeit in Kästen."""
    b = f.getbbox('H'); return b[1], b[3]

def card_um(img, f, y_first, n_lines, lh, x0, x1, pad=56, alpha=0.9, extra=0):
    """Kasten, der den sichtbaren Text oben und unten gleich weit umschließt (behebt „Text sitzt zu tief“)."""
    top, base = cap_v(f)
    y0 = y_first + top - pad; y1 = y_first + (n_lines - 1) * lh + base + pad + int(f.size * 0.06) + extra
    card(img, (int(x0), int(y0), int(x1), int(y1)), alpha=alpha)

def serien_pille(d, serie, f, P, x=90, y=246):
    tw = d.textlength(serie, font=f); top, base = cap_v(f)
    d.rounded_rectangle((x - 22, y + top - 16, x + tw + 22, y + base + 16), 30, fill=P['acc'])
    d.text((x, y), serie, font=f, fill=(255, 255, 255) if P['acc'] != GOLD else NIGHT)

def card(img, box, alpha=0.9, radius=36):
    ov = Image.new('RGBA', img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    x0, y0, x1, y1 = box
    sh = Image.new('RGBA', img.size, (0, 0, 0, 0)); ImageDraw.Draw(sh).rounded_rectangle((x0, y0 + 10, x1, y1 + 10), radius, fill=(31, 47, 74, 40))
    sh = sh.filter(ImageFilter.GaussianBlur(16))
    d.rounded_rectangle(box, radius, fill=(255, 255, 255, int(255 * alpha)))
    img.alpha_composite(sh); img.alpha_composite(ov)

# ---------- Szene ----------
PUNCT = (',', '.', ';', ':', '?', '!', '–', '…')

def hook_satzzahl(sentences, hook):
    h = re.sub(r'\W', '', hook.lower()); acc = ''
    for i, s_ in enumerate(sentences):
        acc += re.sub(r'\W', '', s_.lower())
        if len(acc) >= len(h): return i + 1
    return 1

SCHWACH = set('der die das den dem des ein eine einen einem einer am im an in zu zum zur von vom mit auf für bei nach aus und oder aber nicht kein keine du ich er sie es wir ihr dein deine mein meine sein seine ist bist sind war hat hast habe wird wirst'.split())
CAP_MAX_W = 7          # Untertitel: bis zu 7 Wörter gleichzeitig, auf höchstens 2 Zeilen (seit Charge 2)

def chunks_of(words, d, f, maxw):
    """Untertitel-Blöcke (Satzstücke) von bis zu CAP_MAX_W Wörtern auf höchstens 2 Zeilen.
    Bricht am Satzende, an Satzzeichen ab 4 Wörtern, sonst nach CAP_MAX_W Wörtern."""
    out = []
    by_sent = {}
    for k, w_ in enumerate(words): by_sent.setdefault(w_[3], []).append(k)
    wid = lambda ids: d.textlength(' '.join(words[x][0] for x in ids), font=f)
    for si in sorted(by_sent):
        cur, blocks = [], []
        for k in by_sent[si]:
            schwach = cur and words[cur[-1]][0].lower() in SCHWACH and len(cur) < CAP_MAX_W + 2 and wid(cur + [k]) <= 2 * maxw * 0.92
            if cur and not schwach and (len(cur) >= CAP_MAX_W or wid(cur + [k]) > 2 * maxw * 0.92 or (words[cur[-1]][0].endswith(PUNCT) and len(cur) >= 4)):
                blocks.append(cur); cur = []
            cur.append(k)
        if cur: blocks.append(cur)
        # einzelnes Wort am Satzende an den vorigen Block hängen, wenn es passt
        if len(blocks) >= 2 and len(blocks[-1]) <= 2 and len(blocks[-2]) + len(blocks[-1]) <= CAP_MAX_W + 1 and wid(blocks[-2] + blocks[-1]) <= 2 * maxw * 0.92:
            blocks[-2] += blocks.pop()
        out += blocks
    return out

def render(beitrag, style, out, voice):
    global HOOK_HOLD
    prof = stimmprofil(beitrag); HOOK_HOLD = prof.get('hook_hold', 0.45)
    print('Stimmprofil:', prof)
    sentences = saetze(TAG.sub('', beitrag['sprechtext']))
    n_hook = hook_satzzahl(sentences, beitrag.get('hook', sentences[0]))
    nr = beitrag.get('serie_nummer')
    serie = beitrag['serie'].upper() + (f' #{nr}' if nr else '')
    with_end = bool(beitrag.get('endkarte', beitrag.get('buchhinweis')))   # Endtafel nur, wo gewollt (seit Charge 2: Tiefer)
    zitat_von = beitrag.get('zitat_von')   # z. B. „Seneca“: wird unter der Aussage gezeigt
    with tempfile.TemporaryDirectory() as tmp:
        vt, sr, words, timing = build_audio(sentences, n_hook, voice, tmp, prof, beitrag['sprechtext'])
        sentences = [anzeige_satz(s_) for s_ in sentences]
        if beitrag.get('zitat_lauter', bool(zitat_von)):   # Zitat am Anfang präsenter, Erklärung danach ruhiger
            g = float(beitrag.get('zitat_gain', 1.35)); cut = int(timing[n_hook - 1][1] * sr); ramp = int(0.25 * sr)
            env = np.ones(len(vt), np.float32); env[:cut] = g
            env[cut:cut + ramp] = np.linspace(g, 1.0, len(env[cut:cut + ramp]))
            vt = vt * env
        tail = END_CARD if with_end else 0.5
        total = len(vt) / sr + tail
        vt = np.pad(vt, (0, int(total * sr) - len(vt)))
        mix = vt + pad_music(len(vt), sr)
        wav = os.path.join(tmp, 'a.wav'); write_wav(mix, sr, wav)
        bg = Background(style)
        P = PAL[style]
        f_hook = font('Lora.ttf', 104, 'Bold') if style == 'tusche' else font('Poppins-Bold.ttf', 104)
        f_cap = font('Poppins-Bold.ttf', 76)
        f_top = font('Lora.ttf', 44, 'Medium') if style == 'tusche' else font('Poppins-SemiBold.ttf', 42)
        f_zit = font('Lora.ttf', 46, 'Medium')
        f_lab = font('Poppins-Medium.ttf', 34); f_end = font('Poppins-Bold.ttf', 76); f_end2 = font('Lora.ttf', 44, 'Regular')
        cover = buchcover(); cover.thumbnail((620, 870), Image.LANCZOS)
        cov_s = buchcover(); cov_s.thumbnail((240, 330), Image.LANCZOS)
        n = int(total * FPS)
        probe = ImageDraw.Draw(Image.new('RGB', (10, 10)))
        cap_w = CAP_W
        expl = [w_ for w_ in words if w_[3] >= n_hook]
        chs = chunks_of(expl, probe, f_cap, cap_w)
        ch_start = [expl[c[0]][1] for c in chs]
        def zeilen_von(c, f_):   # Block in Zeilen (Listen von Wortindizes) umbrechen
            lines, cur = [], []
            for x in c:
                if cur and probe.textlength(' '.join(expl[q][0] for q in cur + [x]), font=f_) > CAP_W: lines.append(cur); cur = []
                cur.append(x)
            if cur: lines.append(cur)
            return lines
        def fit_cap(c):   # Schrift je Block verkleinern, bis er auf höchstens 2 Zeilen in den sicheren Bereich passt
            for sz in range(76, 47, -4):
                f_ = font('Poppins-Bold.ttf', sz); ls_ = zeilen_von(c, f_)
                if len(ls_) <= 2 and all(probe.textlength(' '.join(expl[q][0] for q in l), font=f_) <= CAP_W for l in ls_): return f_, ls_
            return f_, ls_
        cap_fit = [fit_cap(c) for c in chs]
        hook_text = ' '.join(sentences[:n_hook])
        hook_end = timing[n_hook - 1][1] + max(HOOK_HOLD, 0.3) * 0.8
        end_start = total - tail
        ff = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                               '-i', wav, '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
                               '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', out], stdin=subprocess.PIPE)
        top_lines = wrap(probe, hook_text, f_top, 740)
        # Aussage passt immer aufs Bild: Schrift verkleinern, bis höchstens 4 Zeilen (satzweise umbrochen) und jedes Wort passt
        hw = HOOK_W - (20 if style == 'tusche' else 0)
        for size in range(104, 51, -4):
            f_hook_fit = font('Lora.ttf', size, 'Bold') if style == 'tusche' else font('Poppins-Bold.ttf', size)
            hook_lines = [ln for s_ in sentences[:n_hook] for ln in wrap(probe, s_, f_hook_fit, hw)]   # Zeilenumbruch satzweise
            if len(hook_lines) <= 4 and all(probe.textlength(l, font=f_hook_fit) <= hw for l in hook_lines): break
        hook_lh = int(size * 1.2)
        for i in range(n):
            t = i / FPS
            img = bg.frame(t).convert('RGBA'); d = ImageDraw.Draw(img)
            if not (with_end and t >= end_start):
                # Serienlabel (unterhalb der Plattform-Leiste)
                serien_pille(d, serie, f_lab, P)
                nm = 'Der eigene Ratgeber'; d.text((W - 90 - d.textlength(nm, font=f_lab), 250), nm, font=f_lab, fill=P['name'])
                if t < hook_end + 0.25:
                    a = 1.0   # Startbild: Aussage steht ab dem ersten Bild vollständig (auch Vorschaubild)
                    lines, lh = hook_lines, hook_lh; f_hook = f_hook_fit
                    top_, base_ = cap_v(f_hook)
                    y = H * 0.40 - ((len(lines) - 1) * lh + base_ + top_) / 2
                    fade = 1 if t < hook_end else max(0, 1 - (t - hook_end) / 0.25)
                    if style in ('himmel', 'cover'):
                        card_um(img, f_hook, y, len(lines), lh, CX - HOOK_W / 2 - 50, CX + HOOK_W / 2 + 50, pad=72, alpha=0.88 * fade, extra=80 if zitat_von else 0); d = ImageDraw.Draw(img)
                    # kleines Buchcover unter der Aussage (Wiedererkennung im Vorschaubild)
                    yb_ = y + (len(lines) - 1) * lh + base_ + 150 + (80 if zitat_von and style in ('himmel', 'cover') else 0)
                    if fade > 0 and yb_ + cov_s.height < 1500:
                        cv = cov_s.convert('RGBA'); cv.putalpha(int(255 * fade))
                        img.alpha_composite(cv, (int(CX - cov_s.width / 2), int(yb_))); d = ImageDraw.Draw(img)
                    if style == 'tusche':
                        enso(img, CX, y + ((len(lines) - 1) * lh + base_ + top_) / 2, 440, 1.0); d = ImageDraw.Draw(img)
                    col = mixc(P['ink'], P['dimbg'], a * fade)
                    for k, ln in enumerate(lines):
                        tw = d.textlength(ln, font=f_hook); d.text((CX - tw / 2, y + k * lh), ln, font=f_hook, fill=col)
                    if zitat_von:   # Urheber des Zitats unter der Aussage
                        zt = '– ' + zitat_von; zy = y + (len(lines) - 1) * lh + base_ + 34
                        d.text((CX - d.textlength(zt, font=f_zit) / 2, zy), zt, font=f_zit, fill=mixc(P['acc'], P['dimbg'], fade))
                    if style == 'klar':
                        yb = y + (len(lines) - 1) * lh + base_ + 50
                        d.line((CX - 70 * a, yb, CX + 70 * a, yb), fill=RED, width=8)
                else:
                    # Aussage bleibt klein oben stehen (Kontext für Späteinsteiger)
                    ta = ease((t - hook_end) / 0.5) * 0.75
                    for k, ln in enumerate(top_lines):
                        d.text((90, 350 + k * 58), ln, font=f_top, fill=mixc(P['ink'], P['dimbg'], ta))
                    # aktueller Untertitel-Block
                    ci = max([k for k, s0 in enumerate(ch_start) if s0 <= t + 0.04] or [0])
                    blk = chs[ci]; f_cap, cap_lines = cap_fit[ci]
                    since = t - ch_start[ci]; pop = ease(since / 0.12) if since >= 0 else 0
                    clh = int(f_cap.size * 1.22); top_, base_ = cap_v(f_cap)
                    widths = [d.textlength(' '.join(expl[q][0] for q in l), font=f_cap) for l in cap_lines]
                    y = H * 0.56 - ((len(cap_lines) - 1) * clh + base_ + top_) / 2 + (1 - pop) * 14
                    if style in ('himmel', 'cover'):
                        card_um(img, f_cap, y, len(cap_lines), clh, CX - max(widths) / 2 - 44, CX + max(widths) / 2 + 44, pad=40, alpha=0.86); d = ImageDraw.Draw(img)
                    cur_q = max([q for q in blk if expl[q][1] <= t + 0.03] or [blk[0]])
                    for li, l in enumerate(cap_lines):
                        x = CX - widths[li] / 2
                        for q in l:
                            w_ = expl[q]; spoken = w_[1] <= t + 0.03
                            colr = P['acc'] if q == cur_q else (P['ink'] if spoken else mixc(P['ink'], P['dimbg'], 0.45))
                            d.text((x, y + li * clh), w_[0], font=f_cap, fill=mixc(colr, P['dimbg'], pop))
                            x += d.textlength(w_[0] + ' ', font=f_cap)
            else:
                a = ease((t - end_start) / 0.5)
                ov = Image.new('RGBA', img.size, P['endbg'] + (int(235 * a),)); img.alpha_composite(ov); d = ImageDraw.Draw(img)
                cx = (W - cover.width) // 2; cy = 360 + int((1 - a) * 40)
                img.alpha_composite(cover.convert('RGBA'), (cx, cy)); d = ImageDraw.Draw(img)
                for k, (txt, f_, colr) in enumerate([('Der eigene Ratgeber', f_end, P['endink']), ('Das Buch – Link im Profil', f_end2, P['endink2'])]):
                    tw = d.textlength(txt, font=f_); d.text(((W - tw) / 2, cy + cover.height + 70 + k * 100), txt, font=f_, fill=mixc(colr, P['endbg'], a))
            ff.stdin.write(img.convert('RGB').tobytes())
        ff.stdin.close(); ff.wait()
        if ff.returncode: raise RuntimeError('FFmpeg-Fehler')
    return total

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('json'); ap.add_argument('stil'); ap.add_argument('out')
    ap.add_argument('--stimme', default='elevenlabs', help="'elevenlabs', 'stumm' (Test ohne Ton) oder Name einer Piper-Stimme (Rückfall)"); ap.add_argument('--index', type=int, default=0)
    a = ap.parse_args()
    b = json.load(open(a.json)); b = b[a.index] if isinstance(b, list) else b
    stil = b.get('stil', 'himmel') if a.stil == 'auto' else a.stil
    voice = a.stimme if a.stimme in ('elevenlabs', 'stumm') else (a.stimme if a.stimme.endswith('.onnx') else os.path.join(BASE, 'voices', a.stimme + '.onnx'))
    d = render(b, stil, a.out, voice)
    print(f'{a.out}: {d:.1f} s')
