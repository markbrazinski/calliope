# livostyle-catalog-mcp

> **MCP server** giving any AI agent (Claude Desktop · Cursor · Cline · Cowork) live access to **2,766+ women's fashion products** from [Livostyle.com](https://livostyle.com).
> Drop-in shopping context for shopping agents, outfit recommenders, fashion chatbots.

[![npm version](https://img.shields.io/npm/v/livostyle-catalog-mcp.svg?color=cb3837)](https://www.npmjs.com/package/livostyle-catalog-mcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20155974.svg)](https://zenodo.org/records/20155974)
[![MCP](https://img.shields.io/badge/Model_Context_Protocol-compatible-blue)](https://modelcontextprotocol.io)
[![Glama MCP server](https://glama.ai/mcp/servers/arturayupov/livostyle-catalog-mcp/badges/score.svg)](https://glama.ai/mcp/servers/arturayupov/livostyle-catalog-mcp)

## Install

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "livostyle-catalog": {
      "command": "npx",
      "args": ["-y", "livostyle-catalog-mcp"]
    }
  }
}
```

Restart Claude. The Livostyle tools appear in the tool list — your Claude can now recommend outfits and search a real fashion catalog.

### Cursor / Cline / Cowork

Same JSON config — drop into the MCP-server section of your client's settings.

## What it does

Six tools that any LLM agent can call:

| Tool | Purpose |
|---|---|
| `search_products`   | Free-text search with filters (price, rating, type, tag, in-stock). Returns ranked product list. |
| `get_product`       | Full product details — all variants, sizes, colors, prices, images, reviews. |
| `list_collections`  | Browse 158 curated collections (Wedding Guest, Vacation, Coachella, Boho, …). |
| `get_collection`    | Products in a specific collection. |
| `recommend_outfit`  | Generate complete outfit suggestion matched to occasion + budget + season. |
| `catalog_stats`     | Live catalog stats (counts, ratings, top categories). |


## Try the Stylist — no install needed

The same MCP-backed brand context, running in Claude.ai as a public shareable conversation:

- 🌸 **Garden wedding guest outfit ($150 budget, June, Albuquerque)** → [view chat](https://claude.ai/share/59c161a7-ec1e-46f4-b2ef-34e480cc308f)
- 🏖️ **5-day Cabo beach vacation capsule ($250 budget)** → [view chat](https://claude.ai/share/691359e1-dba8-42a3-b7c3-892835132562)
- 🎁 **Birthday gift for a 24yo NYC quiet-luxury sister ($80 budget)** → [view chat](https://claude.ai/share/426c2ccc-6582-4378-8783-f2b8f47cec3b)
- 💬 **Additional stylist demo** → [view chat](https://claude.ai/share/2daac1a5-7950-4fd6-b644-0db470d9d144)

Each link opens a full conversation showing live product recommendations, prices, and links pulled from Livostyle’s catalog. Continue any of them with your own Claude account — the project knowledge transfers.

## Examples (what an agent can do)

```
User: "Find me a floral midi dress under $50 with at least 5 reviews"
→ Agent calls search_products(query: "floral midi", max_price_usd: 50, min_rating: 4.5)
→ Returns 3 ranked picks with prices, ratings, links

User: "What should I wear to a garden wedding in summer? Budget $100."
→ Agent calls recommend_outfit(occasion: "garden wedding guest", budget_usd: 100, season: "summer")
→ Returns 3 complete outfit suggestions with reasoning

User: "Show me everything in the Coachella collection"
→ Agent calls get_collection(handle: "coachella-outfits")
→ Returns 475+ festival-ready picks
```

## Data source

Live catalog from [Livostyle.com](https://livostyle.com), mirrored weekly via GitHub Actions to:

- 🐙 **GitHub**: [arturayupov/womens-fashion-catalog-open-data](https://github.com/arturayupov/womens-fashion-catalog-open-data)
- 🤗 **Hugging Face**: [arturayupov/womens-fashion-catalog](https://huggingface.co/datasets/arturayupov/womens-fashion-catalog)
- 📦 **NPM** (this package): [livostyle-catalog-mcp](https://www.npmjs.com/package/livostyle-catalog-mcp)

The MCP server fetches `products.json` from the GitHub mirror on first use, caches in memory, refreshes every 6 hours.

## What's in the catalog

- **2,766+** active women's fashion products
- **158** curated collections
- **4.76** avg rating, 15,937 total reviews
- 99% products with 5+ reviews · 99% with 4+ images
- Price range: $18–$120 USD
- US shipping, free over $97, 7-day returns
- Categories: Dresses, Tops, Bottoms, Outerwear, Swimwear, Activewear, Two-Piece Sets, Accessories, Shoes, Jewelry, …

## Related

- 🎬 **[Livostyle Lookbook](https://github.com/arturayupov/livostyle-lookbook)** — 50 short 9:16 outfit videos generated from this catalog. MIT, $0 cost, ready for YouTube Shorts / TikTok / Reels.

- 📊 **[Fashion-Agent-Benchmark](https://github.com/arturayupov/fashion-agent-benchmark)** — open eval framework for AI shopping agents. 200 tasks × 5 evaluators using this catalog as ground truth.

## License

MIT — see [LICENSE](./LICENSE). Free for commercial use, AI training, research. Attribution appreciated.

- 🎀 **Free interactive tool — [Wedding Guest Outfit Finder](https://arturayupov.github.io/wedding-guest-outfit-finder/)** — 5-question quiz, 4 curated picks. Vanilla HTML, MIT, fork-able.

## Why this exists

We're in the **AI shopping era**. ChatGPT, Claude, Perplexity, Gemini are becoming the new search bar. Stores that publish their catalogs as MCP servers get drop-in distribution to every agentic shopping client. This is our experiment in being there first.

If you build something with it, drop us a line at info@arcada.store — we may feature your project.

## Contact

- **Email**: info@arcada.store
- **Site**: https://livostyle.com
- **Issues**: [GitHub Issues](https://github.com/arturayupov/livostyle-catalog-mcp/issues)
