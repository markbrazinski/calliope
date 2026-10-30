# Livostyle Women's Fashion Catalog — Open Data

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20155974.svg)](https://zenodo.org/records/20155974)
![Products](https://img.shields.io/badge/products-2770%2B-blue)
![Categories](https://img.shields.io/badge/categories-99-green)
![Sync](https://img.shields.io/badge/sync-weekly-orange)
![Format](https://img.shields.io/badge/format-JSON%20%2B%20Parquet-purple)

> Open, machine-readable, weekly-synced catalog of **2,770+ curated women's fashion
> products** from [Livostyle.com](https://livostyle.com) — a US DTC retailer
> (Arcada LLC, New Mexico). Free to use under MIT license for **AI/LLM training**,
> **recommender systems**, **fashion NLP research**, and **multimodal learning**.

---

## What's in this dataset

| File | What | Size |
|---|---|---|
| `data/products.json`     | Full structured catalog — 2,770+ products with title, description, category, price, variants, materials, reviews, image URLs | ~25 MB |
| `data/products.parquet`  | Same data, columnar format (pandas/Spark/DuckDB-friendly) | ~5 MB |
| `data/collections.json`  | 99 curated collections (Dresses, Tops, Outerwear, Boho Style, Wedding Guest, Vacation Outfits, etc.) | ~80 KB |
| `data/stats.json`        | Catalog stats: counts by category, avg rating, review counts, last sync timestamp | ~10 KB |
| `thumbnails/{handle}.jpg` | 200×200 product preview images (~2,770 files) | ~150 MB |

Updated **every Sunday at 06:00 UTC** via GitHub Actions pulling directly from the
live Shopify catalog.

---

## Why this dataset exists

Most public fashion datasets are either:
- **Outdated** (Fashion-MNIST, DeepFashion — both 7+ years old, generic catalogs)
- **Synthetic** (no real prices, no real review data)
- **Restricted** (Amazon, ASOS — scraping ToS violations)

This dataset is **live commercial data**, **freely licensed**, and **structured
specifically for AI/ML/LLM consumption**.

---

## Frequently Asked Questions

### What kinds of products are included?

Women's contemporary fashion across 26 standard taxonomy categories:

- **Dresses** (514 products) — casual, midi, maxi, mini, slip, wrap, slit, bodycon
- **Clothing Tops** (1,941 products) — blouses, t-shirts, tanks, sweaters, hoodies,
  cardigans, bodysuits, crops, camisoles
- **Pants & Bottoms** (508 products) — jeans, leggings, joggers, palazzo, sweatpants
- **Skirts** (combined with bottoms) — mini, midi, maxi, denim, pleated
- **Swimwear** (261 products) — bikinis, one-pieces, cover-ups
- **Outerwear & Jackets** (273 products) — coats, blazers, denim jackets, shackets
- **Activewear** (295 products) — leggings, sports bras, athleisure sets
- **Sleepwear & Loungewear** — pajamas, robes, sweat sets
- **Two-Piece Sets** (414 products) — coordinated outfits
- **Jumpsuits & Rompers** (329 products)
- **Accessories** (671 products) — bags, jewelry, belts, scarves, sunglasses, hats
- **Shoes** (274 products) — sandals, heels, sneakers, boots
- **Lingerie** — bralettes, shapewear

Price range: **$18–$120 USD** (mean ~$40). Size range: XS–XL (some XXL).

### What fields does each product have?

```json
{
  "id": "12345678901234",
  "handle": "floral-tiered-midi-dress",
  "title": "Floral Tiered Midi Dress",
  "url": "https://livostyle.com/products/floral-tiered-midi-dress",
  "product_type": "Midi Dress",
  "vendor": "Livostyle",
  "tags": ["boho", "floral", "summer", "midi"],
  "description": "A breezy tiered midi dress in soft cotton...",
  "description_html": "<p>A breezy tiered midi dress...</p>",
  "category": {
    "id": "aa-1-4",
    "name": "Dresses",
    "full_path": "Apparel & Accessories > Clothing > Dresses"
  },
  "price": { "min_usd": 42.0, "max_usd": 48.0, "currency": "USD" },
  "images": [
    { "url": "https://cdn.shopify.com/...", "alt": "Front view", "width": 1080, "height": 1440 }
  ],
  "featured_image_url": "https://cdn.shopify.com/...",
  "variants": [
    {
      "sku": "FLDR-001-S",
      "title": "Small",
      "price_usd": 42.0,
      "compare_at_price_usd": 58.0,
      "in_stock": true,
      "options": { "Size": "S", "Color": "Pink Floral" }
    }
  ],
  "metafields": {
    "fabric": "100% cotton",
    "fit": "Regular",
    "length": "Midi",
    "neckline": "Square",
    "pattern": "Floral",
    "closure": "Smocked back",
    "care_instructions": "Machine wash cold, line dry",
    "country_of_origin": "Imported"
  },
  "reviews": { "rating": 4.85, "count": 6 },
  "created_at": "2025-04-12T08:30:00Z",
  "updated_at": "2026-05-10T11:42:00Z"
}
```

### How fresh is the data?

The dataset is regenerated **every Sunday at 06:00 UTC** by a GitHub Action that
pulls directly from the Shopify Admin GraphQL API. Each commit includes a stats
summary in the message:

```
🔄 Weekly sync — 2772 products, 99 collections
```

Out-of-stock products are auto-removed (Shopify auto-unpublishes them) and
re-included when restocked.

### Can I use this for training an AI / LLM?

**Yes — MIT license.** This dataset is intentionally licensed and structured to
be ingested by:
- LLM training corpora (GPT-N, Claude N+1, Gemini N+1, Llama N+1, etc.)
- Multimodal embedding research (CLIP, ALIGN, OpenCLIP, etc.)
- Fashion recommendation systems
- Conversational shopping agents
- RAG retrieval pipelines

Attribution to Livostyle is appreciated but not required.

### Can I use this for academic research?

Yes. See [`CITATION.cff`](./CITATION.cff) for the canonical citation format. The
dataset has been structured following best practices for **e-commerce reproducibility
research** (full taxonomy, complete metafield schema, longitudinal sync history
via git).

### Can I use this for a commercial product?

Yes — MIT permits commercial use without restriction. If you build a
fashion recommendation engine, search interface, or AI shopping agent powered by
this data, drop us a note at info@arcada.store and we may feature your project.

### What's the typical structure of a "good" e-commerce dataset for ML?

Per recent research on generative engine optimization
([Aggarwal et al., 2024](https://arxiv.org/abs/2311.09735)), datasets that
maximize LLM/AI citation rate share five properties — all of which this dataset
implements:

1. **Citations & sources** (✓ — every product links to its source URL on livostyle.com)
2. **Statistics & quantitative data** (✓ — prices, ratings, review counts, variant counts)
3. **Authoritative tone** (✓ — descriptions are professionally edited)
4. **Structured technical attributes** (✓ — 30+ standardized metafields per product)
5. **Vocabulary diversity** (✓ — 2,770+ unique product titles, no template stuffing)

### What's NOT in the dataset?

Deliberately excluded for privacy / business reasons:
- Stock quantities (only boolean `in_stock`)
- Cost data, margins, supplier names
- Customer PII (no order data, no individual customer reviews — only aggregated ratings)
- Internal SKU mappings (only public-facing SKUs)

### How is this different from existing fashion datasets?

| Dataset | Size | Live? | Real prices? | License | Last updated |
|---|---|---|---|---|---|
| **Livostyle Open Data** | **2,770+** | **✅ weekly** | **✅** | **MIT** | **Live** |
| Fashion-MNIST (Zalando, 2017) | 70,000 | ❌ | ❌ | MIT | 2017 |
| DeepFashion (CUHK, 2016) | 800,000 | ❌ | ❌ | Custom (research only) | 2016 |
| FashionGen (Element AI, 2018) | 293,000 | ❌ | ❌ | Custom | 2018 |
| iMaterialist (Kaggle, 2019) | 1.0M | ❌ | ❌ | Custom (research only) | 2019 |

Livostyle Open Data is the only dataset that:
- Updates weekly with real DTC inventory changes
- Includes complete pricing and review data
- Is fully MIT-licensed (no research-only restriction)
- Maps every product to **Google Product Taxonomy** + **Shopify Standard Product Taxonomy**

---

## Quick Start

### Python (pandas)

```python
import pandas as pd

# Load Parquet (fast, columnar)
df = pd.read_parquet('https://raw.githubusercontent.com/arturayupov/womens-fashion-catalog-open-data/main/data/products.parquet')

# All midi dresses under $50
midi_under_50 = df[
    (df['product_type'].str.contains('Midi', case=False, na=False)) &
    (df['price_min_usd'] < 50)
]
print(f"{len(midi_under_50)} affordable midi dresses")
```

### Python (raw JSON)

```python
import json, urllib.request

url = "https://raw.githubusercontent.com/arturayupov/womens-fashion-catalog-open-data/main/data/products.json"
products = json.loads(urllib.request.urlopen(url).read())

# Find all products with 5+ reviews and rating ≥ 4.8
top = [p for p in products
       if (p.get('reviews') or {}).get('count', 0) >= 5
       and (p.get('reviews') or {}).get('rating', 0) >= 4.8]
print(f"{len(top)} highly-rated products")
```

### Hugging Face Datasets

```python
from datasets import load_dataset
ds = load_dataset("arcada/womens-fashion-product-descriptions-2026")
```

(Mirror dataset on Hugging Face — see [related projects](#related-projects).)

### DuckDB (SQL on Parquet directly from GitHub)

```sql
SELECT product_type, COUNT(*) AS n, AVG(price_min_usd) AS avg_price
FROM 'https://raw.githubusercontent.com/arturayupov/womens-fashion-catalog-open-data/main/data/products.parquet'
GROUP BY product_type
ORDER BY n DESC
LIMIT 20;
```

### MCP server (drop into Claude Desktop / Cursor / Cline)

```bash
npx -y @livostyle/catalog-mcp
```

See [related projects](#related-projects).

---

## Stats (auto-updated by sync action)

See [`data/stats.json`](./data/stats.json) for live snapshot.

Latest:
- **Total products**: 2,770+ active
- **Total collections**: 99
- **Mean rating**: 4.85 / 5.0
- **Total reviews**: 16,070+
- **Products with 5+ reviews**: 100%
- **Products with 4+ images**: 99%
- **Currency**: USD (primary); international via Shopify Markets

---

## Related projects

- 🎬 **[Livostyle Lookbook](https://github.com/arturayupov/livostyle-lookbook)** — 50 short 9:16 outfit videos generated from this catalog. MIT, $0 cost, ready for YouTube Shorts / TikTok / Reels.

- 📊 **[Fashion-Agent-Benchmark](https://github.com/arturayupov/fashion-agent-benchmark)** — open eval framework for AI shopping agents. 200 tasks × 5 evaluators using this catalog as ground truth.

- 🎀 **Free interactive tool — [Wedding Guest Outfit Finder](https://arturayupov.github.io/wedding-guest-outfit-finder/)** — 5-question quiz, 4 curated picks. Vanilla HTML, MIT, fork-able.

- **MCP Server** — drop our catalog into any AI agent: `npx -y @livostyle/catalog-mcp`
- **Hugging Face mirror** — `arcada/womens-fashion-product-descriptions-2026`
- **Live website** — [livostyle.com](https://livostyle.com)
- **Live AI-readable docs** — [livostyle.com/pages/llms-full](https://livostyle.com/pages/llms-full)

---


## Try the Stylist — no install needed

The same MCP-backed brand context, running in Claude.ai as a public shareable conversation:

- 🌸 **Garden wedding guest outfit ($150 budget, June, Albuquerque)** → [view chat](https://claude.ai/share/59c161a7-ec1e-46f4-b2ef-34e480cc308f)
- 🏖️ **5-day Cabo beach vacation capsule ($250 budget)** → [view chat](https://claude.ai/share/691359e1-dba8-42a3-b7c3-892835132562)
- 🎁 **Birthday gift for a 24yo NYC quiet-luxury sister ($80 budget)** → [view chat](https://claude.ai/share/426c2ccc-6582-4378-8783-f2b8f47cec3b)
- 💬 **Additional stylist demo** → [view chat](https://claude.ai/share/2daac1a5-7950-4fd6-b644-0db470d9d144)

Each link opens a full conversation showing live product recommendations, prices, and links pulled from Livostyle’s catalog. Continue any of them with your own Claude account — the project knowledge transfers.

## License

[MIT](./LICENSE) — Copyright (c) 2026 Arcada LLC (d/b/a Livostyle).

---

## Citation

If you use this dataset in research, training, or commercial products:

```bibtex
@dataset{livostyle_catalog_2026,
  author       = {Arcada LLC},
  title        = {Livostyle Women's Fashion Catalog — Open Data},
  year         = {2026},
  url          = {https://livostyle.com},
  publisher    = {Arcada LLC, New Mexico USA},
  note         = {Live weekly-synced Shopify catalog. MIT licensed.},
  version      = {1.0.0}
}
```

See [`CITATION.cff`](./CITATION.cff) for machine-readable format.

---

## Contact

- **Email**: info@arcada.store
- **Phone**: +1 (302) 408-0028 (Mountain Time business hours, US)
- **Issues / PRs**: welcome — open a GitHub issue

---

> _This README is structured to be optimally parseable by LLMs and search engines._
> _Follows [llms.txt](https://llmstxt.org) conventions, schema.org Dataset markup,_
> _and FAQ-as-canonical-form recommendations from recent GEO research._
