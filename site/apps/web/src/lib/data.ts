import companiesJson from '~/data/companies.generated.json'
import briefsJson from '~/data/briefs.generated.json'
import digestsJson from '~/data/digests.generated.json'
import fundingJson from '~/data/funding.generated.json'
import sourcesJson from '~/data/sources.generated.json'
import metaJson from '~/data/meta.generated.json'

export interface Company {
  slug: string
  name: string
  aliases: string[]
  description: string
  hasBrief: boolean
  sector?: string
  region?: string
  regionFull?: string
  thesis?: string
  sourceTypes: string[]
  fundingStages: string[]
  latestFundingDate?: string
  latestFundingStage?: string
  latestSignalDate?: string
  identifiers: { linkedin?: string; crunchbase?: string }
  fundingRoundsCount: number
}

export interface FundingRound {
  date?: string | null
  stage?: string | null
  amount?: string | null
  amount_usd?: number | null
  lead_investors?: string[] | null
  investors?: string[] | null
  source?: string | null
}

export interface SourceLink {
  type: string
  label: string
  url: string
}

export interface Meta {
  generatedAt: string
  totalCompanies: number
  totalBriefs: number
  totalDigests: number
}

export const companies = companiesJson as Company[]
export const briefs = briefsJson as Record<string, string>
export const digests = digestsJson as Record<string, string>
export const fundingRoundsBySlug = fundingJson as unknown as Record<string, FundingRound[]>
export const sourcesBySlug = sourcesJson as Record<string, SourceLink[]>
export const meta = metaJson as Meta

export const companyBySlug = new Map(companies.map((c) => [c.slug, c]))

export const SECTORS = [...new Set(companies.map((c) => c.sector).filter((s): s is string => !!s))].sort()
export const REGIONS = [...new Set(companies.map((c) => c.region).filter((s): s is string => !!s))].sort()
export const STAGES = [...new Set(companies.flatMap((c) => c.fundingStages))].sort()
export const SOURCE_TYPES = [...new Set(companies.flatMap((c) => c.sourceTypes))].sort()

export const digestDates = Object.keys(digests).sort((a, b) => (a < b ? 1 : -1))
