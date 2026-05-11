# TATUUM Product Collection Test Report

Date: 2026-05-11

Target: `https://www.tatuum.com/en/`

Goal: Collect 200 product records with title, price, color, size, description, and product image URLs.

## Result

The task completed successfully.

Output files were generated locally under `output/`:

- `tatuum_products_200.csv`
- `tatuum_products_200_summary.json`
- `tatuum_products_200_sample.json`

The `output/` directory is gitignored, so generated task data is not committed.

## Field Coverage

| Field | Coverage |
| --- | ---: |
| title | 200 / 200 |
| price | 200 / 200 |
| color | 200 / 200 |
| sizes | 200 / 200 |
| description | 200 / 200 |
| image_url | 200 / 200 |
| image_urls | 200 / 200 |
| available_sizes | 146 / 200 |

`available_sizes` was added as a quality/debug field. The requested `sizes` field contains all detected size options.

## Crawpapa-Fetch Evidence

The MCP analysis showed:

- The home page behaves like a JavaScript-heavy shell for static requests.
- Browser rendering exposes useful DOM content, but homepage/list extraction is not the best primary path.
- `infer_category_tree` found a public product sitemap through robots/sitemap discovery.
- The product sitemap exposed thousands of product detail URLs.
- Detail pages contain stable structured product data and Magento/Hyva swatch configuration.

The winning collection path was:

```text
probe_access_strategy
  -> infer_category_tree
  -> product sitemap
  -> detail page fetch
  -> JSON-LD / Open Graph / product attribute table / swatch config extraction
  -> CSV export
```

## Extraction Strategy

The collector used public product URLs from:

```text
https://www.tatuum.com/sitemap/sitemap_pl_product.xml
```

Those URLs were converted to `/en/p/...` detail pages where possible so the final records matched the requested English site. The final redirected URL was preserved in each row.

Field sources:

- `title`: Product JSON-LD, fallback to `h1`
- `price`: Product JSON-LD offer price, fallback to product meta tags
- `original_price`: Open Graph product original-price meta
- `color`: product detail attribute row labeled `Color` / `Kolor`, fallback to meta title pattern
- `sizes`: Magento/Hyva `initConfigurableOptions(...)` size options
- `available_sizes`: Magento/Hyva salable swatch options
- `description`: Product JSON-LD/meta description, upgraded to long detail popup text when available
- `image_urls`: Product JSON-LD/Open Graph/catalog product images, filtered to `/media/catalog/product/`

## What This Test Proved

This was a good example of Crawpapa-Fetch acting as an Agent sensing layer instead of a simple selector helper.

The important value was not just fetching a page. The tool chain helped identify that the homepage was a weak extraction target and that the public sitemap plus detail-page structured data was the correct stable route.

The result supports the current product direction:

- Prefer runtime/site evidence over generic DOM guessing.
- Treat sitemap/category discovery as a first-class path for ecommerce.
- Use detail-page structured data and platform config when list pages are shallow or JS-heavy.
- Keep generated data out of git and commit only reports, docs, schemas, and reusable code.

## Gaps Exposed

The task still required custom Python glue after MCP analysis. Future MCP upgrades should reduce that gap:

- Add a higher-level `collect_from_product_sitemap` or plan template for sitemap-to-detail extraction.
- Let `build_site_model` promote product sitemap coverage into `best_data_source`.
- Improve detail-page field inference for Magento/Hyva product pages, especially swatch config and product attributes.
- Add explicit output contracts for ecommerce fields such as all sizes, available sizes, original price, and image filtering.
- Add a report field that explains why list-page extraction was rejected in favor of sitemap/detail extraction.

## Operator Notes

No CAPTCHA solving, login bypass, private endpoint access, or disallowed filtered URLs were used.

Robots.txt was checked. The task avoided disallowed filter/query paths and used public product sitemap/detail pages.
