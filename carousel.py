#!/usr/bin/env python3
"""Karussell-Folien „Der eigene Ratgeber“ (1080x1350, 4:5) aus Autor-JSON.

Aufruf: python3 carousel.py beitrag.json stil ausgabeordner [--index N]
Stile: klar | himmel | tusche
"""
import json, os, re, argparse
from PIL import Image, ImageDraw
import render as R

CW, CH = 1080, 1350

def bg_image(style):
    b = R.Background(style)
    im = b.frame(0.0).convert('RGBA')
    top = (R.H - CH) // 2
    return im.crop((0, top, CW, top + CH))

def kicker_split(txt):
    m = re.match(r'^([^:]{2,32}):\s+(.*)$', txt)
    if m and len(m.group(1).split()) <= 4:
        return m.group(1), m.group(2)
    return None, txt

def draw_lines(d, lines, f, x, y, lh, fill, center=False):
    for k, ln in enumerate(lines):
        xx = (CW - d.textlength(ln, font=f)) / 2 if center else x
        d.text((xx, y + k * lh), ln, font=f, fill=fill)

def slides(beitrag, style, outdir):
    os.makedirs(outdir, exist_ok=True)
    P = R.PAL[style]
    serif = style == 'tusche'
    f_big = R.font('Lora.ttf', 92, 'Bold') if serif else R.font('Poppins-Bold.ttf', 86)
    f_quote = R.font('Lora.ttf', 70, 'Medium')
    f_body = R.font('Poppins-Medium.ttf', 52)
    f_kick = R.font('Poppins-SemiBold.ttf', 34)
    f_small = R.font('Poppins-Medium.ttf', 30)
    folien = beitrag['folien']; n = len(folien)
    serie = beitrag['serie'].upper() + (f" #{beitrag['serie_nummer']}" if beitrag.get('serie_nummer') else '')
    is_quote = beitrag['serie'] in ('Ein Satz', 'Satz aus dem Buch')
    paths = []
    base = bg_image(style)
    for i, txt in enumerate(folien):
        img = base.copy(); d = ImageDraw.Draw(img)
        d.text((80, 90), serie, font=f_small, fill=P['label'])
        d.line((80, 138, 160, 138), fill=P['acc'], width=5)
        last = i == n - 1
        if i == 0:
            f = f_quote if is_quote else f_big
            lh = 96 if is_quote else 108
            lines = R.wrap(d, txt, f, CW - (330 if style == 'tusche' else 220))
            y = CH * 0.46 - len(lines) * lh / 2
            if style == 'himmel':
                R.card(img, (60, int(y - 70), CW - 60, int(y + len(lines) * lh + 60))); d = ImageDraw.Draw(img)
            if style == 'tusche':
                R.enso(img, CW / 2, y + len(lines) * lh / 2 - 8, 440, 1.0); d = ImageDraw.Draw(img)
            draw_lines(d, lines, f, 0, y, lh, P['ink'], center=True)
            if style == 'klar':
                yb = y + len(lines) * lh + 50; d.line((CW / 2 - 70, yb, CW / 2 + 70, yb), fill=P['acc'], width=7)
            if n > 1:
                hint = 'weiter'; hx = CW - 80 - 50 - d.textlength(hint, font=f_small)
                d.text((hx, CH - 120), hint, font=f_small, fill=P['acc'])
                ax = CW - 80 - 36; ay = CH - 120 + 21
                d.line((ax, ay, ax + 34, ay), fill=P['acc'], width=4); d.line((ax + 22, ay - 11, ax + 34, ay), fill=P['acc'], width=4); d.line((ax + 22, ay + 11, ax + 34, ay), fill=P['acc'], width=4)
        elif last and (txt.startswith('Aus:') or txt.startswith('Quelle') or kicker_split(txt)[0] == 'Quelle'):
            # Schlussfolie: Quelle (falls vorhanden) + Buchcover
            y = 250
            if not txt.startswith('Aus:'):
                kick, body = kicker_split(txt)
                d.text((100, y), 'QUELLE', font=f_kick, fill=P['acc']); y += 60
                for ln in R.wrap(d, body, f_small, CW - 200): d.text((100, y), ln, font=f_small, fill=P['ink']); y += 44
                y += 40
            cov = R.buchcover()
            ch_ = min(640, CH - y - 330); cov.thumbnail((int(ch_ * 0.72), ch_), Image.LANCZOS)
            img.alpha_composite(cov.convert('RGBA'), ((CW - cov.width) // 2, int(y))); d = ImageDraw.Draw(img)
            y += cov.height + 50
            for txt2, f2, col in [('Aus dem Buch', f_small, P['acc']), ('Der eigene Ratgeber', R.font('Poppins-Bold.ttf', 60), P['ink'])]:
                d.text(((CW - d.textlength(txt2, font=f2)) / 2, y), txt2, font=f2, fill=col); y += 60
        else:
            kick, body = kicker_split(txt)
            small = last and (body.startswith('Quelle') or kick == 'Quelle' or txt.startswith('Aus:'))
            f = f_small if small else f_body
            lh = 44 if small else 74
            lines = R.wrap(d, body, f, CW - 200)
            block = len(lines) * lh + (70 if kick else 0)
            y = CH * 0.47 - block / 2
            if style == 'himmel':
                R.card(img, (60, int(y - 60), CW - 60, int(y + block + 50))); d = ImageDraw.Draw(img)
            if kick:
                d.text((100, y), kick.upper(), font=f_kick, fill=P['acc']); y += 70
            draw_lines(d, lines, f, 100, y, lh, P['ink'])
        name = 'Der eigene Ratgeber'
        d.text((80, CH - 120), name, font=f_small, fill=P['name'])
        if n > 1:
            pg = f'{i + 1} / {n}'
            if i > 0: d.text((CW - 80 - d.textlength(pg, font=f_small), CH - 120), pg, font=f_small, fill=P['name'])
        p = os.path.join(outdir, f'{beitrag["einheit_id"]}_{i + 1:02d}.png')
        img.convert('RGB').save(p, quality=95); paths.append(p)
    return paths

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('json'); ap.add_argument('stil'); ap.add_argument('out'); ap.add_argument('--index', type=int, default=0)
    a = ap.parse_args()
    b = json.load(open(a.json)); b = b[a.index] if isinstance(b, list) else b
    stil = b.get('stil', 'klar') if a.stil == 'auto' else a.stil
    for p in slides(b, stil, a.out): print(p)
