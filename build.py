#!/usr/bin/env python3
"""
LUMARONOVA — static mock storefront build script.

Parses the Shopify product CSV, downloads every product/variant image plus
brand assets (logo, hero banners, category tiles, trust icons, fonts) from the
live store, then generates a fully static storefront:

    index.html          — homepage (header, hero, category tiles, product grid)
    products/<handle>.html — one page per product (gallery + copy-image-URL)
    assets/style.css, assets/app.js
    images/             — downloaded product images (self-hosted, no Shopify CDN)
    products.json       — machine-readable catalog

Idempotent: re-running skips already-downloaded files.
"""
import csv
import html as htmlmod
import json
import os
import re
import shutil
import sys
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join("/Users/rhro/Downloads/lumaronova assets/products_extract/products_export.csv")
IMAGES_DIR = os.path.join(HERE, "images")
ASSETS_DIR = os.path.join(HERE, "assets")
IMG_DIR = os.path.join(ASSETS_DIR, "img")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")
PRODUCTS_DIR = os.path.join(HERE, "products")
DATA_DIR = os.path.join(HERE, "data")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
BASE = "https://lumaronova.online"

# ----------------------------------------------------------------------------
# Brand assets to mirror (live store) — (url, local relative dest, content type)
# ----------------------------------------------------------------------------
BRAND_ASSETS = [
    # logo / favicon
    ("/cdn/shop/files/Gemini_Generated_Image_nf6fcanf6fcanf6f.png?v=1784672187", "img/logo.png", "png"),
    ("/cdn/shop/files/favicon.png", "img/favicon.png", "png"),
    # hero slides
    ("/cdn/shop/files/home-banner-v8.webp?v=1783913126", "img/hero-1.jpg", "jpg"),
    ("/cdn/shop/files/slide-2-banner-v9.webp?v=1783913128", "img/hero-2.jpg", "jpg"),
    # category tiles
    ("/cdn/shop/files/71yaaFllT7L._AC_SY535.jpg", "img/cat-rings.jpg", "jpg"),
    ("/cdn/shop/files/aj0030_0e98a61b-2912-4bdd-b694-c30b19b98307.jpg", "img/cat-bracelets.jpg", "jpg"),
    ("/cdn/shop/files/pixelcut-export_43.png", "img/cat-earrings.png", "png"),
    ("/cdn/shop/files/26b0263dd61d6c5bfe41feaac33d9ece.png", "img/cat-necklaces.png", "png"),
    # trust icons
    ("/cdn/shop/files/delivery-icon.svg?v=1783913124", "img/icon-delivery.svg", "svg"),
    ("/cdn/shop/files/return-icon.svg?v=1783913127", "img/icon-return.svg", "svg"),
    ("/cdn/shop/files/secure-icon.svg?v=1783913127", "img/icon-secure.svg", "svg"),
]

FONT_ASSETS = [
    ("/cdn/fonts/poppins/poppins_n4.0ba78fa5af9b0e1a374041b3ceaadf0a43b41362.woff2", "fonts/poppins-400.woff2"),
    ("/cdn/fonts/poppins/poppins_n7.56758dcf284489feb014a026f3727f2f20a54626.woff2", "fonts/poppins-700.woff2"),
    ("/cdn/fonts/andada_pro/andadapro_n6.9d2eb02e005e338e8521851933208672b925c734.woff2", "fonts/andada-pro-600.woff2"),
]

# ----------------------------------------------------------------------------
# Download helpers
# ----------------------------------------------------------------------------
def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download(url, dest, force=False):
    """Download url -> dest (absolute path). Returns 'ok', 'skipped', or 'error'."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0 and not force:
        return "skipped"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        data = http_get(url)
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"
    if not data:
        return "error: empty body"
    with open(dest, "wb") as f:
        f.write(data)
    return "ok"


# ----------------------------------------------------------------------------
# CSV parsing
# ----------------------------------------------------------------------------
def parse_csv(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    by_handle = OrderedDict()
    for r in rows:
        h = (r["Handle"] or "").strip()
        if not h:
            continue
        by_handle.setdefault(h, []).append(r)
    return by_handle


def clean_description(raw):
    """Strip images/links, keep readable text as paragraphs."""
    if not raw:
        return ""
    s = raw
    # drop image tags (alicdn embeds etc.) and script/style
    s = re.sub(r"<(img|script|style|iframe|video|audio|object|embed)\b[^>]*>.*?</\1>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<(img|script|style|iframe|video|audio|object|embed)\b[^>]*/?>", " ", s, flags=re.I)
    # unwrap links, keep text
    s = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", s, flags=re.I | re.S)
    # block-level tags -> newline
    s = re.sub(r"</(p|div|li|ul|ol|h[1-6]|br|tr)>", "\n", s, flags=re.I)
    s = re.sub(r"<(br|p|div|li|ul|ol|h[1-6]|tr)\b[^>]*>", "\n", s, flags=re.I)
    # strip remaining tags
    s = re.sub(r"<[^>]+>", "", s)
    s = htmlmod.unescape(s)
    # collapse whitespace per line, then blank lines
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in s.split("\n")]
    out = []
    for ln in lines:
        if ln:
            out.append(ln)
    return "\n".join(out)


def classify(tags, title):
    t = {x.strip().lower() for x in tags}
    tt = title.lower()
    def word(kw):
        return re.search(r"\b" + re.escape(kw) + r"\b", tt) is not None
    # explicit tag wins
    if "earrings" in t:
        return "Earrings"
    if "bracelets" in t:
        return "Bracelets"
    if "necklaces" in t:
        return "Necklaces"
    if "rings" in t:
        return "Rings"
    # title heuristic (word-boundary; earring before ring to avoid substring clash)
    if word("earring") or word("earrings") or word("hoop") or word("stud"):
        return "Earrings"
    if word("bracelet") or word("bangle") or word("cuff") or word("anklet"):
        return "Bracelets"
    if word("necklace") or word("pendant") or word("choker") or word("locket") or word("rosary") or word("crucifix") or word("chain"):
        return "Necklaces"
    if word("ring") or word("band"):
        return "Rings"
    return "Accessories"


def slugify(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def build_products(by_handle):
    products = []
    url_to_local = {}  # CDN url -> local relative filename
    for h, rs in by_handle.items():
        title = ""
        vendor = ""
        ptype = ""
        tags = []
        body = ""
        for r in rs:
            if (r["Title"] or "").strip():
                title = r["Title"].strip()
            if (r["Vendor"] or "").strip():
                vendor = r["Vendor"].strip()
            if (r["Type"] or "").strip():
                ptype = r["Type"].strip()
            if (r["Tags"] or "").strip():
                tags = [x.strip() for x in r["Tags"].split(",") if x.strip()]
            if (r["Body (HTML)"] or "").strip():
                body = r["Body (HTML)"]

        # ordered images (position ascending), deduped
        img_pos = []
        for r in rs:
            u = (r["Image Src"] or "").strip()
            if not u.startswith("http"):
                continue
            pos = (r["Image Position"] or "").strip()
            try:
                pos = int(pos)
            except ValueError:
                pos = 999
            img_pos.append((pos, u))
        # dedupe preserving first position
        seen = set()
        ordered = []
        for pos, u in sorted(img_pos, key=lambda x: (x[0], x[1])):
            if u in seen:
                continue
            seen.add(u)
            ordered.append(u)

        # variants
        variants = []
        prices = []
        compare_at = None
        for r in rs:
            opt = {}
            for n in (1, 2, 3):
                nm = (r[f"Option{n} Name"] or "").strip()
                val = (r[f"Option{n} Value"] or "").strip()
                if nm and val:
                    opt[nm] = val
            price = (r["Variant Price"] or "").strip()
            sku = (r["Variant SKU"] or "").strip()
            varimg = (r["Variant Image"] or "").strip()
            cmp = (r["Variant Compare At Price"] or "").strip()
            if not (price or sku or opt):
                continue
            variants.append({
                "options": opt,
                "price": price,
                "compare_at": cmp,
                "sku": sku,
                "image_url": varimg,
            })
            if price:
                prices.append(float(price))
            if cmp and float(cmp) > 0:
                compare_at = cmp

        # assign local filenames: images/<handle>__<NNN>.<ext>
        images = []
        for i, u in enumerate(ordered, 1):
            ext = os.path.splitext(urlparse(u).path)[1].lower().lstrip(".") or "jpg"
            if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
                ext = "jpg"
            fn = f"{h}__{i:03d}.{ext}"
            url_to_local[u] = f"images/{fn}"
            images.append(f"images/{fn}")

        # map variant image urls to local
        for v in variants:
            v["image"] = url_to_local.get(v["image_url"], images[0] if images else None)
            v.pop("image_url", None)

        price_min = min(prices) if prices else None
        price_max = max(prices) if prices else None

        products.append({
            "handle": h,
            "title": title,
            "description": clean_description(body),
            "vendor": vendor,
            "type": ptype,
            "tags": tags,
            "category": classify(tags, title),
            "price": price_min,
            "price_max": price_max,
            "compare_at": compare_at,
            "images": images,
            "variants": variants,
        })

    # list of all (url, local) pairs to download
    downloads = []
    for u, loc in url_to_local.items():
        downloads.append((u, os.path.join(HERE, loc)))
    return products, downloads


# ----------------------------------------------------------------------------
# HTML generation
# ----------------------------------------------------------------------------
def fmt_price(p):
    if p is None:
        return ""
    try:
        return f"${float(p):,.2f}"
    except (ValueError, TypeError):
        return f"${p}"


def esc(s):
    return htmlmod.escape(s or "", quote=True)


def render_card(p):
    first = p["images"][0] if p["images"] else ""
    price = fmt_price(p["price"] if p["price"] == p["price_max"] else None)
    if p["price"] != p["price_max"]:
        price = f"{fmt_price(p['price'])} – {fmt_price(p['price_max'])}"
    cmp = ""
    if p.get("compare_at"):
        try:
            if float(p["compare_at"]) > float(p["price"] or 0):
                cmp = f'<s class="price-cmp">{fmt_price(p["compare_at"])}</s>'
        except (ValueError, TypeError):
            pass
    # image link opens direct image; title links to product page
    return f"""
    <article class="card" data-category="{esc(p['category'])}">
      <div class="card-media">
        <a href="{esc(first)}" target="_blank" rel="noopener" title="Open full image">
          <img src="{esc(first)}" alt="{esc(p['title'])}" loading="lazy">
        </a>
        <button class="copy-btn" data-copy="{esc(first)}" title="Copy image URL">
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11v14z"/></svg>
          <span>Copy image URL</span>
        </button>
      </div>
      <div class="card-info">
        <h3 class="card-title"><a href="products/{esc(p['handle'])}.html">{esc(p['title'])}</a></h3>
        <div class="card-price">{cmp}<span class="price">{price}</span></div>
        <span class="card-cat">{esc(p['category'])}</span>
      </div>
    </article>"""


def render_product_page(p):
    # product pages live in products/, so images need a ../ prefix
    imgs = ["../" + x for x in p["images"]]
    gallery = ""
    for i, img in enumerate(imgs):
        gallery += f"""
        <figure class="gallery-item" id="img-{i}">
          <a href="{esc(img)}" target="_blank" rel="noopener" title="Open full image in new tab">
            <img src="{esc(img)}" alt="{esc(p['title'])} — image {i+1}" loading="lazy">
          </a>
          <figcaption>
            <span class="img-label">Image {i+1} of {len(imgs)}</span>
            <button class="copy-btn" data-copy="{esc(img)}">
              <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11v14z"/></svg>
              <span>Copy image URL</span>
            </button>
          </figcaption>
        </figure>"""

    price = fmt_price(p["price"])
    if p["price"] != p["price_max"]:
        price = f"{fmt_price(p['price'])} – {fmt_price(p['price_max'])}"
    cmp = ""
    if p.get("compare_at"):
        try:
            if float(p["compare_at"]) > float(p["price"] or 0):
                cmp = f'<s class="price-cmp">{fmt_price(p["compare_at"])}</s>'
        except (ValueError, TypeError):
            pass

    # variant chips
    var_html = ""
    if p["variants"]:
        opts = [v["options"] for v in p["variants"]]
        if any(opts):
            var_html = '<div class="variants"><h3>Options</h3>'
            seen_vars = set()
            for v in p["variants"]:
                if not v["options"]:
                    continue
                key = json.dumps(v["options"], sort_keys=True)
                if key in seen_vars:
                    continue
                seen_vars.add(key)
                label = " / ".join(f"{k}: {val}" for k, val in v["options"].items())
                vprice = fmt_price(v["price"]) if v["price"] else ""
                var_html += f'<span class="variant-chip">{esc(label)} <b>{vprice}</b></span>'
            var_html += "</div>"

    desc_html = ""
    if p["description"]:
        paras = [esc(x) for x in p["description"].split("\n") if x.strip()]
        desc_html = "<div class=\"description\">" + "".join(f"<p>{x}</p>" for x in paras) + "</div>"

    title = esc(p["title"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — LUMARONOVA</title>
<meta name="description" content="{title}">
<link rel="icon" type="image/png" href="../assets/img/favicon.png">
<link rel="stylesheet" href="../assets/style.css">
</head>
<body>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="logo" href="../index.html"><img src="../assets/img/logo.png" alt="LUMARONOVA"></a>
    <nav class="main-nav">
      <a href="../index.html">Home</a>
      <a href="../index.html#rings">Rings</a>
      <a href="../index.html#bracelets">Bracelets</a>
      <a href="../index.html#earrings">Earrings</a>
      <a href="../index.html#necklaces">Necklaces</a>
    </nav>
  </div>
</header>

<main class="wrap product-page">
  <nav class="crumbs"><a href="../index.html">Shop all</a> <span>/</span> <span>{title}</span></nav>
  <div class="product-layout">
    <div class="gallery">
      {gallery}
    </div>
    <aside class="product-meta">
      <h1 class="product-title">{title}</h1>
      <div class="product-price">{cmp}<span class="price">{price}</span></div>
      <span class="product-cat">{esc(p['category'])}</span>
      {var_html}
      <p class="hint">Click any image to open it full-size in a new tab. Use “Copy image URL” to copy its direct, public HTTPS link.</p>
      <div class="desc-wrap">
        <h2>Description</h2>
        {desc_html}
      </div>
    </aside>
  </div>
</main>

<footer class="site-footer">
  <div class="wrap footer-inner">
    <div class="footer-brand"><img src="../assets/img/logo.png" alt="LUMARONOVA"></div>
    <div class="footer-links">
      <a href="../index.html">Home</a>
      <a href="../index.html#rings">Rings</a>
      <a href="../index.html#bracelets">Bracelets</a>
      <a href="../index.html#earrings">Earrings</a>
      <a href="../index.html#necklaces">Necklaces</a>
    </div>
    <p class="footer-note">LUMARONOVA Fine Jewelry — static catalog. No checkout, no accounts, no Shopify dependency.</p>
  </div>
</footer>
<script src="../assets/app.js"></script>
</body>
</html>
"""


def render_index(products):
    cards = "\n".join(render_card(p) for p in products)
    by_cat = {}
    for p in products:
        by_cat.setdefault(p["category"], []).append(p)

    # category tiles
    tiles = [
        ("Rings", "assets/img/cat-rings.jpg"),
        ("Bracelets", "assets/img/cat-bracelets.jpg"),
        ("Earrings", "assets/img/cat-earrings.png"),
        ("Necklaces", "assets/img/cat-necklaces.png"),
    ]
    tiles_html = ""
    for cat, img in tiles:
        n = len(by_cat.get(cat, []))
        tiles_html += f"""
        <a class="cat-tile" href="#{slugify(cat)}">
          <img src="{img}" alt="{cat}">
          <span class="cat-label">{cat}<small>{n} items</small></span>
        </a>"""

    # trust badges
    trust = [
        ("assets/img/icon-delivery.svg", "Fast Shipping"),
        ("assets/img/icon-return.svg", "30-Day Returns"),
        ("assets/img/icon-secure.svg", "Secure Checkout"),
    ]
    trust_html = "".join(
        f'<div class="trust-item"><img src="{i}" alt=""><span>{t}</span></div>' for i, t in trust
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LUMARONOVA Fine Jewelry — Catalog</title>
<meta name="description" content="LUMARONOVA Fine Jewelry — static product catalog with copy-image-URL support.">
<link rel="icon" type="image/png" href="assets/img/favicon.png">
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="logo" href="index.html"><img src="assets/img/logo.png" alt="LUMARONOVA"></a>
    <nav class="main-nav">
      <a href="index.html">Home</a>
      <a href="#rings">Rings</a>
      <a href="#bracelets">Bracelets</a>
      <a href="#earrings">Earrings</a>
      <a href="#necklaces">Necklaces</a>
    </nav>
  </div>
</header>

<section class="hero">
  <img src="assets/img/hero-1.jpg" alt="Effortless Elegance for Every Day">
  <div class="hero-content">
    <h2>Effortless Elegance<br>for Every Day</h2>
    <a class="hero-btn" href="#catalog">Shop the catalog</a>
  </div>
</section>

<section class="wrap">
  <div class="cat-tiles">
    {tiles_html}
  </div>
</section>

<section class="wrap" id="catalog">
  <div class="catalog-head">
    <h2>Browse our products</h2>
    <div class="filters" id="filters">
      <button class="filter-btn active" data-filter="all">All</button>
      <button class="filter-btn" data-filter="Rings">Rings</button>
      <button class="filter-btn" data-filter="Bracelets">Bracelets</button>
      <button class="filter-btn" data-filter="Earrings">Earrings</button>
      <button class="filter-btn" data-filter="Necklaces">Necklaces</button>
      <button class="filter-btn" data-filter="Accessories">Accessories</button>
    </div>
  </div>
  <div class="grid" id="grid">
    {cards}
  </div>
</section>

<section class="wrap trust-bar">
  {trust_html}
</section>

<footer class="site-footer">
  <div class="wrap footer-inner">
    <div class="footer-brand"><img src="assets/img/logo.png" alt="LUMARONOVA"></div>
    <div class="footer-links">
      <a href="index.html">Home</a>
      <a href="#rings">Rings</a>
      <a href="#bracelets">Bracelets</a>
      <a href="#earrings">Earrings</a>
      <a href="#necklaces">Necklaces</a>
    </div>
    <p class="footer-note">LUMARONOVA Fine Jewelry — static catalog. No checkout, no accounts, no Shopify dependency.</p>
  </div>
</footer>
<script src="assets/app.js"></script>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    for d in (IMAGES_DIR, ASSETS_DIR, IMG_DIR, FONTS_DIR, PRODUCTS_DIR, DATA_DIR):
        os.makedirs(d, exist_ok=True)

    print("== Parsing CSV ==")
    by_handle = parse_csv(CSV_PATH)
    products, downloads = build_products(by_handle)
    print(f"   {len(products)} products, {len(downloads)} distinct product images")

    # copy source CSV into project (for repo completeness)
    shutil.copy(CSV_PATH, os.path.join(DATA_DIR, "products_export.csv"))

    # ---- download brand + font assets (sequential, few files) ----
    print("== Downloading brand assets ==")
    for path, rel, ctype in BRAND_ASSETS:
        url = BASE + path
        dest = os.path.join(ASSETS_DIR, rel)
        r = download(url, dest)
        print(f"   [{r:>7}] {rel}")
    for path, rel in FONT_ASSETS:
        url = BASE + path
        dest = os.path.join(ASSETS_DIR, rel)
        r = download(url, dest)
        print(f"   [{r:>7}] {rel}")

    # ---- download product images (concurrent) ----
    print(f"== Downloading {len(downloads)} product images ==")
    failed = []
    done = 0
    skipped = 0

    def work(item):
        url, dest = item
        return url, download(url, dest)

    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(work, item) for item in downloads]
        for fut in as_completed(futs):
            url, res = fut.result()
            if res == "ok":
                done += 1
            elif res == "skipped":
                skipped += 1
            else:
                failed.append((url, res))
            n = done + skipped + len(failed)
            if n % 50 == 0 or n == len(downloads):
                print(f"   ... {n}/{len(downloads)} (ok={done}, skip={skipped}, fail={len(failed)})")

    print(f"   Downloaded: {done}, skipped (cached): {skipped}, failed: {len(failed)}")
    for url, err in failed[:20]:
        print(f"   FAIL {url} -> {err}")

    # ---- write products.json ----
    with open(os.path.join(HERE, "products.json"), "w") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    print("== Wrote products.json ==")

    # ---- generate HTML ----
    with open(os.path.join(HERE, "index.html"), "w") as f:
        f.write(render_index(products))
    print("== Wrote index.html ==")
    for p in products:
        with open(os.path.join(PRODUCTS_DIR, f"{p['handle']}.html"), "w") as f:
            f.write(render_product_page(p))
    print(f"== Wrote {len(products)} product pages ==")

    print("\nDONE.")
    if failed:
        print(f"WARNING: {len(failed)} image(s) failed to download (see list above).")


if __name__ == "__main__":
    main()
