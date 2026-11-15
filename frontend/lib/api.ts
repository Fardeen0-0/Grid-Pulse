export type RawPoint = {
  time: string;
  home_id: string;
  watts: number;
  grid_carbon_gco2_kwh: number;
  is_peak_event: boolean;
};

export type CaggPoint = {
  bucket: string;
  home_id: string;
  avg_watts: number;
  avg_carbon: number;
  kwh_used: number;
};

export type Peer = {
  home_id: string;
  display_name: string;
  watts: number;
  grid_carbon_gco2_kwh: number;
  time: string;
};

export type Receipt = {
  home_id: string;
  drop_pct: number;
  co2_avoided_grams: number;
  sol_amount: number;
  signature: string;
  solscan_url: string;
  from_wallet: string;
  to_wallet: string;
  baseline_watts: number;
  reduced_watts: number;
  carbon_gco2_kwh: number;
  window_seconds: number;
};

export type LiveTelemetry = {
  home_id: string;
  server_time: string;
  latest_timestamp: string | null;
  watts: number;
  grid_carbon_gco2_kwh: number;
  peak_active: boolean;
  load_reduced: boolean;
  baseline_watts: number;
  reduced_watts: number;
  drop_pct: number;
  co2_avoided_grams: number;
  raw_points: RawPoint[];
  cagg: CaggPoint[];
  peers: Peer[];
  latest_receipt: Receipt | null;
};

export type UserProfile = {
  home_id: string;
  display_name: string;
  solana_wallet: string;
  total_co2_saved_grams: number;
  total_sol_earned: number;
  streak_days: number;
};

export type ShiftTip = {
  appliance: string;
  action: string;
  monthly_co2_saved_grams: number;
  monthly_savings_usd: number;
};

export type BillResult = {
  id?: string;
  model?: string;
  source?: string;
  analysis: {
    utility_name: string;
    kwh_rate_usd: number;
    monthly_kwh: number;
    peak_window: string;
    tips: ShiftTip[];
  };
};

async function parseError(response: Response): Promise<string> {
  const data = await response.json().catch(() => ({}));
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item)))
      .join(", ");
  }
  return `Request failed (${response.status})`;
}

function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host === "localhost" || host === "127.0.0.1") return "http://127.0.0.1:8000";
  }
  return "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase()}${path}`, { cache: "no-store", ...init });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return (await response.json()) as T;
}

export function getLive(signal?: AbortSignal) {
  return request<LiveTelemetry>("/api/telemetry/live", { signal });
}

export function getLeaderboard() {
  return request<{ homes: UserProfile[] }>("/api/leaderboard");
}

export function getUser(homeId = "home_demo_01") {
  return request<{ user: UserProfile; latest_receipt: Receipt | null }>(`/api/user/${homeId}`);
}

export function triggerSpike() {
  return request<LiveTelemetry>("/api/demo/trigger-spike", { method: "POST" });
}

export function reduceLoad() {
  return request<Receipt>("/api/demo/reduce-load", { method: "POST" });
}

export function analyzeSample() {
  return request<BillResult>("/api/gemini/analyze-sample", { method: "POST" });
}

export function analyzeBill(file: File) {
  const body = new FormData();
  body.append("file", file);
  return request<BillResult>("/api/gemini/analyze-bill", { method: "POST", body });
}
