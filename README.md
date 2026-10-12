# LUMARONOVA — Static Storefront

A self-contained static mock of the LUMARONOVA fine-jewelry storefront, rebuilt
so the catalog remains browsable after the original Shopify store is retired.
No Shopify API, no CDN dependency, no checkout, no backend — just static files.

## Purpose

Visitors can:

- Browse the full product catalog (responsive grid with titles, prices, categories).
- Click any product image to open its **direct public URL** in a new tab.
- Use the **"Copy image URL"** button beside every image to copy that image's
  absolute HTTPS URL (computed from the site's current origin — never a
  product-page URL).

Every product has a detail page with a full gallery of all its images and
variant/option info.

## How it was built

1. `products_export.csv` — the Shopify product export (46 products, 473 images).
2. `build.py` — a Python script that:
   - Parses the CSV (proper `csv` module), groups rows by product handle.
   - Extracts every product + variant image URL.
   - Downloads all images into `images/` (safe, unique filenames, correct extensions).
   - Downloads brand assets (logo, hero banners, category tiles, icons, fonts).
   - Generates `index.html`, `products/*.html`, and `products.json`.
3. `assets/style.css` + `assets/app.js` — hand-written styling and interactions.

## Project structure

```
lumaronova/
├── index.html            # homepage (header, hero, category tiles, product grid)
├── products/             # one HTML page per product (gallery + copy buttons)
├── assets/
│   ├── style.css         # all styling (brand: cream #FCF8F5, gold #CB8012)
│   ├── app.js            # copy-image-URL + category filter
│   ├── img/              # logo, favicon, hero banners, category tiles, icons
│   └── fonts/            # self-hosted Poppins + Andada Pro (woff2)
├── images/               # 473 self-hosted product images (no Shopify CDN)
├── products.json         # machine-readable catalog (generated)
├── data/products_export.csv  # source CSV (for reference / rebuild)
├── build.py              # build script
└── README.md
```

## Local preview

From the project root:

```
python3 -m http.server 8000
```

Then open http://localhost:8000/ .

## Rebuild

After editing `build.py` or replacing the source CSV:

```
python3 build.py
```

The script is idempotent — already-downloaded images are skipped.

## Deployment (GitHub + Vercel)

This is a pure static site:

- **GitHub**: push the whole folder. No secrets are committed.
- **Vercel**: import the repo as a static project (no build command, no
  framework). The catalog and all image files are served publicly with no login.

All assets are self-hosted under `images/` and `assets/` — nothing references
`cdn.shopify.com`, `alicdn.com`, or the old domain, so the site keeps working
independently of Shopify.

## Notes

- Category labels are derived from product tags plus a title heuristic
  (Rings / Bracelets / Earrings / Necklaces / Accessories).
- Images are the original Shopify-resolution files (~1,000 px). Total image
  weight is ~124 MB across 473 files; this is intentional to preserve quality.
