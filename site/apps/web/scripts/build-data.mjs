#!/usr/bin/env node
// Reads etp-hermes data + signals and emits JSON the Next app can statically
// import. Copies brief infographics into public/ so they're served by the
// static export at /etp-hermes/briefs/<slug>/infographic.png.

import { readFileSync, readdirSync, existsSync, mkdirSync, copyFileSync, writeFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '../../../..')
const DATA_DIR = join(REPO_ROOT, 'data')
const SIGNALS_DIR = join(REPO_ROOT, 'signals')
const BRIEFS_DIR = join(SIGNALS_DIR, 'briefs')
const UPDATES_DIR = join(SIGNALS_DIR, 'updates')
const APP_ROOT = resolve(__dirname, '..')
const OUT_DATA_DIR = join(APP_ROOT, 'src/data')
const OUT_PUBLIC_BRIEFS = join(APP_ROOT, 'public/briefs')

mkdirSync(OUT_DATA_DIR, { recursive: true })
mkdirSync(OUT_PUBLIC_BRIEFS, { recursive: true })

const toSlug = (s) =>
  String(s)
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '')

const readUtf8 = (p) => readFileSync(p, 'utf8')

// --- Parse LIVING_BRIEF.md ---------------------------------------------------

function sectionBody(md, heading) {
  // Split into level-2 sections. The first chunk is the document head.
  const parts = md.split(/(?=^## )/m)
  const want = heading.toLowerCase()
  for (const part of parts) {
    const head = part.match(/^##\s+(.+)/)
    if (head && head[1].trim().toLowerCase() === want) {
      return part.replace(/^##\s+.+\n?/, '')
    }
  }
  return undefined
}

function parseBrief(md) {
  const out = { sector: undefined, region: undefined, thesis: undefined, lastUpdated: undefined }

  const m = md.match(/_Last updated:\s*([^_]+?)_/)
  if (m) out.lastUpdated = m[1].trim()

  const thesisBody = sectionBody(md, 'Thesis')
  if (thesisBody) out.thesis = thesisBody.trim().split(/\n\s*\n/)[0].trim()

  const profileBody = sectionBody(md, 'Profile')
  if (profileBody) {
    for (const line of profileBody.split('\n')) {
      const sector = line.match(/^\s*-\s*Sector\s*:\s*(.+?)\s*$/i)
      if (sector) out.sector = sector[1].trim()
      const region = line.match(/^\s*-\s*Region\s*:\s*(.+?)\s*$/i)
      if (region) out.region = region[1].trim()
    }
  }
  return out
}

// --- Discover briefs ---------------------------------------------------------

const briefSlugs = new Set()
const briefsByDir = {}
if (existsSync(BRIEFS_DIR)) {
  for (const entry of readdirSync(BRIEFS_DIR)) {
    const p = join(BRIEFS_DIR, entry)
    if (!statSync(p).isDirectory()) continue
    const briefPath = join(p, 'LIVING_BRIEF.md')
    const infoPath = join(p, 'infographic.png')
    if (!existsSync(briefPath)) continue
    const md = readUtf8(briefPath)
    briefsByDir[entry] = {
      slug: entry,
      raw: md,
      parsed: parseBrief(md),
      hasInfographic: existsSync(infoPath),
    }
    briefSlugs.add(entry)
  }
}

// --- Load companies ----------------------------------------------------------

const companies = JSON.parse(readUtf8(join(DATA_DIR, 'companies.json')))

// --- Load digests + index per company ---------------------------------------

const digestFiles = existsSync(UPDATES_DIR)
  ? readdirSync(UPDATES_DIR).filter((f) => /^\d{4}-\d{2}-\d{2}\.md$/.test(f))
  : []

const digests = {}
const digestsLowercase = {} // for case-insensitive scanning
for (const f of digestFiles) {
  const date = f.replace(/\.md$/, '')
  const raw = readUtf8(join(UPDATES_DIR, f))
  digests[date] = raw
  digestsLowercase[date] = raw.toLowerCase()
}

function latestSignalDate(needles) {
  const dates = Object.keys(digests).sort().reverse()
  const lowered = needles.map((n) => n.toLowerCase()).filter((n) => n.length >= 3)
  for (const d of dates) {
    const body = digestsLowercase[d]
    for (const n of lowered) {
      if (body.includes(n)) return d
    }
  }
  return undefined
}

// --- Build the normalized records -------------------------------------------

function latestFunding(rounds) {
  if (!Array.isArray(rounds) || rounds.length === 0) return {}
  const dated = rounds.filter((r) => r.date)
  if (dated.length === 0) return { latestFundingStage: rounds[rounds.length - 1].stage }
  dated.sort((a, b) => (a.date < b.date ? 1 : -1))
  return { latestFundingDate: dated[0].date, latestFundingStage: dated[0].stage }
}

const normalized = []
const slugCollisions = {}
for (const c of companies) {
  const slug = toSlug(c.name)
  if (slugCollisions[slug]) slugCollisions[slug].push(c.name)
  else slugCollisions[slug] = [c.name]

  const sources = Array.isArray(c.sources) ? c.sources : []
  const sourceTypes = [...new Set(sources.map((s) => s.type).filter(Boolean))]
  const stagesRaw = Array.isArray(c.funding_rounds)
    ? c.funding_rounds.map((r) => r.stage).filter(Boolean)
    : []
  const fundingStages = [...new Set(stagesRaw)]
  const { latestFundingDate, latestFundingStage } = latestFunding(c.funding_rounds)

  const aliases = Array.isArray(c.aliases) ? c.aliases : []
  const brief = briefsByDir[slug]
  const needles = [c.name, ...aliases]
  const lsd = latestSignalDate(needles)

  // Reduce a long "Southeast Asia (Singapore HQ; …)" to a filterable short label.
  const shortRegion = brief?.parsed.region
    ? brief.parsed.region.split(/[(/;,]/)[0].trim()
    : undefined

  normalized.push({
    slug,
    name: c.name,
    aliases,
    description: c.description ?? '',
    hasBrief: !!brief,
    sector: brief?.parsed.sector,
    region: shortRegion,
    regionFull: brief?.parsed.region,
    thesis: brief?.parsed.thesis,
    sourceTypes,
    fundingStages,
    latestFundingDate,
    latestFundingStage,
    latestSignalDate: lsd,
    identifiers: c.identifiers ?? {},
    fundingRoundsCount: stagesRaw.length,
  })
}

const collisions = Object.entries(slugCollisions).filter(([, names]) => names.length > 1)
if (collisions.length) {
  console.warn(`[build-data] Slug collisions (${collisions.length}):`)
  for (const [slug, names] of collisions) console.warn(`  ${slug} <- ${names.join(', ')}`)
}

// --- Warn on unmatched brief dirs -------------------------------------------

const normalizedSlugs = new Set(normalized.map((n) => n.slug))
const orphanBriefs = [...briefSlugs].filter((s) => !normalizedSlugs.has(s))
if (orphanBriefs.length) {
  console.warn(`[build-data] Brief directories without a matching company: ${orphanBriefs.join(', ')}`)
}

// --- Build full-detail brief payload ----------------------------------------

const briefsOut = {}
for (const slug of briefSlugs) {
  briefsOut[slug] = briefsByDir[slug].raw
}

// --- Copy infographics ------------------------------------------------------

for (const slug of briefSlugs) {
  if (!briefsByDir[slug].hasInfographic) continue
  const src = join(BRIEFS_DIR, slug, 'infographic.png')
  const dstDir = join(OUT_PUBLIC_BRIEFS, slug)
  mkdirSync(dstDir, { recursive: true })
  copyFileSync(src, join(dstDir, 'infographic.png'))
}

// --- Build sorted full-funding payload (per-company rounds) ----------------

const fundingOut = {}
for (const c of companies) {
  const slug = toSlug(c.name)
  if (Array.isArray(c.funding_rounds) && c.funding_rounds.length) {
    fundingOut[slug] = c.funding_rounds
  }
}

// --- Build sources payload --------------------------------------------------

const sourcesOut = {}
for (const c of companies) {
  const slug = toSlug(c.name)
  if (Array.isArray(c.sources) && c.sources.length) {
    sourcesOut[slug] = c.sources
  }
}

// --- Write outputs ----------------------------------------------------------

const meta = {
  generatedAt: new Date().toISOString(),
  totalCompanies: normalized.length,
  totalBriefs: briefSlugs.size,
  totalDigests: digestFiles.length,
}

writeFileSync(join(OUT_DATA_DIR, 'companies.generated.json'), JSON.stringify(normalized))
writeFileSync(join(OUT_DATA_DIR, 'briefs.generated.json'), JSON.stringify(briefsOut))
writeFileSync(join(OUT_DATA_DIR, 'digests.generated.json'), JSON.stringify(digests))
writeFileSync(join(OUT_DATA_DIR, 'funding.generated.json'), JSON.stringify(fundingOut))
writeFileSync(join(OUT_DATA_DIR, 'sources.generated.json'), JSON.stringify(sourcesOut))
writeFileSync(join(OUT_DATA_DIR, 'meta.generated.json'), JSON.stringify(meta))

console.log(`[build-data] ${meta.totalCompanies} companies, ${meta.totalBriefs} briefs, ${meta.totalDigests} digests`)
