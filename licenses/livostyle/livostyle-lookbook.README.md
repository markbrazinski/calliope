# Livostyle Lookbook — 50 AI-Pipeline Outfit Videos

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20155974.svg)](https://doi.org/10.5281/zenodo.20155974)

> 50 short (6-second, 9:16 vertical) outfit videos generated programmatically from the Livostyle.com catalog. Zero AI-generation cost (uses ffmpeg Ken Burns over real product images). Ready for YouTube Shorts, TikTok, Pinterest Idea Pins, Instagram Reels.

🌐 **Live gallery**: https://arturayupov.github.io/livostyle-lookbook/

## What's inside

| Folder | What |
|---|---|
| `videos/*.mp4` | 50 rendered videos, 1080×1920, ~700 KB each |
| `src/render_one.sh` | Single-video renderer (ffmpeg) |
| `src/batch_render.py` | Batch script — pulls catalog, picks 50, renders all |
| `src/selection.json` | Frozen selection used for these renders |
| `videos/manifest.json` | Metadata index of all rendered videos |
| `index.html` | Static gallery for browsing |

## How it works

1. Loads the live MIT-licensed catalog from [arturayupov/womens-fashion-catalog-open-data](https://github.com/arturayupov/womens-fashion-catalog-open-data)
2. Picks 50 products with rating ≥ 4.7, ≥ 5 reviews, valid image, in-stock — one per product_type max for diversity
3. For each: downloads featured image at 1080×1620, runs ffmpeg with Ken Burns zoom (zoom 1.0 → 1.18 over 180 frames), overlays title + price + Livostyle branding, outputs MP4

Render time: ~5 min for 50 videos on free GitHub Actions runner. Zero external API cost.

## Re-render

```bash
git clone https://github.com/arturayupov/livostyle-lookbook
cd livostyle-lookbook
python3 src/batch_render.py
```

Output: 50 fresh videos in `videos/` from the latest catalog state.

## Upload to social

Each video is:
- **1080×1920** (9:16 vertical) — correct dimensions for YouTube Shorts, TikTok, Instagram Reels, Pinterest Idea Pins
- **6 seconds** — under all platform minimums (TikTok 3s, IG Reels 3s, Shorts 60s)
- **H.264 / yuv420p** — universal codec, works everywhere
- **+faststart MOOV** — fast streaming start

Manifest format makes bulk-upload trivial:
```python
import json
for v in json.load(open('videos/manifest.json')):
    upload_to_youtube(v['video'], title=v['title'], description=f"${v['price']} {v['url']}")
```

## License

MIT for code. Catalog images remain © Livostyle (used here under MIT license of the open catalog dataset).

## Related

- 🌐 https://livostyle.com — the live store
- 📊 [Methodology paper](https://doi.org/10.5281/zenodo.20155974) — the GEO case study this is part of
- 🐙 [Open catalog data](https://github.com/arturayupov/womens-fashion-catalog-open-data)
- 🎀 [Wedding Outfit Finder](https://github.com/arturayupov/wedding-guest-outfit-finder)
- 📊 [Fashion-Agent-Benchmark](https://github.com/arturayupov/fashion-agent-benchmark)
- 📈 [ACR Tracker](https://github.com/arturayupov/acr-tracker)
