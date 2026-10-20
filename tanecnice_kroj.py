#!/usr/bin/env python3
"""Animovany tanec postavicek k pisni "Tanecnice z Lucnice" (Elan). Video je bez zvuku.

Pouziti:
  python3 tanecnice.py                 # finalni video -> output/tanecnice.mp4
  python3 tanecnice.py --preview       # 5 s nahled + output/contact_sheet.png
  python3 tanecnice.py --preview --start 40 --lyrics muj_text.txt

Text pisne s casy patri do lyrics.txt (format je popsany primo v souboru).
Podle textu se kresli karaoke titulky a postavy reaguji na klicova slova.
"""
import argparse
import functools
import math
import os
import random
import re
import subprocess
from multiprocessing import Pool

from PIL import Image, ImageDraw, ImageFont

# ======================= PARAMETRY =======================
BPM = 150               # tempo pisne (odhad - over tap tempem a uprav)
OFFSET = 0.0            # cas prvni doby v nahravce (s); posun, kdyz pohyb "nesedi"
DURATION = 171          # delka videa v s (nahravka Elanu ma 2:51)
WIDTH, HEIGHT = 1280, 720
FPS = 24
SUPERSAMPLE = 2         # vyhlazeni hran
SECTION_BEATS = 16      # po kolika dobach se meni sestava / pozadi
WORKERS = os.cpu_count() or 1
OUTPUT = "output/tanecnice_kroj.mp4"
LYRICS_FILE = "lyrics.txt"

TITLE = "Tanečnice z Lúčnice"
TITLE_SUB = "Elán"
INTRO_SECONDS = 3.2
OUTRO_SECONDS = 4.0
OUTRO_TEXT = "Líbí? Dej like a sdílej!"
SHOW_BADGE = True       # maly nazev v rohu po intru

TITLE_FONT = ["/usr/share/fonts/opentype/inter/InterDisplay-Black.otf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
TEXT_FONT = ["/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]

# ======================= BARVY =======================
INK = (28, 22, 24)
WHITE = (252, 250, 244)
RED = (206, 36, 46)
DARK_RED = (150, 22, 32)
YELLOW = (255, 204, 48)
GREEN = (40, 140, 80)
SKIN = (248, 210, 176)
CHEEK = (242, 150, 150)
HAIR = (96, 56, 30)
VEST = (34, 34, 40)
LINE_W = 0.032          # tloustka car (v jednotkach vysky postavy)

# delky kosti (jednotka = vyska postavy)
TORSO, NECK, HEAD_R = 0.28, 0.015, 0.095
UPPER_ARM, FOREARM = 0.15, 0.14
THIGH, SHIN = 0.21, 0.21

# ======================= POZY =======================
# Uhly ve stupnich, absolutni v obraze: 0 = dolu, +90 = doprava, 180 = nahoru.
BASE = dict(x=0.0, hop=0.0, lean=0.0, head=0.0, turn=0.0, flare=0.0,
            ual=-15, fal=-10, uar=15, far=10,
            thl=-6, shl=-4, thr=6, shr=4)


def P(*parts, **kw):
    d = dict(BASE)
    for p in parts:
        d.update(p)
    d.update(kw)
    return d


HIPS = dict(ual=-50, fal=30, uar=50, far=-30)            # ruce v bok
OUT = dict(ual=-80, fal=-88, uar=80, far=88)             # ruce do stran
UP = dict(ual=-155, fal=-165, uar=155, far=165)          # ruce nahoru
LOW = dict(ual=-30, fal=-20, uar=30, far=20)
CLAP_UP = dict(ual=-160, fal=160, uar=160, far=-160)     # tlesknuti nad hlavou
CLAP_FRONT = dict(ual=-35, fal=112, uar=35, far=-112)    # tlesknuti pred telem
BENT = dict(thl=-14, shl=4, thr=14, shr=-4)
STRAIGHT = dict(thl=-4, shl=-3, thr=4, shr=3)
CROUCH = dict(thl=-26, shl=12, thr=26, shr=-12)
SPLIT = dict(thl=-48, shl=-62, thr=48, shr=62)

# smycky: (delka v dobach, [(doba, poza), ...]) - prvni a posledni poza navazuji
LOOPS = {
    "side": (4, [
        (0.0, P(HIPS, BENT)),
        (0.5, P(HIPS, x=-0.14, lean=-6, hop=0.03, thl=-32, shl=-26, thr=6, shr=6)),
        (1.0, P(HIPS, BENT, x=-0.28)),
        (1.5, P(HIPS, x=-0.14, lean=6, hop=0.03, thl=-6, shl=-6, thr=32, shr=26)),
        (2.0, P(HIPS, BENT)),
        (2.5, P(HIPS, x=0.14, lean=6, hop=0.03, thl=-6, shl=-6, thr=32, shr=26)),
        (3.0, P(HIPS, BENT, x=0.28)),
        (3.5, P(HIPS, x=0.14, lean=-6, hop=0.03, thl=-32, shl=-26, thr=6, shr=6)),
        (4.0, P(HIPS, BENT)),
    ]),
    "spin": (4, [
        (0.0, P(OUT, BENT)),
        (0.5, P(OUT, STRAIGHT, hop=0.02)),
        (1.5, P(OUT, STRAIGHT, turn=0.4, flare=1)),
        (2.5, P(STRAIGHT, ual=-120, fal=-140, uar=120, far=140, turn=0.8, flare=1)),
        (3.0, P(UP, STRAIGHT, turn=1.0, flare=0.4, hop=0.04)),
        (4.0, P(OUT, BENT, turn=1.0)),
    ]),
    "arms_up": (2, [
        (0.0, P(LOW, CROUCH)),
        (1.0, P(UP, STRAIGHT, hop=0.06, head=10)),
        (2.0, P(LOW, CROUCH)),
    ]),
    "hops": (2, [
        (0.0, P(OUT, CROUCH)),
        (0.5, P(hop=0.15, lean=-5, ual=-115, fal=-150, uar=60, far=40,
                thl=-60, shl=-22, thr=5, shr=5)),
        (1.0, P(OUT, CROUCH)),
        (1.5, P(hop=0.15, lean=5, ual=-60, fal=-40, uar=115, far=150,
                thl=-5, shl=-5, thr=60, shr=22)),
        (2.0, P(OUT, CROUCH)),
    ]),
    "clap": (2, [
        (0.0, P(OUT, BENT)),
        (0.5, P(CLAP_UP, STRAIGHT, hop=0.05)),
        (1.0, P(OUT, BENT, x=0.05)),
        (1.5, P(CLAP_FRONT, BENT, lean=-8, x=0.02)),
        (2.0, P(OUT, BENT)),
    ]),
    "kick": (4, [
        (0.0, P(HIPS, BENT)),
        (0.5, P(HIPS, hop=0.05, lean=8, thl=-85, shl=-85, thr=4, shr=4)),
        (1.0, P(HIPS, BENT)),
        (1.5, P(HIPS, hop=0.05, lean=-8, thl=-4, shl=-4, thr=85, shr=85)),
        (2.0, P(HIPS, BENT)),
        (2.5, P(LOW, CROUCH, thl=-34, thr=34)),
        (3.0, P(UP, SPLIT, hop=0.22)),
        (3.5, P(OUT, CROUCH)),
        (4.0, P(HIPS, BENT)),
    ]),
    "sway": (4, [
        (0.0, P(STRAIGHT, lean=-12, x=-0.06, ual=-165, fal=-150, uar=70, far=40)),
        (1.0, P(BENT, lean=0, hop=0.0, ual=-120, fal=-160, uar=120, far=160)),
        (2.0, P(STRAIGHT, lean=12, x=0.06, ual=-70, fal=-40, uar=165, far=150)),
        (3.0, P(BENT, lean=0, ual=-120, fal=-160, uar=120, far=160)),
        (4.0, P(STRAIGHT, lean=-12, x=-0.06, ual=-165, fal=-150, uar=70, far=40)),
    ]),
    "heel": (2, [
        (0.0, P(OUT, BENT)),
        (0.25, P(OUT, hop=0.04, thl=-22, shl=-38, thr=10, shr=0)),
        (0.5, P(OUT, BENT, lean=-4)),
        (0.75, P(OUT, hop=0.04, thl=-10, shl=0, thr=22, shr=38)),
        (1.0, P(OUT, BENT, lean=4)),
        (1.25, P(HIPS, hop=0.04, thl=-22, shl=-38, thr=10, shr=0)),
        (1.5, P(HIPS, CROUCH)),
        (1.75, P(HIPS, STRAIGHT, hop=0.08)),
        (2.0, P(OUT, BENT)),
    ]),
    "point": (2, [
        (0.0, P(BENT, ual=-50, fal=30, uar=88, far=92, lean=4)),
        (0.5, P(STRAIGHT, hop=0.04, ual=-50, fal=30, uar=100, far=120, lean=8)),
        (1.0, P(BENT, ual=-50, fal=30, uar=88, far=92, lean=4)),
        (1.5, P(STRAIGHT, hop=0.04, ual=-50, fal=30, uar=60, far=170, lean=0, head=-10)),
        (2.0, P(BENT, ual=-50, fal=30, uar=88, far=92, lean=4)),
    ]),
    "jump": (2, [
        (0.0, P(OUT, BENT)),
        (0.4, P(LOW, CROUCH, thl=-36, thr=36, lean=0)),
        (0.9, P(UP, SPLIT, hop=0.26, head=8)),
        (1.4, P(OUT, CROUCH)),
        (2.0, P(OUT, BENT)),
    ]),
}

# programy sekci (kazda = SECTION_BEATS dob): (smycka, pocet opakovani)
PROGRAMS = [
    [("side", 2), ("clap", 4)],
    [("hops", 4), ("spin", 2)],
    [("sway", 2), ("arms_up", 4)],
    [("heel", 4), ("kick", 2)],
    [("side", 1), ("spin", 1), ("jump", 2), ("clap", 2)],
    [("point", 2), ("hops", 2), ("kick", 2)],
    [("clap", 2), ("heel", 2), ("spin", 2)],
    [("kick", 1), ("side", 1), ("arms_up", 2), ("jump", 2)],
]
CHORUS_PROGRAMS = [
    [("hops", 2), ("spin", 1), ("clap", 2), ("jump", 2)],
    [("kick", 2), ("spin", 2)],
    [("side", 1), ("jump", 2), ("heel", 2), ("spin", 1), ],
]
HEART_LOOPS = {"sway", "point"}     # pri techto smyckach leti srdicka

# rozestaveni: figs = (role f/m, x 0..1, meritko, zrcadlit, posun do hloubky, posun programu)
# travel: None | "drift" | "swap" | "circle"
CONFIGS = [
    dict(travel="drift", figs=[("f", 0.50, 1.00, False, 0.00, 0)]),
    dict(travel="swap", figs=[("f", 0.34, 1.00, False, 0.00, 0), ("m", 0.66, 1.07, True, 0.00, 0)]),
    dict(travel="drift", figs=[("f", 0.34, 1.00, False, 0.00, 0), ("m", 0.66, 1.07, True, 0.00, 3)]),
    dict(travel=None, figs=[("m", 0.20, 0.74, True, 0.07, 1), ("m", 0.80, 0.74, False, 0.07, 1),
                            ("f", 0.50, 1.00, False, 0.00, 0)]),
    dict(travel="circle", figs=[("f", 0.50, 1.00, False, 0.00, 0), ("m", 0.50, 1.07, True, 0.00, 0)]),
    dict(travel="drift", figs=[("m", 0.50, 1.07, False, 0.00, 0)]),
]
CHORUS_CONFIG = dict(travel="circle", figs=[
    ("f", 0.15, 0.66, False, 0.10, 2), ("f", 0.85, 0.66, True, 0.10, 2),
    ("f", 0.50, 1.00, False, 0.00, 0), ("m", 0.50, 1.07, True, 0.00, 0)])

BACKGROUNDS = [
    dict(sky=((255, 232, 192), (255, 206, 152)), floor=(190, 132, 86), deco="sun",
         hill=((140, 196, 112), (108, 172, 92))),
    dict(sky=((196, 228, 250), (234, 246, 252)), floor=(170, 124, 86), deco="clouds",
         hill=((150, 206, 140), (118, 186, 118))),
    dict(sky=((253, 238, 214), (247, 220, 188)), floor=(158, 62, 50), deco="frieze"),
    dict(sky=((255, 224, 230), (250, 198, 210)), floor=(184, 116, 108), deco="hearts"),
]
CHORUS_BG = dict(sky=((255, 214, 120), (255, 160, 90)), floor=(146, 76, 48), deco="rays")
FLOOR_Y = 0.79          # vyska podlahy (podil vysky obrazu)

# reakce na slova v textu: (zacatek slova, smycka, kdo: all/f/m, srdicka)
REACTIONS = [
    ("tanečn", "spin", "f", False), ("točí", "spin", "all", False), ("toč", "spin", "all", False),
    ("krúž", "spin", "all", False), ("kruh", "spin", "all", False),
    ("srd", "sway", "all", True), ("lás", "sway", "all", True), ("ľúb", "sway", "all", True),
    ("lúb", "sway", "all", True), ("milu", "sway", "all", True), ("bozk", "sway", "all", True),
    ("pus", "sway", "all", True), ("oč", "point", "all", True),
    ("hej", "jump", "all", False), ("hop", "jump", "all", False), ("juch", "jump", "all", False),
    ("tlesk", "clap", "all", False), ("ruk", "arms_up", "all", False), ("skáč", "hops", "all", False),
    ("skoč", "hops", "all", False), ("noh", "kick", "all", False), ("dup", "heel", "all", False),
    ("tanc", "heel", "all", False), ("tanč", "heel", "all", False),
]
BLEND_BEATS = 0.35

LYRICS = []             # [{t0, t1, text, words, chorus}]
EVENTS = []             # [{start, end, loop, who, hearts, t0, t1}]


# ======================= POMOCNE =======================
def clamp(v, a=0.0, b=1.0):
    return a if v < a else b if v > b else v


def ease(u):
    return u * u * (3 - 2 * u)


def ease_out_back(u):
    if u <= 0:
        return 0.0
    if u >= 1:
        return 1.0
    c = 1.9
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


def lerp_pose(a, b, u):
    return {k: a[k] + (b[k] - a[k]) * u for k in a}


def beat_of(t):
    return (t - OFFSET) * BPM / 60.0


def time_of(beat):
    return beat * 60.0 / BPM + OFFSET


def vec(angle, length):
    a = math.radians(angle)
    return math.sin(a) * length, math.cos(a) * length


def add(p, v):
    return p[0] + v[0], p[1] + v[1]


@functools.lru_cache(maxsize=256)
def font(kind, size):
    for path in (TITLE_FONT if kind == "title" else TEXT_FONT):
        if os.path.exists(path):
            return ImageFont.truetype(path, max(4, int(size)))
    return ImageFont.load_default()


def rnd(*key):
    return random.Random(hash(key) & 0xffffffff).random()


def heart_poly(cx, cy, r, n=28):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        x = 16 * math.sin(a) ** 3
        y = 13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a)
        pts.append((cx + x * r / 16, cy - y * r / 16))
    return pts


# ======================= TEXT PISNE =======================
def parse_lyrics(path):
    """Radek: 'm:ss[.s] [akce] R: text'  ([akce] i 'R:' jsou volitelne, '-' = skryt)."""
    lines = []
    if not path or not os.path.exists(path):
        return lines
    for raw in open(path, encoding="utf-8"):
        raw = raw.strip()
        m = re.match(r"^(\d+):(\d+(?:\.\d+)?)\s*(.*)$", raw)
        if not m or raw.startswith("#"):
            continue
        t = int(m.group(1)) * 60 + float(m.group(2))
        rest = m.group(3).strip()
        action = None
        am = re.match(r"^\[(\w+)\]\s*(.*)$", rest)
        if am:
            action, rest = am.group(1), am.group(2)
        chorus = False
        if rest[:2].upper() == "R:":
            chorus, rest = True, rest[2:].strip()
        lines.append(dict(t0=t, text="" if rest == "-" else rest, action=action, chorus=chorus))
    lines.sort(key=lambda l: l["t0"])
    for i, l in enumerate(lines):
        nxt = lines[i + 1]["t0"] if i + 1 < len(lines) else l["t0"] + 6
        l["t1"] = min(nxt, l["t0"] + 7.0)
        l["words"] = l["text"].split()
    return [l for l in lines if l["text"] or l["action"]]


def build_events(lines):
    events = []
    for l in lines:
        loop, who, hearts = None, "all", False
        if l["action"] in LOOPS:
            loop = l["action"]
        elif l["action"] == "hearts":
            loop, hearts = "sway", True
        else:
            low = l["text"].lower()
            for key, lp, w, h in REACTIONS:
                if re.search(r"\b" + re.escape(key), low):
                    loop, who, hearts = lp, w, h
                    break
        if not loop:
            continue
        start = math.ceil(beat_of(l["t0"]) - 0.3)
        length = LOOPS[loop][0]
        avail = beat_of(l["t1"]) - start
        reps = int(clamp(avail // length, 1, max(1, 8 // length)))
        events.append(dict(start=start, end=start + reps * length, loop=loop, who=who,
                           hearts=hearts, t0=l["t0"], t1=max(l["t1"], time_of(start + reps * length))))
    return events


def is_chorus_section(section):
    if not LYRICS:
        return section % 4 == 3
    a, b = time_of(section * SECTION_BEATS), time_of((section + 1) * SECTION_BEATS)
    mid = (a + b) / 2
    return any(l["chorus"] and l["t0"] <= mid <= l["t1"] + 2 for l in LYRICS)


def chorus_now(t):
    if not LYRICS:
        return is_chorus_section(int(beat_of(t) // SECTION_BEATS))
    return any(l["chorus"] and l["t0"] <= t < l["t1"] for l in LYRICS)


# ======================= CHOREOGRAFIE =======================
def loop_pose(name, beat):
    length, keys = LOOPS[name]
    beat = clamp(beat, 0.0, length)
    pose = dict(keys[-1][1])
    for (b0, p0), (b1, p1) in zip(keys, keys[1:]):
        if beat <= b1:
            u = 0 if b1 == b0 else (beat - b0) / (b1 - b0)
            pose = lerp_pose(p0, p1, ease(u))
            break
    if beat >= length:
        pose["turn"] %= 1.0
    return pose


def program_for(section, shift):
    if is_chorus_section(section):
        return CHORUS_PROGRAMS[(section + shift) % len(CHORUS_PROGRAMS)]
    return PROGRAMS[(section + shift) % len(PROGRAMS)]


def base_pose(beat, shift=0):
    section = math.floor(beat / SECTION_BEATS)
    sb = beat - section * SECTION_BEATS
    prog = program_for(section, shift)
    pos, prev = 0.0, None
    for name, reps in prog:
        length = LOOPS[name][0]
        for _ in range(reps):
            if sb < pos + length:
                local = sb - pos
                pose = loop_pose(name, local)
                if prev and prev != name and local < BLEND_BEATS:
                    pose = lerp_pose(loop_pose(prev, LOOPS[prev][0]), pose, ease(local / BLEND_BEATS))
                return pose, name
            pos += length
            prev = name
    name = prog[-1][0]
    return loop_pose(name, LOOPS[name][0]), name


def figure_pose(beat, role, shift):
    """Poza jedne postavy: zakladni program, prebity reakci na text."""
    pose, name = base_pose(beat, shift)
    for ev in reversed(EVENTS):
        if ev["who"] not in ("all", role):
            continue
        if ev["start"] - 0.0001 <= beat < ev["end"] + BLEND_BEATS:
            L = LOOPS[ev["loop"]][0]
            if beat < ev["end"]:
                local = beat - ev["start"]
                p = loop_pose(ev["loop"], local % L)
                if local < BLEND_BEATS:
                    p = lerp_pose(pose, p, ease(local / BLEND_BEATS))
            else:
                p = lerp_pose(loop_pose(ev["loop"], L), pose, ease((beat - ev["end"]) / BLEND_BEATS))
            return p, ev["loop"]
    return pose, name


def hearts_active(t):
    for ev in EVENTS:
        if ev["hearts"] and ev["t0"] <= t <= ev["t1"]:
            return ev["t0"]
    return None


# ======================= POSTAVY =======================
def seg(d, A, B, col, w):
    w = max(1, int(w))
    d.line([A, B], fill=col, width=w)
    r = w / 2
    for q in (A, B):
        d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=col)


def ochain(d, pts, col, w, o):
    for a, b in zip(pts, pts[1:]):
        seg(d, a, b, INK, w + 2 * o)
    for a, b in zip(pts, pts[1:]):
        seg(d, a, b, col, w)


def circle(d, c, r, fill, outline=None, width=0):
    d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=fill, outline=outline,
              width=int(width) if outline else 0)


def draw_figure(d, pose, prev, role, cx, ground, S, mirror, shadow):
    sgn = -1 if mirror else 1
    keep = ("hop", "turn", "flare")
    p = {k: (v if k in keep else v * sgn) for k, v in pose.items()}
    vx = (pose["x"] - prev["x"]) * sgn / 0.12            # rychlost do strany (jedn./doba)
    dturn = (pose["turn"] - prev["turn"]) % 1.0
    dturn = dturn - 1 if dturn > 0.5 else dturn
    c = math.cos(2 * math.pi * pose["turn"])
    ac = abs(c)
    lw = max(2, LINE_W * S)
    o = lw * 0.35

    # kostra (jednotky, kycle = 0,0)
    kl = vec(p["thl"], THIGH); fl = add(kl, vec(p["shl"], SHIN))
    kr = vec(p["thr"], THIGH); fr = add(kr, vec(p["shr"], SHIN))
    up = vec(180 + p["lean"], 1)
    neck = (up[0] * TORSO, up[1] * TORSO)
    sh = (up[0] * TORSO * 0.86, up[1] * TORSO * 0.86)
    head = add(neck, vec(180 + p["lean"] + p["head"], NECK + HEAD_R))
    shw = 0.05
    shl_ = (sh[0] - shw, sh[1]); shr_ = (sh[0] + shw, sh[1])
    el = add(shl_, vec(p["ual"], UPPER_ARM)); hl = add(el, vec(p["fal"], FOREARM))
    er = add(shr_, vec(p["uar"], UPPER_ARM)); hr = add(er, vec(p["far"], FOREARM))
    hipw = 0.03
    hip_l, hip_r = (-hipw, 0.0), (hipw, 0.0)
    kl = add(hip_l, kl); fl = add(hip_l, fl)
    kr = add(hip_r, kr); fr = add(hip_r, fr)

    lowest = max(fl[1], fr[1])
    hx = cx + p["x"] * S
    hy = ground - (lowest + p["hop"]) * S - lw * 0.8

    def T(pt):
        return hx + pt[0] * c * S, hy + pt[1] * S

    def U(x, y):                     # bez zuzeni otockou (sukne, obliceje)
        return hx + x * S, hy + y * S

    # stin
    sw = 0.17 * S * (1 - 1.6 * pose["hop"])
    d.ellipse([hx - sw, ground - 0.014 * S, hx + sw, ground + 0.014 * S], fill=shadow)

    H = T(head)
    hr_px = HEAD_R * S

    def braid():
        a = clamp(-vx * 28 + dturn * 600 + 6 * math.sin(pose["turn"] * 6.28), -80, 80)
        pts = [(H[0] + 0.02 * S * c, H[1] + 0.05 * S)]
        for i in range(3):
            pts.append(add(pts[-1], vec(a * (0.6 + 0.3 * i), 0.07 * S)))
        ochain(d, pts, HAIR, 0.04 * S, o)
        for q in pts[1:]:
            circle(d, q, 0.024 * S, HAIR)
        e = pts[-1]
        d.polygon([e, (e[0] - 0.035 * S, e[1] + 0.05 * S), (e[0] + 0.035 * S, e[1] + 0.05 * S)],
                  fill=RED, outline=INK, width=int(o))

    if role == "f" and c >= 0:
        braid()

    # nohy
    if role == "f":
        ochain(d, [T(hip_l), T(kl), T(fl)], INK, lw, 0)
        ochain(d, [T(hip_r), T(kr), T(fr)], INK, lw, 0)
        for f in (fl, fr):
            F = T(f)
            d.ellipse([F[0] - 0.04 * S, F[1] - 0.022 * S, F[0] + 0.04 * S, F[1] + 0.018 * S],
                      fill=RED, outline=INK, width=int(o))
    else:
        for hp, k, f in ((hip_l, kl, fl), (hip_r, kr, fr)):
            mid = ((k[0] + f[0]) / 2, (k[1] + f[1]) / 2)
            ochain(d, [T(hp), T(k), T(mid)], WHITE, lw * 1.9, o)
            seg(d, T(mid), T(f), INK, lw * 2.3)
            F = T(f)
            d.ellipse([F[0] - 0.05 * S, F[1] - 0.026 * S, F[0] + 0.05 * S, F[1] + 0.02 * S], fill=INK)

    # sukne
    if role == "f":
        waist = -0.05
        wt = 0.075 * (0.6 + 0.4 * ac)
        hem = 0.20 - 0.04 * pose["flare"]
        hw = 0.18 + 0.09 * pose["flare"] + 0.02 * abs(dturn) * 10
        sx = clamp(-vx * 0.05 - dturn * 0.6, -0.07, 0.07)
        top_off = up[0] * 0.05
        # spodnicka s krajkou
        for i in range(9):
            u = i / 8
            q = U(-hw - 0.01 + sx + u * (2 * hw + 0.02), hem + 0.012)
            circle(d, q, 0.026 * S, WHITE, INK, o)
        TL, TR = U(top_off - wt, waist), U(top_off + wt, waist)
        BL, BR = U(-hw + sx, hem), U(hw + sx, hem)
        d.polygon([TL, TR, BR, BL], fill=RED, outline=INK, width=int(o * 1.6))

        def along(u, side):
            a, b = (TL, BL) if side < 0 else (TR, BR)
            return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u

        d.polygon([along(0.70, -1), along(0.70, 1), along(0.84, 1), along(0.84, -1)], fill=YELLOW)
        for i in range(7):
            u = (i + 0.5) / 7
            a, b = along(0.77, -1), along(0.77, 1)
            circle(d, (a[0] + (b[0] - a[0]) * u, a[1]), 0.012 * S, GREEN)
        for i in (-1, 0, 1):                              # sklady
            a = (TL[0] + (TR[0] - TL[0]) * (0.5 + 0.3 * i), TL[1])
            b = (BL[0] + (BR[0] - BL[0]) * (0.5 + 0.3 * i), BL[1] - 0.06 * S)
            d.line([a, b], fill=DARK_RED, width=int(o * 1.4))
        if c > 0.2:                                       # zasterka
            aw_t, aw_b = 0.045 * c, 0.095 * c
            d.polygon([U(top_off - aw_t, waist + 0.01), U(top_off + aw_t, waist + 0.01),
                       U(sx * 0.6 + aw_b, hem - 0.035), U(sx * 0.6 - aw_b, hem - 0.035)],
                      fill=WHITE, outline=INK, width=int(o))
            fc = U(top_off * 0.5 + sx * 0.3, (waist + hem) / 2)
            for k in range(5):
                a = k * 2 * math.pi / 5
                circle(d, (fc[0] + math.cos(a) * 0.014 * S * c, fc[1] + math.sin(a) * 0.014 * S),
                       0.01 * S, RED)
            circle(d, fc, 0.008 * S, YELLOW)

    # trup
    torso_w = (0.10 if role == "m" else 0.075) * S * (0.55 + 0.45 * ac)
    ochain(d, [T((0, -0.02)), T(neck)], WHITE, torso_w, o)
    vw = (0.06 if role == "m" else 0.05) * (0.45 + 0.55 * ac)
    vtop = (up[0] * TORSO * 0.82, up[1] * TORSO * 0.82)
    vbot = (up[0] * 0.02, up[1] * 0.02)
    if role == "m":
        gap = 0.012 if c > 0 else 0.0
        for s in (-1, 1):
            poly = [U(vtop[0] * c + s * gap, vtop[1]), U(vtop[0] * c + s * vw, vtop[1] + 0.02),
                    U(vbot[0] * c + s * vw, vbot[1]), U(vbot[0] * c + s * gap, vbot[1])]
            d.polygon(poly, fill=VEST, outline=RED, width=int(o))
        d.line([U(-vw - 0.005, -0.01), U(vw + 0.005, -0.01)], fill=RED, width=int(0.03 * S))
        if c > 0.2:
            circle(d, U(0, -0.01), 0.014 * S, YELLOW, INK, o * 0.6)
    else:
        btop = (up[0] * TORSO * 0.62, up[1] * TORSO * 0.62)
        poly = [U(btop[0] * c - vw, btop[1]), U(btop[0] * c + vw, btop[1]),
                U(vbot[0] * c + vw * 0.85, vbot[1] - 0.03), U(vbot[0] * c - vw * 0.85, vbot[1] - 0.03)]
        d.polygon(poly, fill=VEST, outline=INK, width=int(o))
        if c > 0.3:
            for k in range(3):
                y = btop[1] + (vbot[1] - 0.03 - btop[1]) * (k + 0.5) / 3
                x = btop[0] * c
                d.line([U(x - 0.015 * c, y), U(x + 0.015 * c, y + 0.02)], fill=RED, width=int(o * 1.3))
                d.line([U(x + 0.015 * c, y), U(x - 0.015 * c, y + 0.02)], fill=RED, width=int(o * 1.3))

    # ruce
    for s_, e_, h_ in ((shl_, el, hl), (shr_, er, hr)):
        if role == "f":
            ochain(d, [T(s_), T(e_)], WHITE, lw * 1.7, o)
            circle(d, T(s_), 0.046 * S, WHITE, INK, o)
            seg(d, T(e_), T(h_), INK, lw)
        else:
            ochain(d, [T(s_), T(e_), T(h_)], WHITE, lw * 2.1, o)
            seg(d, T(h_), T(h_), RED, lw * 2.3)
        circle(d, T(h_), 0.024 * S, SKIN, INK, o)

    # hlava
    circle(d, H, hr_px, SKIN, INK, lw * 0.6)
    bb = [H[0] - hr_px, H[1] - hr_px, H[0] + hr_px, H[1] + hr_px]
    if c < -0.3:
        circle(d, H, hr_px, HAIR, INK, lw * 0.6)
    elif role == "f":
        d.pieslice(bb, 180, 360, fill=HAIR, outline=INK, width=int(lw * 0.6))
    if c > 0.3:
        fx = 0.006 * c
        for s in (-1, 1):
            ex, ey = H[0] + (s * 0.034 * c + fx) * S, H[1] + 0.004 * S
            d.ellipse([ex - 0.012 * S, ey - 0.016 * S, ex + 0.012 * S, ey + 0.016 * S], fill=INK)
            circle(d, (ex + 0.004 * S, ey - 0.006 * S), 0.004 * S, WHITE)
            circle(d, (H[0] + s * 0.058 * c * S, H[1] + 0.032 * S), 0.015 * S, CHEEK)
        w = 0.034 * c * S
        d.arc([H[0] - w, H[1] + 0.01 * S, H[0] + w, H[1] + 0.058 * S], 20, 160, fill=INK,
              width=int(lw * 0.45))
        if role == "m":
            for s in (-1, 1):
                mx = H[0] + s * 0.018 * c * S
                d.ellipse([mx - 0.02 * c * S, H[1] + 0.024 * S, mx + 0.02 * c * S, H[1] + 0.038 * S],
                          fill=HAIR)
    if role == "f":
        for k in range(5):                                 # venecek
            a = math.radians(-160 + k * 35)
            q = (H[0] + math.cos(a) * hr_px * c, H[1] + math.sin(a) * hr_px * 0.98)
            circle(d, q, 0.024 * S, (RED, YELLOW, WHITE, YELLOW, RED)[k], INK, o * 0.6)
            circle(d, q, 0.007 * S, DARK_RED)
        if c < 0:
            braid()
    else:                                                 # klobouk
        tx, ty = H[0], H[1] - hr_px * 0.7
        cw, bw = 0.075 * S * (0.6 + 0.4 * ac), 0.15 * S * (0.6 + 0.4 * ac)
        d.rectangle([tx - cw, ty - 0.07 * S, tx + cw, ty], fill=INK)
        d.rectangle([tx - cw, ty - 0.022 * S, tx + cw, ty - 0.006 * S], fill=RED)
        seg(d, (tx - bw, ty), (tx + bw, ty), INK, 0.026 * S)
        fs = 1 if c >= 0 else -1
        fxb = tx + fs * cw * 0.7
        d.polygon([(fxb, ty - 0.04 * S), (fxb + fs * 0.05 * S, ty - 0.15 * S),
                   (fxb + fs * 0.012 * S, ty - 0.05 * S)], fill=GREEN, outline=INK, width=int(o * 0.6))


# ======================= POZADI =======================
_BG_CACHE = {}


def background(key, W, H):
    if key in _BG_CACHE:
        return _BG_CACHE[key]
    bg = CHORUS_BG if key == "chorus" else BACKGROUNDS[key]
    floor_y = int(H * FLOOR_Y)
    top, bot = bg["sky"]
    col = Image.new("RGB", (1, H))
    col.putdata([tuple(int(top[i] + (bot[i] - top[i]) * y / floor_y) for i in range(3))
                 if y < floor_y else bot for y in range(H)])
    img = col.resize((W, H))
    d = ImageDraw.Draw(img)
    deco = bg["deco"]
    if bg.get("hill"):
        h1, h2 = bg["hill"]
        for x0, w, h, cl in ((-0.15, 0.6, 0.28, h1), (0.55, 0.65, 0.32, h1), (0.2, 0.7, 0.22, h2)):
            d.ellipse([W * x0, floor_y - H * h, W * (x0 + w), floor_y + H * h], fill=cl)
    if deco == "clouds":
        for cx, cy, s in ((0.18, 0.18, 1.0), (0.62, 0.12, 0.8), (0.86, 0.26, 0.9)):
            for dx, dy, r in ((-0.05, 0.01, 0.045), (0, -0.015, 0.06), (0.05, 0.01, 0.045)):
                d.ellipse([W * (cx + dx * s) - H * r * s, H * (cy + dy * s) - H * r * s,
                           W * (cx + dx * s) + H * r * s, H * (cy + dy * s) + H * r * s], fill=WHITE)
    if deco in ("frieze", "hearts"):
        band = H * 0.11
        d.rectangle([0, 0, W, band], fill=RED if deco == "frieze" else (222, 86, 120))
        n = 16
        for i in range(n):
            x = W * (i + 0.5) / n
            if deco == "hearts" or i % 2:
                d.polygon(heart_poly(x, band * 0.52, band * 0.28), fill=WHITE)
            else:
                r = band * 0.3
                d.polygon([(x, band / 2 - r), (x + r, band / 2), (x, band / 2 + r), (x - r, band / 2)],
                          fill=YELLOW)
        d.line([(0, band), (W, band)], fill=INK, width=max(2, H // 200))
        for i in range(8):                                   # tulipany na stene
            x = W * (i + 0.5) / 8
            y = floor_y - H * 0.08
            d.line([(x, y), (x, y - H * 0.08)], fill=GREEN, width=max(2, H // 220))
            d.ellipse([x - H * 0.02, y - H * 0.11, x + H * 0.02, y - H * 0.07],
                      fill=RED if deco == "frieze" else (222, 86, 120))
    # podlaha (prkna)
    fl = bg["floor"]
    d.rectangle([0, floor_y, W, H], fill=fl)
    dark = tuple(int(v * 0.82) for v in fl)
    y, step = floor_y, H * 0.018
    while y < H:
        d.line([(0, y), (W, y)], fill=dark, width=max(1, H // 360))
        step *= 1.35
        y += step
    for i in range(-10, 11):
        x0 = W / 2 + i * W * 0.03
        d.line([(x0, floor_y), (W / 2 + i * W * 0.12, H)], fill=dark, width=max(1, H // 400))
    d.line([(0, floor_y), (W, floor_y)], fill=INK, width=max(2, H // 200))
    floor_strip = img.crop((0, floor_y, W, H))
    _BG_CACHE[key] = (img, floor_strip, floor_y, tuple(int(v * 0.7) for v in fl), deco)
    return _BG_CACHE[key]


def draw_sky_dynamic(img, d, deco, beat, W, H, floor_y, strip):
    if deco == "sun":
        cx, cy, r = W * 0.83, H * 0.2, H * 0.08
        rot = beat * 6
        for k in range(12):
            a = math.radians(rot + k * 30)
            b = math.radians(rot + k * 30 + 9)
            L = r * (1.9 + 0.15 * math.sin(beat * math.pi))
            d.polygon([(cx + math.cos(a) * r * 1.1, cy + math.sin(a) * r * 1.1),
                       (cx + math.cos((a + b) / 2) * L, cy + math.sin((a + b) / 2) * L),
                       (cx + math.cos(b) * r * 1.1, cy + math.sin(b) * r * 1.1)], fill=(255, 190, 70))
        circle(d, (cx, cy), r, YELLOW, (240, 160, 40), H * 0.006)
    elif deco == "rays":
        cx, cy = W / 2, H * 0.42
        rot = beat * 5
        for k in range(18):
            a = math.radians(rot + k * 20)
            b = math.radians(rot + k * 20 + 10)
            L = W
            d.polygon([(cx, cy), (cx + math.cos(a) * L, cy + math.sin(a) * L),
                       (cx + math.cos(b) * L, cy + math.sin(b) * L)], fill=(255, 226, 150))
        img.paste(strip, (0, floor_y))
    # pulzujici svetlo na podlaze do rytmu
    pulse = math.exp(-3 * (beat % 1.0)) if beat >= 0 else 0
    rw, rh = W * (0.34 + 0.03 * pulse), H * (0.05 + 0.006 * pulse)
    gy = floor_y + H * 0.06
    d.ellipse([W / 2 - rw, gy - rh, W / 2 + rw, gy + rh], fill=(255, 238, 200) if deco != "rays" else (255, 220, 140))


# ======================= EFEKTY =======================
CONFETTI_COLS = [RED, YELLOW, GREEN, WHITE, (60, 120, 220), (230, 90, 160)]


def draw_confetti(d, t, W, H):
    for i in range(70):
        sp = 0.12 + 0.2 * rnd(i, 1)
        y = ((t * sp + rnd(i, 2)) % 1.15 - 0.08) * H
        x = (rnd(i, 3) * W + math.sin(t * 2 + i) * H * 0.03) % W
        a = t * (3 + 4 * rnd(i, 4)) + i
        w, h = H * 0.012, H * 0.006 * (0.3 + abs(math.cos(a * 1.3)))
        ca, sa = math.cos(a), math.sin(a)
        pts = [(x + ca * dx - sa * dy, y + sa * dx + ca * dy)
               for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h))]
        d.polygon(pts, fill=CONFETTI_COLS[i % len(CONFETTI_COLS)])


def draw_hearts(d, t, t0, xs, top_y, H):
    for k in range(int((t - t0) * 4) + 1):
        ts = t0 + k * 0.25
        age = t - ts
        if not 0 <= age < 1.8:
            continue
        x0 = xs[k % len(xs)] + (rnd(k, 7) - 0.5) * H * 0.16
        x = x0 + math.sin(age * 4 + k) * H * 0.02
        y = top_y - age * H * 0.16
        r = H * 0.028 * ease_out_back(clamp(age / 0.3)) * (1 - clamp((age - 1.4) / 0.4))
        if r > 1:
            d.polygon(heart_poly(x, y, r), fill=RED if k % 3 else (236, 90, 140), outline=INK,
                      width=max(1, int(H * 0.003)))


# ======================= TITULKY =======================
def text(d, xy, s, f, fill, sw, anchor="mm", shadow=None):
    if shadow:
        off = max(2, sw)
        d.text((xy[0] + off, xy[1] + off * 1.2), s, font=f, fill=shadow, anchor=anchor,
               stroke_width=sw, stroke_fill=shadow)
    d.text(xy, s, font=f, fill=fill, anchor=anchor, stroke_width=sw, stroke_fill=INK)


def draw_banner(d, t_rel, yc, W, H, title, sub, exit_u=0.0):
    size = H * 0.115 * (1 - 0.3 * exit_u)
    yc = yc - exit_u * H * 0.35
    f = font("title", size)
    widths = [f.getlength(ch) for ch in title]
    total = sum(widths)
    sb = ease_out_back(clamp(t_rel / 0.35))
    bw, bh = (total + H * 0.1) * sb, size * 1.45 * sb
    if bw > 2:
        d.rounded_rectangle([W / 2 - bw / 2 + H * 0.01, yc - bh / 2 + H * 0.012,
                             W / 2 + bw / 2 + H * 0.01, yc + bh / 2 + H * 0.012], radius=bh * 0.3,
                            fill=(90, 14, 20))
        d.rounded_rectangle([W / 2 - bw / 2, yc - bh / 2, W / 2 + bw / 2, yc + bh / 2], radius=bh * 0.3,
                            fill=RED, outline=WHITE, width=int(H * 0.007))
        for s in (-1, 1):
            hx = W / 2 + s * (bw / 2 - H * 0.025)
            d.polygon(heart_poly(hx, yc, H * 0.016 * sb), fill=YELLOW)
    x = W / 2 - total / 2
    for i, ch in enumerate(title):
        ti = 0.15 + i * 0.045
        s = ease_out_back(clamp((t_rel - ti) / 0.3))
        if s > 0.05 and ch != " ":
            fs = size * s
            wob = math.sin(t_rel * 6 + i * 0.7) * H * 0.006
            text(d, (x + widths[i] / 2, yc + wob), ch, font("title", fs), WHITE,
                 max(1, int(fs * 0.07)), shadow=(90, 14, 20))
        x += widths[i]
    if sub:
        s = ease_out_back(clamp((t_rel - 0.25 - len(title) * 0.045) / 0.35))
        if s > 0.05:
            fs = H * 0.05 * s * (1 - 0.3 * exit_u)
            text(d, (W / 2, yc + bh / 2 + H * 0.05), sub, font("text", fs), YELLOW,
                 max(1, int(fs * 0.12)), shadow=(60, 20, 10))


def draw_badge(img, t, W, H):
    if not SHOW_BADGE or t < INTRO_SECONDS or t > DURATION - OUTRO_SECONDS:
        return
    u = ease_out_back(clamp((t - INTRO_SECONDS) / 0.5))
    f = font("title", H * 0.036)
    tw = f.getlength(TITLE)
    pad = H * 0.018
    w, h = int(tw + 2 * pad + H * 0.04), int(H * 0.062)
    box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(box)
    bd.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=(206, 36, 46, 225),
                         outline=(255, 255, 255, 255), width=max(2, int(H * 0.004)))
    bd.polygon(heart_poly(pad + H * 0.012, h / 2, H * 0.013), fill=YELLOW)
    bd.text((pad + H * 0.035, h / 2), TITLE, font=f, fill=WHITE, anchor="lm")
    x = int(H * 0.03 - (1 - u) * (w + H * 0.05))
    img.paste(box, (x, int(H * 0.025)), box)


def current_line(t):
    for i in range(len(LYRICS) - 1, -1, -1):
        l = LYRICS[i]
        if l["t0"] <= t:
            return l if t < l["t1"] and l["text"] else None
    return None


def draw_lyrics(img, d, t, W, H):
    l = current_line(t)
    if not l:
        return
    age = t - l["t0"]
    dur = l["t1"] - l["t0"]
    pop = ease_out_back(clamp(age / 0.25))
    outro = clamp((l["t1"] - t) / 0.15)
    s = pop * outro
    if s < 0.05:
        return
    words = l["words"]
    base = H * (0.068 if l["chorus"] else 0.058)
    f = font("text", base)
    space = f.getlength(" ")
    widths = [f.getlength(w) for w in words]
    total = sum(widths) + space * (len(words) - 1)
    if total > W * 0.86:
        base *= W * 0.86 / total
        f = font("text", base)
        space = f.getlength(" ")
        widths = [f.getlength(w) for w in words]
        total = sum(widths) + space * (len(words) - 1)
    yc = H * 0.905
    bw, bh = (total + H * 0.07) * s, base * 1.55 * s
    box = Image.new("RGBA", (int(bw) + 2, int(bh) + 2), (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle(
        [0, 0, int(bw), int(bh)], radius=int(bh * 0.45),
        fill=(150, 20, 30, 190) if l["chorus"] else (25, 18, 22, 165))
    img.paste(box, (int(W / 2 - bw / 2), int(yc - bh / 2)), box)
    # casovani slov (karaoke)
    weights = [len(w) + 1 for w in words]
    span = dur * 0.85
    acc, starts = 0.0, []
    for w_ in weights:
        starts.append(l["t0"] + span * acc / sum(weights))
        acc += w_
    x = W / 2 - total * s / 2
    for i, w in enumerate(words):
        wa = t - starts[i]
        bump = 1 + 0.2 * math.exp(-wa * 9) if wa >= 0 else 1.0
        col = (YELLOW if not l["chorus"] else (255, 236, 110)) if wa >= 0 else WHITE
        fs = base * s * bump
        cxw = x + widths[i] * s / 2
        text(d, (cxw, yc - (bump - 1) * H * 0.04), w, font("text", fs), col, max(1, int(fs * 0.09)))
        x += (widths[i] + space) * s


def draw_titles(img, d, t, W, H):
    if t < INTRO_SECONDS:
        exit_u = ease(clamp((t - (INTRO_SECONDS - 0.5)) / 0.5))
        draw_banner(d, t, H * 0.17, W, H, TITLE, TITLE_SUB, exit_u)
    if t > DURATION - OUTRO_SECONDS:
        tr = t - (DURATION - OUTRO_SECONDS)
        draw_banner(d, tr, H * 0.17, W, H, TITLE, None)
        s = ease_out_back(clamp((tr - 1.0) / 0.4))
        if s > 0.05:
            fs = H * 0.06 * s * (1 + 0.04 * math.sin(tr * 8))
            f = font("text", fs)
            tw = f.getlength(OUTRO_TEXT)
            text(d, (W / 2 - H * 0.03, H * 0.33), OUTRO_TEXT, f, YELLOW, max(1, int(fs * 0.1)),
                 shadow=(60, 20, 10))
            d.polygon(heart_poly(W / 2 + tw / 2 + H * 0.025, H * 0.33, fs * 0.45), fill=RED,
                      outline=INK, width=max(2, int(fs * 0.06)))
    draw_badge(img, t, W, H)
    draw_lyrics(img, d, t, W, H)


# ======================= SCENA =======================
def layout(section, sb, chorus):
    cfg = CHORUS_CONFIG if chorus else CONFIGS[section % len(CONFIGS)]
    figs = []
    n = len(cfg["figs"])
    for k, (role, fx, sc, mirror, gup, shift) in enumerate(cfg["figs"]):
        z = 0.0
        if cfg["travel"] == "drift":
            fx += 0.1 * math.sin(2 * math.pi * sb / SECTION_BEATS + k * math.pi)
        elif cfg["travel"] == "swap" and n == 2:
            other = cfg["figs"][1 - k][1]
            u = ease(clamp((sb - 6) / 4))
            fx += (other - fx) * u
            z = (1 if k == 0 else -1) * math.sin(math.pi * u)
        elif cfg["travel"] == "circle" and gup == 0:
            idx = [i for i, f in enumerate(cfg["figs"]) if f[4] == 0].index(k)
            a = 2 * math.pi * sb / SECTION_BEATS + idx * math.pi
            fx = 0.5 + 0.2 * math.cos(a)
            z = math.sin(a)
        figs.append(dict(role=role, fx=fx, scale=sc * (1 + 0.07 * z), mirror=mirror,
                         gup=gup - 0.03 * z, shift=shift, z=z - gup * 10))
    figs.sort(key=lambda f: f["z"])
    return figs


def render_frame(t, W=None, H=None):
    W, H = W or WIDTH, H or HEIGHT
    Ws, Hs = W * SUPERSAMPLE, H * SUPERSAMPLE
    beat = beat_of(t)
    section = math.floor(beat / SECTION_BEATS)
    sb = beat - section * SECTION_BEATS
    chorus = is_chorus_section(section)
    key = "chorus" if chorus else section % len(BACKGROUNDS)
    bg, strip, floor_y, shadow, deco = background(key, Ws, Hs)
    img = bg.copy()
    d = ImageDraw.Draw(img)
    draw_sky_dynamic(img, d, deco, beat, Ws, Hs, floor_y, strip)

    zoom = 1 + 0.035 * math.exp(-3 * (beat % 4)) if beat >= 0 else 1
    S0 = Hs * 0.46
    xs = []
    for f in layout(section, sb, chorus):
        pose, _ = figure_pose(beat, f["role"], f["shift"])
        prev, _ = figure_pose(beat - 0.12, f["role"], f["shift"])
        S = S0 * f["scale"] * zoom
        cx = Ws / 2 + (Ws * f["fx"] - Ws / 2) * zoom
        ground = floor_y + Hs * 0.035 - Hs * f["gup"]
        draw_figure(d, pose, prev, f["role"], cx, ground, S, f["mirror"], shadow)
        if f["gup"] <= 0.04:
            xs.append(cx)

    t0 = hearts_active(t)
    _, cur = figure_pose(beat, "f", 0)
    if t0 is None and cur in HEART_LOOPS:
        t0 = time_of(math.floor(beat / 4) * 4)
    if t0 is not None and xs:
        draw_hearts(d, t, t0, xs, floor_y - Hs * 0.5, Hs)
    if chorus_now(t):
        draw_confetti(d, t, Ws, Hs)
    draw_titles(img, d, t, Ws, Hs)
    if SUPERSAMPLE > 1:
        img = img.resize((W, H), Image.LANCZOS)
    return img


def _frame_bytes(args):
    t, W, H = args
    return render_frame(t, W, H).tobytes()


def encode(path, W, H, seconds, start=0.0):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = int(round(seconds * FPS))
    jobs = [(start + i / FPS, W, H) for i in range(n)]
    with Pool(WORKERS) as pool:
        for i, data in enumerate(pool.imap(_frame_bytes, jobs, chunksize=4)):
            proc.stdin.write(data)
            if i % (FPS * 10) == 0:
                print(f"  {i / FPS:6.1f} s / {seconds} s", flush=True)
    proc.stdin.close()
    proc.wait()


def main():
    global LYRICS, EVENTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="5 s nahled + contact sheet")
    ap.add_argument("--start", type=float, default=0.0, help="zacatek nahledu (s)")
    ap.add_argument("--lyrics", default=LYRICS_FILE, help="soubor s textem a casy")
    args = ap.parse_args()
    LYRICS = parse_lyrics(args.lyrics)
    EVENTS = build_events(LYRICS)
    print(f"text: {len(LYRICS)} radku, reakci: {len(EVENTS)}")
    if args.preview:
        W, H, secs = 640, 360, 5
        encode("output/preview.mp4", W, H, secs, args.start)
        tw, th = 320, 180
        sheet = Image.new("RGB", (tw * 8, th), "white")
        for i in range(8):
            f = render_frame(args.start + i * secs / 8, W, H)
            sheet.paste(f.resize((tw, th), Image.LANCZOS), (i * tw, 0))
        sheet.save("output/contact_sheet.png")
        print("output/preview.mp4, output/contact_sheet.png")
    else:
        encode(OUTPUT, WIDTH, HEIGHT, DURATION)
        print(OUTPUT)


if __name__ == "__main__":
    main()
