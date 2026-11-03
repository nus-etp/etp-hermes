# hermenaut site feature map

Routes are relative to `BASE_URL` (`http://127.0.0.1:<port>/hermenaut`; the basePath comes from `site/apps/web/next.config.js`). Source: `site/apps/web/src/app/`.

| Feature | Route(s) | File | Last proved |
|---|---|---|---|
| Company directory (search, filters, sort) | `/` | [directory.md](directory.md) | 2026-11-03 |
| Company page (living brief / fallback) | `/c/<slug>/` | [company-page.md](company-page.md) | 2026-11-03 |
| Metrics dashboard (composite rank, sort, filters) | `/metrics/` | [metrics.md](metrics.md) | 2026-11-03 |
| Daily digests index | `/digests/` | [digests-index.md](digests-index.md) | 2026-11-03 |
| Digest detail + prev/next | `/digests/<YYYY-MM-DD>/` | [digest-detail.md](digest-detail.md) | 2026-11-03 |

Cross-cutting: header (`Directory`, `Metrics`, `Daily digests` links, `<n> companies tracked`) and footer (`Data refreshed <date> · <n> briefs · <n> digests`) appear on every route; the build-data step (`site/apps/web/scripts/build-data.mjs`) feeds all of them.
