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
PAUSE = 0.38          # Pause zwischen Sätzen (s)
HOOK_HOLD = 0.6       # Aussage wirkt kurz nach
END_CARD = 2.4        # Schlusstafel mit Buch (nur bei Buchhinweis)

# ---------- Text und Stimme ----------
def saetze(text):
    parts = re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÜ„"])', text.strip())
    return [p.strip() for p in parts if p.strip()]

def tts(text, voice, out, length_scale=1.12):
    p = subprocess.run([sys.executable, '-m', 'piper', '-m', voice, '-f', out,
                        '--length-scale', str(length_scale), '--sentence-silence', '0'],
                       input=text.encode(), capture_output=True)
    if p.returncode: raise RuntimeError(p.stderr.decode()[-500:])

def _el_request(text):
    import base64, urllib.request, urllib.error
    key = os.environ['ELEVENLABS_API_KEY'].strip(); voice = os.environ['ELEVENLABS_VOICE_ID'].strip()
    body = json.dumps({'text': text, 'model_id': os.environ.get('ELEVENLABS_MODEL', 'eleven_multilingual_v2'),
                       'voice_settings': {'stability': 0.55, 'similarity_boost': 0.8, 'style': 0.15, 'speed': 0.95}}).encode()
    req = urllib.request.Request(f'https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128',
                                 data=body, headers={'xi-api-key': key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'ElevenLabs-Fehler {e.code}: {e.read().decode(errors="replace")[:600]}')
    return base64.b64decode(res['audio_base64']), (res.get('alignment') or res.get('normalized_alignment'))

def _words_from_alignment(al):
    words, cur, t0, t1 = [], '', None, None
    for ch, a, b in zip(al['characters'], al['character_start_times_seconds'], al['character_end_times_seconds']):
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

def build_audio(sentences, n_hook, voice, tmp):
    """Sprechtext -> (Tonspur, sr, Wortliste [(wort, start, ende, satzindex)], Satz-Zeiten, Dauer).
    ElevenLabs: EIN Aufruf für den ganzen Text (natürliche Betonung, wenige Anfragen). Nach der Aussage wird eine Pause eingefügt."""
    toks = [(w_, i) for i, s in enumerate(sentences) for w_ in s.split()]
    words = []
    if voice == 'elevenlabs':
        mp3, al = _el_request(' '.join(sentences))
        open(os.path.join(tmp, 'v.mp3'), 'wb').write(mp3)
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', os.path.join(tmp, 'v.mp3'), '-ac', '1', '-ar', '44100', os.path.join(tmp, 'v.wav')], check=True)
        a, sr = read_wav(os.path.join(tmp, 'v.wav'))
        wl = _words_from_alignment(al)
        if len(wl) != len(toks):   # Sicherheitsnetz: proportional verteilen
            d = len(a) / sr; tot = sum(len(w_) + 1 for w_, _ in toks); t = 0
            wl = []
            for w_, _ in toks:
                dt = d * (len(w_) + 1) / tot; wl.append((w_, t, t + dt)); t += dt
        words = [(toks[k][0], wl[k][1], wl[k][2], toks[k][1]) for k in range(len(toks))]
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
    # Pause nach der Aussage einfügen
    lead = 0.15
    hook_last = max(k for k, w_ in enumerate(words) if w_[3] < n_hook)
    if hook_last + 1 < len(words):
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

def chunks_of(words, d, f, maxw):
    """Untertitel-Blöcke von 2-4 Wörtern: bricht an Satzzeichen, sonst nach 3 Wörtern."""
    out = []
    by_sent = {}
    for k, w_ in enumerate(words): by_sent.setdefault(w_[3], []).append(k)
    for si in sorted(by_sent):
        ids = by_sent[si]; cur = []
        for j, k in enumerate(ids):
            cand = cur + [k]
            if cur and d.textlength(' '.join(words[x][0] for x in cand), font=f) > maxw:
                out.append(cur); cur = [k]
            else:
                cur = cand
            rest = len(ids) - j - 1
            if (words[k][0].endswith(PUNCT) and len(cur) >= 2 and rest != 1) or (len(cur) >= 3 and rest not in (1,)) or len(cur) >= 4:
                if rest == 0 or len(cur) >= 2: out.append(cur); cur = []
        if cur:
            if out and len(cur) == 1 and len(out[-1]) < 4 and words[out[-1][0]][3] == si: out[-1] += cur
            else: out.append(cur)
    return out

def render(beitrag, style, out, voice):
    sentences = saetze(beitrag['sprechtext'])
    n_hook = hook_satzzahl(sentences, beitrag.get('hook', sentences[0]))
    nr = beitrag.get('serie_nummer')
    serie = beitrag['serie'].upper() + (f' #{nr}' if nr else '')
    with_end = bool(beitrag.get('buchhinweis'))
    with tempfile.TemporaryDirectory() as tmp:
        vt, sr, words, timing = build_audio(sentences, n_hook, voice, tmp)
        tail = END_CARD if with_end else 0.7
        total = len(vt) / sr + tail
        vt = np.pad(vt, (0, int(total * sr) - len(vt)))
        mix = vt + pad_music(len(vt), sr)
        wav = os.path.join(tmp, 'a.wav'); write_wav(mix, sr, wav)
        bg = Background(style)
        P = PAL[style]
        f_hook = font('Lora.ttf', 104, 'Bold') if style == 'tusche' else font('Poppins-Bold.ttf', 104)
        f_cap = font('Poppins-Bold.ttf', 106)
        f_top = font('Lora.ttf', 44, 'Medium') if style == 'tusche' else font('Poppins-SemiBold.ttf', 42)
        f_lab = font('Poppins-Medium.ttf', 34); f_end = font('Poppins-Bold.ttf', 76); f_end2 = font('Lora.ttf', 44, 'Regular')
        cover = buchcover(); cover.thumbnail((620, 870), Image.LANCZOS)
        n = int(total * FPS)
        probe = ImageDraw.Draw(Image.new('RGB', (10, 10)))
        cap_w = W - 280
        expl = [w_ for w_ in words if w_[3] >= n_hook]
        chs = chunks_of(expl, probe, f_cap, cap_w)
        ch_start = [expl[c[0]][1] for c in chs]
        hook_text = ' '.join(sentences[:n_hook])
        hook_end = timing[n_hook - 1][1] + HOOK_HOLD * 0.8
        end_start = total - tail
        ff = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                               '-i', wav, '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
                               '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', out], stdin=subprocess.PIPE)
        top_lines = wrap(probe, hook_text, f_top, W - 200)
        # Aussage passt immer aufs Bild: Schrift verkleinern, bis höchstens 4 Zeilen (satzweise umbrochen) und jedes Wort passt
        hw = W - 340 if style == 'tusche' else W - 220
        for size in range(104, 63, -6):
            f_hook_fit = font('Lora.ttf', size, 'Bold') if style == 'tusche' else font('Poppins-Bold.ttf', size)
            hook_lines = [ln for s_ in sentences[:n_hook] for ln in wrap(probe, s_, f_hook_fit, hw)]   # Zeilenumbruch satzweise
            if len(hook_lines) <= 4 and all(probe.textlength(l, font=f_hook_fit) <= hw for l in hook_lines): break
        hook_lh = int(size * 1.2)
        for i in range(n):
            t = i / FPS
            img = bg.frame(t).convert('RGBA'); d = ImageDraw.Draw(img)
            if not (with_end and t >= end_start):
                # Serienlabel (unterhalb der Plattform-Leiste)
                d.text((90, 250), serie, font=f_lab, fill=P['label'])
                d.line((90, 302, 180, 302), fill=P['acc'], width=6)
                nm = 'Der eigene Ratgeber'; d.text((W - 90 - d.textlength(nm, font=f_lab), 250), nm, font=f_lab, fill=P['name'])
                if t < hook_end + 0.25:
                    a = ease((t - 0.05) / 0.35)
                    lines, lh = hook_lines, hook_lh; f_hook = f_hook_fit
                    lh = hook_lh; y = H * 0.44 - len(lines) * lh / 2 + (1 - a) * 40
                    fade = 1 if t < hook_end else max(0, 1 - (t - hook_end) / 0.25)
                    if style in ('himmel', 'cover'):
                        card(img, (70, int(y - 70), W - 70, int(y + len(lines) * lh + 60)), alpha=0.88 * a * fade); d = ImageDraw.Draw(img)
                    if style == 'tusche':
                        enso(img, W / 2, y + len(lines) * lh / 2 - 10, 470, ease((t - 0.05) / 1.2)); d = ImageDraw.Draw(img)
                    col = mixc(P['ink'], P['dimbg'], a * fade)
                    for k, ln in enumerate(lines):
                        tw = d.textlength(ln, font=f_hook); d.text(((W - tw) / 2, y + k * lh), ln, font=f_hook, fill=col)
                    if style == 'klar':
                        yb = y + len(lines) * lh + 60
                        d.line((W / 2 - 70 * a, yb, W / 2 + 70 * a, yb), fill=RED, width=8)
                else:
                    # Aussage bleibt klein oben stehen (Kontext für Späteinsteiger)
                    ta = ease((t - hook_end) / 0.5) * 0.75
                    for k, ln in enumerate(top_lines):
                        d.text((90, 350 + k * 58), ln, font=f_top, fill=mixc(P['ink'], P['dimbg'], ta))
                    # aktueller Untertitel-Block
                    ci = max([k for k, s0 in enumerate(ch_start) if s0 <= t + 0.04] or [0])
                    blk = chs[ci]; txt_words = [expl[x] for x in blk]
                    since = t - ch_start[ci]; pop = ease(since / 0.12) if since >= 0 else 0
                    line = ' '.join(w_[0] for w_ in txt_words)
                    tw = d.textlength(line, font=f_cap)
                    y = H * 0.54 + (1 - pop) * 18
                    if style in ('himmel', 'cover'):
                        card(img, (int((W - tw) / 2 - 50), int(y - 40), int((W + tw) / 2 + 50), int(y + 140)), alpha=0.86); d = ImageDraw.Draw(img)
                    x = (W - tw) / 2
                    cur_k = max([k for k, w_ in enumerate(txt_words) if w_[1] <= t + 0.03] or [0])
                    for k, w_ in enumerate(txt_words):
                        spoken = w_[1] <= t + 0.03
                        current = k == cur_k
                        colr = P['acc'] if current else (P['ink'] if spoken else mixc(P['ink'], P['dimbg'], 0.45))
                        d.text((x, y), w_[0], font=f_cap, fill=mixc(colr, P['dimbg'], pop))
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
    ap.add_argument('--stimme', default='elevenlabs', help="'elevenlabs' oder Name einer Piper-Stimme (Rückfall)"); ap.add_argument('--index', type=int, default=0)
    a = ap.parse_args()
    b = json.load(open(a.json)); b = b[a.index] if isinstance(b, list) else b
    stil = b.get('stil', 'himmel') if a.stil == 'auto' else a.stil
    voice = 'elevenlabs' if a.stimme == 'elevenlabs' else (a.stimme if a.stimme.endswith('.onnx') else os.path.join(BASE, 'voices', a.stimme + '.onnx'))
    d = render(b, stil, a.out, voice)
    print(f'{a.out}: {d:.1f} s')
