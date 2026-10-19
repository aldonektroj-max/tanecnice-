#!/usr/bin/env python3
"""Stickman tanec k pisni "Tanecnice z Lucnice" (bez zvuku).

Pouziti:
  python3 tanecnice.py            # finalni video -> output/tanecnice.mp4
  python3 tanecnice.py --preview  # 5 s nahled v nizkem rozliseni + contact sheet
"""
import argparse
import math
import os
import subprocess

from PIL import Image, ImageDraw

# ---------------- PARAMETRY ----------------
BPM = 120
DURATION = 180          # sekundy
WIDTH, HEIGHT = 1280, 720
FPS = 24
SUPERSAMPLE = 2         # vyhlazeni hran (kresli se ve vyssim rozliseni)
SECTION_BEATS = 16      # po kolika dobach (4 takty) se meni pozadi / postavy
OUTPUT = "output/tanecnice.mp4"

INK = (20, 20, 20)
SKIRT = (200, 40, 45)
LINE_W = 0.035          # tloustka car (v jednotkach vysky postavy)

# delky kosti (jednotka = vyska postavy)
TORSO, NECK, HEAD_R = 0.30, 0.02, 0.07
UPPER_ARM, FOREARM = 0.15, 0.14
THIGH, SHIN = 0.22, 0.22

# ---------------- POZY ----------------
# Uhly ve stupnich, absolutni v obraze: 0 = dolu, +90 = doprava, 180 = nahoru.
BASE = dict(x=0.0, hop=0.0, lean=0.0, head=0.0, turn=0.0, flare=0.0,
            ual=-15, fal=-10, uar=15, far=10,
            thl=-6, shl=-4, thr=6, shr=4)


def P(**kw):
    return {**BASE, **kw}


HIPS = dict(ual=-50, fal=30, uar=50, far=-30)            # ruce v bok
OUT = dict(ual=-80, fal=-88, uar=80, far=88)             # ruce do stran
UP = dict(ual=-155, fal=-165, uar=155, far=165)          # ruce nahoru
BENT = dict(thl=-14, shl=4, thr=14, shr=-4)              # pokrcena kolena
STRAIGHT = dict(thl=-4, shl=-3, thr=4, shr=3)
CROUCH = dict(thl=-25, shl=10, thr=25, shr=-10)

# smycky: (delka v dobach, [(doba, poza), ...]) - prvni a posledni poza navazuji
LOOPS = {
    "side": (4, [
        (0.0, P(**HIPS, **BENT)),
        (0.5, P(**HIPS, x=-0.12, lean=-5, thl=-30, shl=-25, thr=5, shr=5)),
        (1.0, P(**HIPS, **BENT, x=-0.24)),
        (1.5, P(**HIPS, x=-0.12, lean=5, thl=-5, shl=-5, thr=30, shr=25)),
        (2.0, P(**HIPS, **BENT)),
        (2.5, P(**HIPS, x=0.12, lean=5, thl=-5, shl=-5, thr=30, shr=25)),
        (3.0, P(**HIPS, **BENT, x=0.24)),
        (3.5, P(**HIPS, x=0.12, lean=-5, thl=-30, shl=-25, thr=5, shr=5)),
        (4.0, P(**HIPS, **BENT)),
    ]),
    "spin": (4, [
        (0.0, P(**OUT, **BENT)),
        (0.5, P(**OUT, **STRAIGHT)),
        (1.5, P(**OUT, **STRAIGHT, turn=0.4, flare=1)),
        (2.5, P(ual=-120, fal=-140, uar=120, far=140, **STRAIGHT, turn=0.8, flare=1)),
        (3.0, P(**UP, **STRAIGHT, turn=1.0, flare=0.3)),
        (4.0, P(**OUT, **BENT, turn=1.0)),
    ]),
    "arms_up": (2, [
        (0.0, P(ual=-30, fal=-20, uar=30, far=20, **BENT)),
        (1.0, P(**UP, **STRAIGHT, hop=0.03, head=8)),
        (2.0, P(ual=-30, fal=-20, uar=30, far=20, **BENT)),
    ]),
    "hops": (2, [
        (0.0, P(**OUT, **CROUCH)),
        (0.5, P(ual=-110, fal=-150, uar=60, far=40, hop=0.12, lean=-4,
                thl=-55, shl=-20, thr=5, shr=5)),
        (1.0, P(**OUT, **CROUCH)),
        (1.5, P(ual=-60, fal=-40, uar=110, far=150, hop=0.12, lean=4,
                thl=-5, shl=-5, thr=55, shr=20)),
        (2.0, P(**OUT, **CROUCH)),
    ]),
}

# programy sekci (kazda = SECTION_BEATS dob): (smycka, pocet opakovani)
PROGRAMS = [
    [("side", 2), ("arms_up", 4)],
    [("hops", 4), ("spin", 2)],
    [("side", 1), ("spin", 1), ("hops", 4)],
    [("arms_up", 2), ("hops", 2), ("spin", 2)],
]

# rozestaveni: (role f/m, stred x 0..1, meritko, zrcadlit, posun zeme nahoru 0..1)
CONFIGS = [
    [("f", 0.50, 1.00, False, 0.00)],
    [("f", 0.36, 1.00, False, 0.00), ("m", 0.64, 1.08, True, 0.00)],
    [("f", 0.30, 1.00, False, 0.00)],
    [("m", 0.34, 1.08, False, 0.00), ("f", 0.66, 1.00, True, 0.00)],
    [("m", 0.20, 0.78, True, 0.07), ("m", 0.80, 0.78, False, 0.07),
     ("f", 0.50, 1.00, False, 0.00)],
    [("f", 0.70, 1.00, True, 0.00)],
]

# pozadi: (barva pozadi, barva podlahy / dekorace, typ dekorace)
BACKGROUNDS = [
    ((246, 240, 226), (222, 210, 186), "sun"),
    ((226, 238, 246), (196, 214, 228), "hills"),
    ((236, 244, 228), (204, 222, 192), "dots"),
    ((248, 232, 232), (230, 204, 204), "stripes"),
]

BLEND_BEATS = 0.35      # plynuly prechod mezi smyckami


def ease(u):
    return u * u * (3 - 2 * u)


def lerp_pose(a, b, u):
    return {k: a[k] + (b[k] - a[k]) * u for k in a}


def loop_pose(name, beat):
    length, keys = LOOPS[name]
    beat = min(max(beat, 0.0), length)
    for (b0, p0), (b1, p1) in zip(keys, keys[1:]):
        if beat <= b1:
            u = 0 if b1 == b0 else (beat - b0) / (b1 - b0)
            pose = lerp_pose(p0, p1, ease(u))
            break
    if beat >= length:
        pose["turn"] %= 1.0
    return pose


def pose_at(beat):
    """Vrati (poza, index sekce) pro danou dobu."""
    section = int(beat // SECTION_BEATS)
    sb = beat - section * SECTION_BEATS
    prog = PROGRAMS[section % len(PROGRAMS)]
    pos, prev = 0.0, None
    for name, reps in prog:
        length = LOOPS[name][0]
        for _ in range(reps):
            if sb < pos + length:
                local = sb - pos
                pose = loop_pose(name, local)
                if prev and prev != name and local < BLEND_BEATS:
                    end = loop_pose(prev, LOOPS[prev][0])
                    pose = lerp_pose(end, pose, ease(local / BLEND_BEATS))
                return pose, section
            pos += length
            prev = name
    return loop_pose(prog[-1][0], LOOPS[prog[-1][0]][0]), section


def vec(angle, length):
    a = math.radians(angle)
    return math.sin(a) * length, math.cos(a) * length


def add(p, v):
    return p[0] + v[0], p[1] + v[1]


def draw_figure(d, pose, role, cx, ground, S, mirror):
    sgn = -1 if mirror else 1
    p = {k: (v * sgn if k not in ("hop", "turn", "flare") else v)
         for k, v in pose.items()}
    c = math.cos(2 * math.pi * pose["turn"])
    lw = max(2, int(LINE_W * S))

    hip = (0.0, 0.0)
    knee_l = add(hip, vec(p["thl"], THIGH)); foot_l = add(knee_l, vec(p["shl"], SHIN))
    knee_r = add(hip, vec(p["thr"], THIGH)); foot_r = add(knee_r, vec(p["shr"], SHIN))
    up = vec(180 + p["lean"], 1)
    neck = (up[0] * TORSO, up[1] * TORSO)
    shoulder = (up[0] * TORSO * 0.9, up[1] * TORSO * 0.9)
    hd = vec(180 + p["lean"] + p["head"], NECK + HEAD_R)
    head = add(neck, hd)
    elb_l = add(shoulder, vec(p["ual"], UPPER_ARM)); hand_l = add(elb_l, vec(p["fal"], FOREARM))
    elb_r = add(shoulder, vec(p["uar"], UPPER_ARM)); hand_r = add(elb_r, vec(p["far"], FOREARM))

    lowest = max(foot_l[1], foot_r[1])
    hx = cx + p["x"] * S
    hy = ground - (lowest + p["hop"]) * S - lw / 2

    def T(pt):
        return hx + pt[0] * c * S, hy + pt[1] * S

    # stin
    sw = 0.16 * S * (1 - 1.5 * pose["hop"])
    d.ellipse([hx - sw, ground - 0.012 * S, hx + sw, ground + 0.012 * S], fill=shadow_color)

    def bone(a, b):
        A, B = T(a), T(b)
        d.line([A, B], fill=INK, width=lw)
        for q in (A, B):
            d.ellipse([q[0] - lw / 2, q[1] - lw / 2, q[0] + lw / 2, q[1] + lw / 2], fill=INK)

    for a, b in ((hip, knee_l), (knee_l, foot_l), (hip, knee_r), (knee_r, foot_r)):
        bone(a, b)

    if role == "f":   # sukne: jednoduchy trojuhelnik
        top = (up[0] * 0.10, up[1] * 0.10)
        half = 0.14 + 0.07 * pose["flare"]
        drop = 0.17 - 0.03 * pose["flare"]
        tri = [(hx + top[0] * c * S, hy + top[1] * S),
               (hx - half * S, hy + drop * S), (hx + half * S, hy + drop * S)]
        d.polygon(tri, fill=SKIRT, outline=INK, width=max(2, lw // 2))

    for a, b in ((hip, neck), (shoulder, elb_l), (elb_l, hand_l),
                 (shoulder, elb_r), (elb_r, hand_r)):
        bone(a, b)

    H = T(head); r = HEAD_R * S
    d.ellipse([H[0] - r, H[1] - r, H[0] + r, H[1] + r], outline=INK, width=lw, fill=bg_color)
    hdir = vec(180 + p["lean"] + p["head"], 1)
    if role == "f":   # drdol
        bun = add(head, (hdir[0] * HEAD_R * 1.15, hdir[1] * HEAD_R * 1.15))
        B = T(bun); br = 0.035 * S
        d.ellipse([B[0] - br, B[1] - br, B[0] + br, B[1] + br], fill=INK)
    else:             # klobouk
        top = add(head, (hdir[0] * HEAD_R * 0.9, hdir[1] * HEAD_R * 0.9))
        Tp = T(top); w = 0.075 * S
        d.rectangle([Tp[0] - w * 0.65, Tp[1] - 0.055 * S, Tp[0] + w * 0.65, Tp[1]], fill=INK)
        d.line([(Tp[0] - w * 1.2, Tp[1]), (Tp[0] + w * 1.2, Tp[1])], fill=INK, width=lw)


bg_color = shadow_color = None


def draw_background(d, W, H, idx, floor_y):
    global bg_color, shadow_color
    bg, accent, kind = BACKGROUNDS[idx % len(BACKGROUNDS)]
    bg_color, shadow_color = bg, accent
    d.rectangle([0, 0, W, H], fill=bg)
    if kind == "sun":
        r = H * 0.16
        d.ellipse([W * 0.8 - r, H * 0.22 - r, W * 0.8 + r, H * 0.22 + r], fill=accent)
    elif kind == "hills":
        for i, (x0, w, h) in enumerate(((-0.1, 0.6, 0.30), (0.35, 0.7, 0.22), (0.75, 0.5, 0.34))):
            d.ellipse([W * x0, floor_y - H * h, W * (x0 + w), floor_y + H * h], fill=accent)
    elif kind == "dots":
        r = H * 0.012
        for i in range(12):
            x = W * (i + 0.5) / 12
            for y in (H * 0.12, H * 0.20):
                d.ellipse([x - r, y - r, x + r, y + r], fill=accent)
    elif kind == "stripes":
        for i in range(0, 14, 2):
            d.rectangle([W * i / 14, 0, W * (i + 1) / 14, floor_y], fill=accent)
    d.rectangle([0, floor_y, W, H], fill=accent if kind != "stripes" else bg)
    d.line([(0, floor_y), (W, floor_y)], fill=INK, width=max(2, H // 240))


def render_frame(t, W, H):
    beat = t * BPM / 60.0
    pose, section = pose_at(beat)
    img = Image.new("RGB", (W * SUPERSAMPLE, H * SUPERSAMPLE))
    Ws, Hs = img.size
    d = ImageDraw.Draw(img)
    floor_y = Hs * 0.86
    draw_background(d, Ws, Hs, section, floor_y)
    S = Hs * 0.52
    for role, fx, sc, mirror, gup in CONFIGS[section % len(CONFIGS)]:
        draw_figure(d, pose, role, Ws * fx, floor_y + Hs * 0.03 - Hs * gup, S * sc, mirror)
    if SUPERSAMPLE > 1:
        img = img.resize((W, H), Image.LANCZOS)
    return img


def encode(path, W, H, seconds, start=0.0, keep=None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = int(round(seconds * FPS))
    kept = []
    for i in range(n):
        img = render_frame(start + i / FPS, W, H)
        proc.stdin.write(img.tobytes())
        if keep and i in keep:
            kept.append(img)
        if i % (FPS * 10) == 0:
            print(f"  {i / FPS:6.1f} s / {seconds} s", flush=True)
    proc.stdin.close()
    proc.wait()
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="5 s nahled + contact sheet")
    ap.add_argument("--start", type=float, default=0.0, help="zacatek nahledu (s)")
    args = ap.parse_args()
    if args.preview:
        W, H, secs = 640, 360, 5
        n = secs * FPS
        idx = [int(i * (n - 1) / 7) for i in range(8)]
        frames = encode("output/preview.mp4", W, H, secs, args.start, set(idx))
        tw, th = 320, 180
        sheet = Image.new("RGB", (tw * 8, th), "white")
        for i, f in enumerate(frames):
            sheet.paste(f.resize((tw, th), Image.LANCZOS), (i * tw, 0))
        sheet.save("output/contact_sheet.png")
        print("output/preview.mp4, output/contact_sheet.png")
    else:
        encode(OUTPUT, WIDTH, HEIGHT, DURATION)
        print(OUTPUT)


if __name__ == "__main__":
    main()
