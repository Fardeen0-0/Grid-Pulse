"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Coins,
  ExternalLink,
  FileScan,
  Leaf,
  Upload,
  Zap,
} from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  analyzeBill,
  analyzeSample,
  getLeaderboard,
  getLive,
  getUser,
  reduceLoad,
  triggerSpike,
  type BillResult,
  type LiveTelemetry,
  type Receipt,
  type UserProfile,
} from "@/lib/api";

function wattsLabel(value: number) {
  return `${Math.round(value).toLocaleString()} W`;
}

function gramsLabel(value: number) {
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(2)} kg`;
  return `${value.toFixed(1)} g`;
}

function shortKey(value: string) {
  if (value.length < 12) return value;
  return `${value.slice(0, 4)}…${value.slice(-4)}`;
}

function clock(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function Dashboard() {
  const [live, setLive] = useState<LiveTelemetry | null>(null);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [homes, setHomes] = useState<UserProfile[]>([]);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [bill, setBill] = useState<BillResult | null>(null);
  const [online, setOnline] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"spike" | "shift" | "bill" | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const clockAnchor = useRef<{ server: number; wall: number } | null>(null);
  const [clockTick, setClockTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setClockTick((value) => value + 1), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let current: AbortController | null = null;
    let misses = 0;

    const remember = (stamp: string | null | undefined) => {
      if (!stamp) return;
      const server = new Date(stamp).getTime();
      if (Number.isNaN(server)) return;
      const previous = clockAnchor.current;
      if (!previous || server > previous.server) {
        clockAnchor.current = { server, wall: Date.now() };
      }
    };

    const tick = async () => {
      if (stopped) return;
      current = new AbortController();
      const killer = setTimeout(() => current?.abort(), 4000);
      try {
        const data = await getLive(current.signal);
        clearTimeout(killer);
        if (stopped) return;
        remember(data.latest_timestamp);
        misses = 0;
        setLive(data);
        setOnline(true);
        if (data.latest_receipt) setReceipt(data.latest_receipt);
      } catch (err) {
        clearTimeout(killer);
        const aborted = err instanceof DOMException && err.name === "AbortError";
        if (stopped || aborted && current?.signal.aborted && stopped) return;
        misses += 1;
        if (!stopped && misses >= 3) setOnline(false);
      }
      if (!stopped) timer = setTimeout(tick, 1000);
    };

    void tick();
    return () => {
      stopped = true;
      current?.abort();
      if (timer) clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const refresh = async () => {
      if (stopped) return;
      try {
        const [profile, board] = await Promise.all([getUser(), getLeaderboard()]);
        if (stopped) return;
        setUser(profile.user);
        if (profile.latest_receipt) setReceipt(profile.latest_receipt);
        setHomes(board.homes);
      } catch {
        /* live telemetry already surfaces a connection problem */
      }
      if (!stopped) timer = setTimeout(refresh, 4000);
    };

    void refresh();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  const chart = useMemo(
    () =>
      (live?.raw_points ?? []).map((point) => ({
        label: clock(point.time),
        watts: Math.round(point.watts),
        carbon: Math.round(point.grid_carbon_gco2_kwh),
      })),
    [live],
  );

  async function onSpike() {
    setBusy("spike");
    setError(null);
    try {
      await triggerSpike();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Spike failed");
    } finally {
      setBusy(null);
    }
  }

  async function onShift() {
    setBusy("shift");
    setError(null);
    try {
      const next = await reduceLoad();
      setReceipt(next);
      const [profile, board] = await Promise.all([getUser(), getLeaderboard()]);
      setUser(profile.user);
      setHomes(board.homes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Payout failed");
    } finally {
      setBusy(null);
    }
  }

  async function onSample() {
    setBusy("bill");
    setError(null);
    try {
      setBill(await analyzeSample());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bill analysis failed");
    } finally {
      setBusy(null);
    }
  }

  async function onUpload(file: File | undefined) {
    if (!file) return;
    setBusy("bill");
    setError(null);
    try {
      setBill(await analyzeBill(file));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bill analysis failed");
    } finally {
      setBusy(null);
    }
  }

  function shownLiveTime(): string | null {
    void clockTick;
    const anchor = clockAnchor.current;
    if (!anchor) return null;
    return clock(new Date(anchor.server + (Date.now() - anchor.wall)).toISOString());
  }

  const liveTime = shownLiveTime();
  const peak = Boolean(live?.peak_active);
  const shifted = Boolean(live?.load_reduced);
  const carbon = live?.grid_carbon_gco2_kwh ?? 210;

  return (
    <main className="mx-auto flex min-h-screen max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:py-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.28em] text-mint">Virtual power plant</p>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight text-foam sm:text-5xl">GridPulse</h1>
          <p className="mt-2 max-w-xl text-sm text-mute sm:text-base">
            Shift load off a dirty evening peak. The grid&apos;s carbon intensity is live, and a verified
            drop pays 0.001 SOL on Devnet.
          </p>
        </div>
        <div className="rounded-2xl border border-line bg-panel/80 px-4 py-3 text-sm shadow-panel">
          <p className="font-medium text-foam">{user?.display_name ?? "Avery Chen"}</p>
          <p className="font-mono text-xs text-mute">{user?.home_id ?? "home_demo_01"}</p>
          <p className="mt-1 font-mono text-xs text-mint">
            {user?.solana_wallet ? shortKey(user.solana_wallet) : "wallet pending"} · {user?.streak_days ?? 0} day streak
          </p>
          <p className={`mt-2 font-mono text-xs ${online ? "text-mint" : "text-ember"}`}>
            {liveTime ? `Live ${liveTime}` : online ? "Telemetry link up" : "Waiting for the API"}
          </p>
        </div>
      </header>

      {peak && (
        <div className="flex items-start gap-3 rounded-2xl border border-amber/50 bg-amber/10 px-4 py-3 text-amber">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <p className="font-semibold">Peak alert · dirty grid spike</p>
            <p className="text-sm text-foam/90">
              Regional intensity is {Math.round(carbon)} gCO₂/kWh. Pause the EV charger and AC before the
              peaker plants finish spinning up.
            </p>
          </div>
        </div>
      )}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          icon={<Zap className="h-4 w-4" />}
          label="Household load"
          value={live ? wattsLabel(live.watts) : "—"}
          hint={shifted ? `${Math.round(live?.drop_pct ?? 75)}% below the 3,400 W baseline` : "Baseline 3,400 W"}
          tone={shifted ? "mint" : "foam"}
        />
        <Stat
          icon={<Activity className="h-4 w-4" />}
          label="Grid carbon"
          value={live ? `${Math.round(live.grid_carbon_gco2_kwh)}` : "—"}
          hint="gCO₂ / kWh"
          tone={peak ? "amber" : "foam"}
        />
        <Stat
          icon={<Leaf className="h-4 w-4" />}
          label="CO₂ avoided"
          value={gramsLabel(user?.total_co2_saved_grams ?? live?.co2_avoided_grams ?? 0)}
          hint="Counted from verified peak drops"
          tone="mint"
        />
        <Stat
          icon={<Coins className="h-4 w-4" />}
          label="SOL earned"
          value={(user?.total_sol_earned ?? 0).toFixed(3)}
          hint="Devnet micro-bounties"
          tone="foam"
        />
      </section>

      <section className="rounded-3xl border border-line bg-panel/85 p-4 shadow-panel sm:p-5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Live household trace</h2>
            <p className="text-sm text-mute">Watts and regional carbon, one sample per second from Tiger Data.</p>
          </div>
          <span className="hidden font-mono text-xs text-mute sm:inline">{liveTime ?? ""}</span>
        </div>
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart} margin={{ top: 10, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#234536" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: "#8eae9f", fontSize: 11 }} minTickGap={28} />
              <YAxis
                yAxisId="watts"
                domain={[0, 4200]}
                tick={{ fill: "#3dffb0", fontSize: 11 }}
                width={48}
              />
              <YAxis
                yAxisId="carbon"
                orientation="right"
                domain={[0, 800]}
                tick={{ fill: "#ffb020", fontSize: 11 }}
                width={36}
              />
              <Tooltip
                contentStyle={{ background: "#10211b", border: "1px solid #234536", borderRadius: 12 }}
                labelStyle={{ color: "#e7f6ef" }}
              />
              <Legend />
              <Line yAxisId="watts" type="monotone" dataKey="watts" name="Watts" stroke="#3dffb0" dot={false} strokeWidth={2} />
              <Line yAxisId="carbon" type="monotone" dataKey="carbon" name="gCO₂/kWh" stroke="#ffb020" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        {!!live?.peers?.length && (
          <div className="mt-4 grid gap-2 sm:grid-cols-5">
            {live.peers.map((peer) => (
              <div key={peer.home_id} className="rounded-xl border border-line/80 px-3 py-2">
                <p className="truncate text-xs text-mute">{peer.display_name}</p>
                <p className="font-mono text-sm text-foam">{wattsLabel(peer.watts)}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.3fr_0.7fr]">
        <div className="flex flex-col gap-4 rounded-3xl border border-line bg-panel/85 p-4 shadow-panel sm:p-5">
          <div>
            <h2 className="text-lg font-semibold">Dirty-grid demo</h2>
            <p className="text-sm text-mute">
              Spike the regional intensity, then shift the EV and AC off the peak. The payout is a real Devnet transfer.
            </p>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <button
              type="button"
              onClick={onSpike}
              disabled={busy !== null}
              className="rounded-xl bg-amber px-4 py-3 text-sm font-semibold text-ink transition hover:brightness-110"
            >
              {busy === "spike" ? "Raising grid carbon…" : "Trigger Dirty Grid Spike"}
            </button>
            <button
              type="button"
              onClick={onShift}
              disabled={busy !== null || !peak || shifted}
              className="rounded-xl bg-mint px-4 py-3 text-sm font-semibold text-ink transition hover:brightness-110"
            >
              {busy === "shift" ? "Confirming Devnet payout…" : "Shift Load Now (Pause EV & AC)"}
            </button>
          </div>
          {error && <p className="rounded-xl border border-ember/40 bg-ember/10 px-3 py-2 text-sm text-ember">{error}</p>}
          {receipt && (
            <a
              href={receipt.solscan_url}
              target="_blank"
              rel="noreferrer"
              className="block rounded-2xl border border-mint/40 bg-mint/10 p-4 transition hover:border-mint"
            >
              <div className="flex items-center justify-between gap-3">
                <p className="font-semibold text-mint">Solscan Devnet Receipt</p>
                <ExternalLink className="h-4 w-4 text-mint" />
              </div>
              <p className="mt-2 font-mono text-sm text-foam">
                {receipt.sol_amount.toFixed(3)} SOL · {Math.round(receipt.drop_pct)}% load drop ·{" "}
                {gramsLabel(receipt.co2_avoided_grams)} CO₂ avoided
              </p>
              <p className="mt-1 break-all font-mono text-xs text-mute">{receipt.signature}</p>
              <p className="mt-2 text-xs text-mute">
                {Math.round(receipt.baseline_watts)} W → {Math.round(receipt.reduced_watts)} W at{" "}
                {Math.round(receipt.carbon_gco2_kwh)} gCO₂/kWh
              </p>
            </a>
          )}
          {!!live?.cagg?.length && (
            <div>
              <h3 className="text-sm font-medium text-foam">1-minute continuous aggregate</h3>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-left text-xs text-mute">
                  <thead>
                    <tr className="font-mono uppercase tracking-wide">
                      <th className="py-1 pr-3">Bucket</th>
                      <th className="py-1 pr-3">Avg W</th>
                      <th className="py-1 pr-3">gCO₂</th>
                      <th className="py-1">kWh</th>
                    </tr>
                  </thead>
                  <tbody>
                    {live.cagg.slice(-5).map((row) => (
                      <tr key={row.bucket} className="border-t border-line/70 font-mono text-foam">
                        <td className="py-1 pr-3">{clock(row.bucket)}</td>
                        <td className="py-1 pr-3">{Math.round(row.avg_watts)}</td>
                        <td className="py-1 pr-3">{Math.round(row.avg_carbon)}</td>
                        <td className="py-1">{row.kwh_used.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        <div className="rounded-3xl border border-line bg-panel/85 p-4 shadow-panel sm:p-5">
          <h2 className="text-lg font-semibold">Neighborhood board</h2>
          <p className="text-sm text-mute">CO₂ avoided across the campus challenge.</p>
          <ol className="mt-4 flex flex-col gap-2">
            {homes.map((home, index) => (
              <li key={home.home_id} className="flex items-center justify-between gap-3 rounded-xl border border-line/80 px-3 py-2">
                <div>
                  <p className="text-sm text-foam">
                    <span className="mr-2 font-mono text-mute">{index + 1}</span>
                    {home.display_name}
                  </p>
                  <p className="font-mono text-xs text-mute">{home.streak_days} day streak</p>
                </div>
                <div className="text-right">
                  <p className="font-mono text-sm text-mint">{gramsLabel(home.total_co2_saved_grams)}</p>
                  <p className="font-mono text-xs text-mute">{home.total_sol_earned.toFixed(3)} SOL</p>
                </div>
              </li>
            ))}
            {!homes.length && <li className="text-sm text-mute">Leaderboard loads with the Atlas profiles.</li>}
          </ol>
        </div>
      </section>

      <section className="rounded-3xl border border-line bg-panel/85 p-4 shadow-panel sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold">Utility bill analyzer</h2>
            <p className="text-sm text-mute">Gemini Vision reads the bill and returns three load-shifting tips.</p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              onClick={onSample}
              disabled={busy !== null}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-mint/50 px-4 py-2 text-sm font-medium text-mint"
            >
              <FileScan className="h-4 w-4" />
              {busy === "bill" ? "Reading the bill…" : "Analyze sample bill"}
            </button>
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={busy !== null}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-line px-4 py-2 text-sm text-foam"
            >
              <Upload className="h-4 w-4" />
              Upload a bill
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="hidden"
              onChange={(event) => onUpload(event.target.files?.[0])}
            />
          </div>
        </div>
        <div className="mt-4 grid gap-4 lg:grid-cols-[280px_1fr]">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/sample_bill.png"
            alt="Pioneer Valley Electric residential statement"
            className="h-auto max-h-[720px] w-full rounded-2xl border border-line bg-white object-contain"
          />
          <div>
            {bill ? (
              <div>
                <p className="text-sm text-mute">
                  {bill.analysis.utility_name} · ${bill.analysis.kwh_rate_usd.toFixed(4)}/kWh ·{" "}
                  {Math.round(bill.analysis.monthly_kwh)} kWh · {bill.analysis.peak_window}
                </p>
                <p className="mt-1 font-mono text-xs text-mute">model {bill.model}</p>
                <ul className="mt-3 grid gap-2">
                  {bill.analysis.tips.map((tip) => (
                    <li key={tip.appliance} className="rounded-xl border border-line px-3 py-2">
                      <p className="font-medium text-foam">{tip.appliance}</p>
                      <p className="text-sm text-mute">{tip.action}</p>
                      <p className="mt-1 font-mono text-xs text-mint">
                        {gramsLabel(tip.monthly_co2_saved_grams)} / month · ${tip.monthly_savings_usd.toFixed(2)}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-sm text-mute">
                The sample statement is Pioneer Valley Electric, $0.1842/kWh, 742 kWh, peak 4:00 PM–9:00 PM.
                Analyze it to store the structured plan in MongoDB Atlas.
              </p>
            )}
          </div>
        </div>
      </section>

      <footer className="pb-4 text-center font-mono text-[11px] uppercase tracking-[0.18em] text-mute">
        Tiger Data · MongoDB Atlas · Gemini · Solana Devnet · Vultr · GoDaddy
      </footer>
    </main>
  );
}

function Stat({
  icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint: string;
  tone: "mint" | "amber" | "foam";
}) {
  const color = tone === "mint" ? "text-mint" : tone === "amber" ? "text-amber" : "text-foam";
  return (
    <article className="rounded-2xl border border-line bg-panel/85 p-4 shadow-panel">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-mute">
        {icon}
        {label}
      </div>
      <p className={`mt-2 font-mono text-2xl ${color}`}>{value}</p>
      <p className="mt-1 text-xs text-mute">{hint}</p>
    </article>
  );
}
