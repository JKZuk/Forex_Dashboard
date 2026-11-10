// Response shapes of the local REST API (see docs/API.md, backend/app/api/schemas.py).

export type Horizon = string; // "short" | "medium" | "long" - list comes from the API

export interface Region {
  code: string;
  name: string;
  currency: string;
  group: string;
  core: boolean;
  trade_sensitive: boolean;
  scored: boolean;
  fx_series_id: string;
}

export interface HorizonScore {
  horizon: Horizon;
  score: number;
  label: string;
  coverage: number;
  no_data: boolean;
  as_of: string;
  change_1w: number | null;
  previous_as_of: string | null;
}

export interface PriceSnapshot {
  series_id: string;
  name: string;
  date: string;
  value: number;
  change_1d_pct: number | null;
  change_1w_pct: number | null;
  change_1m_pct: number | null;
}

export interface OverviewRegion {
  region: Region;
  as_of: string | null;
  scores: Record<Horizon, HorizonScore>;
  headline: string | null;
  price: PriceSnapshot | null;
}

export interface Overview {
  has_data: boolean;
  as_of: string | null;
  horizons: Horizon[];
  regions: OverviewRegion[];
}

export interface Component {
  component: string;
  label: string;
  raw_display: string;
  signal: number | null;
  weight: number;
  contribution: number;
  detail: string;
  has_data: boolean;
}

export interface HorizonPanel {
  horizon: Horizon;
  score: HorizonScore | null;
  components: Component[];
}

export interface SeriesPoint {
  date: string;
  value: number;
}

export interface IndicatorRow {
  series_id: string;
  indicator: string;
  name: string;
  category: string;
  unit: string;
  frequency: string;
  source: string;
  note: string;
  bullish_when: string | null;
  impact: number | null;
  latest_date: string;
  latest_value: number;
  previous_date: string | null;
  previous_value: number | null;
  change: number | null;
  spark: SeriesPoint[];
}

export interface Series {
  series_id: string;
  found: boolean;
  region: string | null;
  indicator: string | null;
  name: string | null;
  category: string | null;
  unit: string;
  frequency: string;
  source: string;
  note: string;
  last_updated: string | null;
  points: SeriesPoint[];
}

export interface NewsSentiment {
  region: string;
  score: number;
}

export interface NewsItem {
  id: string;
  source: string;
  kind: string;
  title: string;
  summary: string;
  url: string;
  published_at: string;
  sentiment: number;
  engine: string;
  region_score: number | null;
  regions: NewsSentiment[];
}

export interface NewsFeed {
  total: number;
  limit: number;
  offset: number;
  items: NewsItem[];
}

export interface CalendarEvent {
  id: string;
  region: string;
  title: string;
  event_time: string;
  time_known: boolean;
  impact: string;
  actual: string;
  forecast: string;
  previous: string;
  source: string;
}

export interface Calendar {
  start: string;
  end: string;
  total: number;
  events: CalendarEvent[];
}

export interface RegionDetail {
  region: Region;
  has_data: boolean;
  as_of: string | null;
  horizons: HorizonPanel[];
  conclusion: { as_of: string; headline: string; text: string } | null;
  price: PriceSnapshot | null;
  indicators: IndicatorRow[];
  trade: Series[];
  news: NewsItem[];
  events: CalendarEvent[];
}

export interface ScoreHistory {
  region: string;
  series: Record<Horizon, { date: string; score: number; coverage: number }[]>;
}

export interface PriceHistory {
  region: string;
  currency: string;
  series_id: string;
  name: string;
  found: boolean;
  points: SeriesPoint[];
}

export interface HeatmapCurrency {
  region: string;
  currency: string;
  name: string;
  group: string;
  scored: boolean;
  as_of: string | null;
  scores: Record<Horizon, number | null>;
  labels: Record<Horizon, string | null>;
}

export interface PairIdea {
  pair: string;
  direction: "long" | "short";
  strong: string;
  weak: string;
  differential: number;
}

export interface Heatmap {
  has_data: boolean;
  as_of: string | null;
  horizons: Horizon[];
  currencies: HeatmapCurrency[];
  pairs: Record<Horizon, PairIdea[]>;
}

export interface CotRow {
  currency: string;
  region: string | null;
  report_date: string;
  noncomm_long: number;
  noncomm_short: number;
  net: number;
  open_interest: number;
  net_pct_oi: number;
  change_1w: number | null;
  change_4w: number | null;
  pct_oi_change_1w: number | null;
  pct_oi_change_4w: number | null;
  percentile_3y: number | null;
  percentile_weeks: number;
  extreme: "long" | "short" | null;
  spark: SeriesPoint[];
}

export interface CotSummary {
  has_data: boolean;
  report_date: string | null;
  rows: CotRow[];
}

export interface CotHistory {
  currency: string;
  points: {
    date: string;
    noncomm_long: number;
    noncomm_short: number;
    net: number;
    open_interest: number;
    net_pct_oi: number;
  }[];
}

export interface Gauge {
  series_id: string;
  name: string;
  unit: string;
  date: string;
  value: number;
  change_1d_pct: number | null;
  change_1w_pct: number | null;
  change_1m_pct: number | null;
}

export interface RefreshJob {
  job: string;
  status: string; // running | ok | error | never | interrupted
  started_at: string | null;
  finished_at: string | null;
  rows: number;
  message: string;
  last_success_at: string | null;
}

export interface RefreshStatus {
  running: boolean;
  server_time: string;
  last_success_at: string | null;
  last_finished_at: string | null;
  sources: string[];
  jobs: RefreshJob[];
  auto_refresh_minutes: number;
}
