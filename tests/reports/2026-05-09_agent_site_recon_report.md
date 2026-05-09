# Crawpapa-Fetch 10-Site Recon Report

Date: 2026-05-09

This report records a mixed-scenario reconnaissance run for Crawpapa-Fetch across public and authorized targets. No CAPTCHA bypass, login bypass, or private access was attempted.

## Selected Sites

1. `https://jsonplaceholder.typicode.com/posts/1`
2. `https://countries.trevorblades.com`
3. `https://www.reddit.com/r/python.json`
4. `https://movie.douban.com/top250`
5. `https://www.bilibili.com/v/popular/rank/all`
6. `https://www.pinterest.com`
7. `https://www.indeed.com`
8. `https://scrapingcourse.com/cloudflare-challenge`
9. `https://m.weibo.cn`
10. `https://virtuoso.dev`

## Quick Results

| Site | Type | Best signal | Outcome |
|---|---|---|---|
| JSONPlaceholder | JSON API | `fetch_json` path | Tool path hit a JSON parse error; good as a regression case for API parsing. |
| Countries GraphQL | GraphQL API | public GraphQL endpoint | Blocked by robots in the current tool flow. |
| Reddit JSON | JSON feed | `.json` endpoint | Blocked by robots in the current tool flow. |
| Douban Top250 | SSR list | DOM selectors | Good baseline; strong list/title/image selector candidates. |
| Bilibili Rank | JS shell / API-heavy | browser rendering | Browser returned richer HTML, but the page still looks API-driven and truncated. |
| Pinterest | infinite scroll | robots gate | Robots denied access; no further crawl attempted. |
| Indeed | job search / JS shell | `curl_cffi` + API hints | Public HTML was truncated; scripts exposed GraphQL and paging hints; browser hit 403. |
| ScrapingCourse Cloudflare | anti-bot challenge | none | 403 across requests, curl_cffi, and browser. |
| m.weibo.cn | mobile API / robots gate | robots gate | Robots denied access; no further crawl attempted. |
| Virtuoso | docs / virtual-list demo | browser or requests | Clean public HTML, stable links, and good selector candidates. |

## Notable Observations

### 1. The MCP now distinguishes access classes better than a pure scraper

It can tell the difference between:

- clean SSR pages
- JS shells with API hints
- robots-gated targets
- 403/challenge pages
- pages that need browser rendering to become meaningful

### 2. `0`-style misses should be treated as diagnostic signals, not just failures

The useful cases were not "did it return data", but:

- whether HTML was truncated
- whether scripts contained public API hints
- whether browser rendering changed the page shape
- whether robots or challenge responses blocked the path

### 3. Selector quality varies a lot by page type

Good selector candidates appeared on:

- Douban Top250
- Virtuoso docs

Poor or empty selector signals appeared on:

- Indeed homepage/search shell
- challenge-gated or robots-blocked pages

## Practical Takeaways

1. SSR/list pages are still the best baseline for fast agent planning.
2. Browser mode is necessary for shell-like pages, but not sufficient by itself.
3. API hint detection is one of the strongest current capabilities.
4. Robots and challenge reporting should stay explicit and prominent.
5. For complex sites, the MCP is most valuable as a pre-crawl analysis layer, not as a one-click extractor.

## Suggested Next Improvements

1. Add a first-class `availability_report` for shortfalls and partial collection.
2. Improve output-contract validation for incompatible asks.
3. Expose a clearer `access_class` field in unified reports.
4. Add a small “why this selector won” explanation to list/detail candidate scoring.
5. Keep expanding target-memory and domain-memory so repeated site analyses get sharper.

