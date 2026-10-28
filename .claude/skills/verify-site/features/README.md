# etp-hermes site feature map

Routes are relative to `BASE_URL` (`http://127.0.0.1:<port>/etp-hermes`). Source: `site/apps/web/src/app/`.

| Feature | Route(s) | File | Last proved |
|---|---|---|---|
| Company directory (search, filters, sort) | `/` | [directory.md](directory.md) | 2026-10-28 (search) |
| Company page (living brief / fallback) | `/c/<slug>/` | [company-page.md](company-page.md) | — |
| Daily digests index | `/digests/` | [digests-index.md](digests-index.md) | — |
| Digest detail + prev/next | `/digests/<YYYY-MM-DD>/` | [digest-detail.md](digest-detail.md) | — |

Cross-cutting: header (`Directory`, `Daily digests` links, `<n> companies tracked`) and footer (`Data refreshed <date> · <n> briefs · <n> digests`) appear on every route; the build-data step (`site/apps/web/scripts/build-data.mjs`) feeds all of them.
