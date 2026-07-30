#!/usr/bin/env python3
"""
pregenerate_cache.py  —  run this ONCE while you have internet.

It calls OpenAI's gpt-image-1 the same way App_v12.py does and saves a batch
of real rangoli PNGs into ai_cache/rangolis/. At the competition (no internet),
App_v12.py automatically serves random images from that folder, so the
"AI Generated" and "Picture to Rangoli" buttons look and behave exactly as
they do online.

Usage:
    # key from openai_key.txt (same file the app uses), 12 images:
    python3 pregenerate_cache.py

    # or pass a key and a count:
    python3 pregenerate_cache.py --key sk-xxxx --count 20

You only need to run this on your own machine before the event. The generated
folder travels with the app.
"""

import argparse
import base64
import json
import os
import random
import sys
import time
import urllib.request
import urllib.error

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
AI_KEY_FILE = os.path.join(_APP_DIR, "openai_key.txt")
CACHED_RANGOLI_DIR = os.path.join(_APP_DIR, "ai_cache", "rangolis")

# The same four base prompts App_v12.py rotates through, plus a few themed ones
# so the cached variety matches what a judge would see live.
BASE_PROMPTS = [
    "Generate a completely original rangoli in the style of a classic "
    "lotus/sunflower mandala: a small centre circle surrounded by ONE ring of "
    "6-9 evenly spaced petals. Optionally add a ring of small pointed accents "
    "or dots just outside the petal tips. No second petal layer, no outer "
    "border. Thin, clean single-stroke black outlines with clear gaps between "
    "each petal. Preserve perfect radial symmetry. Black outlines only on a "
    "white background. No fills, colors, shading, or 3D.",

    "Design a unique geometric rangoli using ONE simple shape repeated "
    "symmetrically (petals, a star, a hexagon, or diamonds) - 6-9 repetitions "
    "in a single ring around a small centre circle. Optionally a ring of small "
    "accent points just outside. Thin clean outlines with clear spacing. "
    "Radial symmetry. White background.",

    "Create an original Indian rangoli built around ONE traditional lotus "
    "motif: a centre circle with 6-9 petals in a single ring, optionally a "
    "thin outer ring of small dots. Thin, well-separated black line art, "
    "single stroke weight. Strong radial symmetry. White background.",
]

THEMES = ["Diwali diyas", "peacock feathers", "monsoon lotus",
          "Pongal harvest", "star and crescent", "spring marigolds"]

CONSTRAINTS = (
    " This will be physically drawn at a small 28mm scale by a "
    "powder-dispensing robot: ONE motif only, a centre circle with ONE ring "
    "of 6-9 petals (or one shape repeated 6-9 times), optionally a thin outer "
    "ring of small accent points. No second layer, no heavy border, no dense "
    "detail. Every outline a single thin clean stroke, never thick or filled, "
    "with a clear gap between petals. Motif large and centred, ~75% of the "
    "frame. Viewed straight-on from above like a coloring-book stencil. No "
    "text, no watermark. Variation seed: {seed}."
)


def load_key(cli_key):
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("OPENAI_API_KEY", "").strip()
    if env:
        return env
    if os.path.exists(AI_KEY_FILE):
        with open(AI_KEY_FILE) as fh:
            k = fh.read().strip()
        if k:
            return k
    sys.exit("No API key found. Pass --key sk-... or put it in openai_key.txt "
             "or the OPENAI_API_KEY env var.")


def build_prompt(i):
    # Mix plain and themed prompts for realistic variety.
    if i % 3 == 0:
        base = ("Create an original traditional Indian rangoli / mandala, "
                f"loosely inspired by \"{random.choice(THEMES)}\" for motif "
                "style only. Must still be unmistakably a radially symmetric "
                "rangoli of petal/floral/geometric motifs around a centre. "
                "Black outlines only on white. No fills or 3D.")
    else:
        base = random.choice(BASE_PROMPTS)
    return base + CONSTRAINTS.format(seed=random.randint(1, 999999))


def generate_one(api_key, prompt):
    body = {"model": "gpt-image-1", "prompt": prompt, "size": "1024x1024"}
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    item = result["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=60) as r:
            return r.read()
    raise ValueError("OpenAI response had no image.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None, help="OpenAI API key")
    ap.add_argument("--count", type=int, default=12, help="images to generate")
    args = ap.parse_args()

    api_key = load_key(args.key)
    os.makedirs(CACHED_RANGOLI_DIR, exist_ok=True)

    print(f"Generating {args.count} rangolis into {CACHED_RANGOLI_DIR}\n")
    made = 0
    for i in range(args.count):
        prompt = build_prompt(i)
        try:
            data = generate_one(api_key, prompt)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:200]
            print(f"  [{i+1}/{args.count}] HTTP {e.code}: {detail}")
            if e.code == 401:
                sys.exit("Key rejected — check your API key.")
            continue
        except Exception as e:
            print(f"  [{i+1}/{args.count}] failed: {e}")
            continue
        out = os.path.join(CACHED_RANGOLI_DIR, f"rangoli_{i+1:03d}.png")
        with open(out, "wb") as fh:
            fh.write(data)
        made += 1
        print(f"  [{i+1}/{args.count}] saved {os.path.basename(out)}")
        time.sleep(0.5)   # be gentle on the rate limit

    print(f"\nDone. {made} images cached. The app will now work fully offline.")


if __name__ == "__main__":
    main()
