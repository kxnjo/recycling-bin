import { useState, useEffect, useRef } from "react";

const API_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/bin"; // TODO: change to your Flask server IP if needed
const POLL_INTERVAL = 3000; // ms
const HISTORY_URL =
  "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/history";

const bins = [
  {
    label: "General Waste",
    key: "bin_a",
    flaskKey: "a",
    color: "#3B82F6",
    icon: "🗑️",
  },
  {
    label: "Plastic",
    key: "bin_b",
    flaskKey: "b",
    color: "#10B981",
    icon: "♻️",
  },
  { label: "Paper", key: "bin_c", flaskKey: "c", color: "#F43F5E", icon: "📄" },
];

function getEventType(val) {
  if (val >= 90) return "critical";
  if (val >= 70) return "warning";
  return "info";
}

function formatTime(ts) {
  const d = ts ? new Date(ts * 1000) : new Date();
  return d.toLocaleString("en-SG", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

const INITIAL_ACTIVITY = [];

const SYSTEM_CHECKS = [
  { label: "MQTT Broker", status: "online" },
  { label: "Sensor — Bin A", status: "online" },
  { label: "Sensor — Bin B", status: "online" },
  { label: "Sensor — Bin C", status: "warning" },
  { label: "YOLOv8 Model", status: "online" },
  { label: "Database", status: "online" },
];

function BinCard({ bin, val }) {
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    let cur = 0;
    const step = val / 35;
    const t = setInterval(() => {
      cur += step;
      if (cur >= val) {
        setDisplay(val);
        clearInterval(t);
      } else setDisplay(Math.round(cur));
    }, 18);
    return () => clearInterval(t);
  }, [val]);

  let statusLabel, statusClass;
  if (val < 70) {
    statusLabel = "NORMAL";
    statusClass = "bg-emerald-50 text-emerald-700 ring-emerald-200";
  } else if (val < 90) {
    statusLabel = "NEARLY FULL";
    statusClass = "bg-amber-50 text-amber-700 ring-amber-200";
  } else {
    statusLabel = "CRITICAL";
    statusClass = "bg-rose-50 text-rose-600 ring-rose-200";
  }

  return (
    <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-100 hover:-translate-y-1 hover:shadow-md transition-all duration-200">
      <div className="flex justify-between items-start mb-5">
        <div className="w-11 h-11 rounded-xl bg-gray-50 flex items-center justify-center text-2xl">
          {bin.icon}
        </div>
        <span
          className={`text-[10px] font-bold tracking-widest px-3 py-1 rounded-full ring-1 ${statusClass}`}
        >
          {statusLabel}
        </span>
      </div>

      <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-1">
        {bin.label}
      </p>

      <div className="flex items-end gap-1 mb-5">
        <span
          className="text-5xl font-extrabold text-gray-800 leading-none"
          // style={{ fontFamily: "'DM Mono', monospace" }}
        >
          {display}
        </span>
        <span className="text-lg text-gray-300 mb-1">%</span>
      </div>

      <div className="w-full h-2.5 bg-gray-100 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700 ease-out"
          style={{
            width: `${display}%`,
            background: `linear-gradient(90deg, ${bin.color}bb, ${bin.color})`,
          }}
        />
      </div>
    </div>
  );
}

export default function App() {
  const [binData, setBinData] = useState({ bin_a: 0, bin_b: 0, bin_c: 0 });
  const [lastIdentified, setLastIdentified] = useState("—");
  const [activity, setActivity] = useState(INITIAL_ACTIVITY);
  const [apiStatus, setApiStatus] = useState("connecting"); // "online" | "offline" | "connecting"
  const [time, setTime] = useState("");
  const prevBinData = useRef({ bin_a: 0, bin_b: 0, bin_c: 0 });
  const activityIdRef = useRef(1);
  const [history, setHistory] = useState([]);
  const [inferenceId, setInferenceId] = useState("—");
  const [online, setOnline] = useState(false);
  // Clock
  useEffect(() => {
    const tick = () =>
      setTime(
        new Date().toLocaleTimeString("en-SG", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }),
      );
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);

  // Fetch historical data on mount
  useEffect(() => {
    const fetchHistory = async () => {
      try {
        const res = await fetch(HISTORY_URL);
        const data = await res.json();
        setHistory(data);
      } catch (err) {
        console.error("Failed to fetch history:", err);
      }
    };

    fetchHistory();
    const t = setInterval(fetchHistory, 10000); // refresh every 10s
    return () => clearInterval(t);
  }, []);

  const getBinFillPercent = (distance) => {
    const maxHeight = 20; // cm
    const fill = maxHeight - distance;
    const percentage = (fill / maxHeight) * 100;

    return Number(percentage.toFixed(2));
  };

  // Poll Flask API
  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await fetch(API_URL);
        console.log("Response status:", res.status);

        if (!res.ok) throw new Error("Not OK");
        const data = await res.json();
        setApiStatus("online");
        if (data.inference_id) {
          setInferenceId(data.inference_id);
        }
        if (data)
        {
          setOnline(true);
        }
        // setOnline(data.message === "online");
        const newBinData = {
          bin_a: getBinFillPercent(data.a) ?? 0,
          bin_b: getBinFillPercent(data.b) ?? 0,
          bin_c: getBinFillPercent(data.c) ?? 0,
        };

        // Log every incoming message — use timestamp to deduplicate
        // so we don't double-log if we poll faster than the backend sends
        const incomingTs = data.timestamp ?? null;
        const isDuplicate =
          incomingTs && incomingTs === prevBinData.current._lastTs;

        if (!isDuplicate) {
          const newEvents = bins.map((bin) => {
            const val = newBinData[bin.key];
            const type = getEventType(val);
            const msgMap = {
              critical: `Bin reached CRITICAL level (${val}%)`,
              warning: `Bin reached NEARLY FULL (${val}%)`,
              info: `Bin level updated (${val}%)`,
            };
            return {
              id: activityIdRef.current++,
              time: formatTime(incomingTs),
              bin: bin.label,
              icon: bin.icon,
              msg: msgMap[type],
              type,
            };
          });
          setActivity((prev) => [...newEvents, ...prev].slice(0, 30));
        }

        // Update last identified
        if (data.label) setLastIdentified(data.label);

        prevBinData.current = { ...newBinData, _lastTs: incomingTs };
        setBinData(newBinData);
      } catch {
        setApiStatus("offline");
      }
    };

    fetchData();
    const t = setInterval(fetchData, POLL_INTERVAL);
    return () => clearInterval(t);
  }, []);

  return (
    <div
      className="min-h-screen bg-gray-50 px-10 py-10"
      style={{ fontFamily: "'DM Sans', sans-serif" }}
    >
      <link
        href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500;700&display=swap"
        rel="stylesheet"
      />

      {/* ── Header ── */}
      <div className="flex justify-between items-start mb-8">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <span className="text-3xl">♻️</span>
            <h1 className="text-2xl font-extrabold text-gray-800 tracking-tight">
              Smart Waste System
            </h1>
          </div>
          <p className="text-sm text-gray-400 ml-12">
            Real-time Waste Management & Analytics Dashboard
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div
            className={`flex items-center gap-2 rounded-xl px-4 py-2 border ${
              online
                ? "bg-emerald-50 border-emerald-100"
                : "bg-rose-50 border-rose-100"
            }`}
          >
            {/* Dot */}
            <span
              className={`w-2.5 h-2.5 rounded-full ${
                online ? "bg-emerald-500" : "bg-rose-500"
              }`}
            />
            {/* Text */}
            <span className="text-xs font-semibold text-gray-700">
              {online ? "Online" : "Offline"}
            </span>
          </div>
          <div className="bg-white border border-gray-100 rounded-xl px-4 py-2 shadow-sm">
            <p className="text-[10px] text-gray-400 uppercase tracking-widest mb-0.5">
              Time
            </p>
            <p
              className="text-base font-bold text-gray-700"
              style={{ fontFamily: "'DM Mono', monospace" }}
            >
              {time}
            </p>
          </div>
        </div>
      </div>

      <div className="h-px bg-gray-100 mb-8" />

      {/* ── Live Capacity ── */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
          <p className="text-[11px] font-bold text-gray-400 uppercase tracking-widest">
            Live Bin Capacity
          </p>
        </div>
        <div className="grid grid-cols-3 gap-5">
          {bins.map((bin) => (
            <BinCard key={bin.key} bin={bin} val={binData[bin.key] ?? 0} />
          ))}
        </div>
      </div>

      <div className="h-px bg-gray-100 mb-8" />

      {/* ── Activity & Classification ── */}
      <div>
        <div className="flex items-center gap-2 mb-5">
          <span className="w-1.5 h-1.5 rounded-full bg-violet-500" />
          <p className="text-[11px] font-bold text-gray-400 uppercase tracking-widest">
            Activity & Classification
          </p>
        </div>

        <div className="grid grid-cols-3 gap-5">
          {/* Recent Activity Log — spans 2 cols */}
          <div className="col-span-2 bg-white rounded-2xl border border-gray-100 shadow-sm p-6">
            <div className="flex justify-between items-center mb-5">
              <div>
                <h2 className="text-sm font-bold text-gray-700">
                  Recent Activity
                </h2>
                <p className="text-xs text-gray-400 mt-0.5">
                  Latest bin events &amp; alerts
                </p>
              </div>
              <span
                className="text-[10px] font-bold text-gray-400 uppercase tracking-widest"
                style={{ fontFamily: "'DM Mono', monospace" }}
              >
                Today
              </span>
            </div>
            <div
              className="flex flex-col gap-2 overflow-y-auto"
              style={{ maxHeight: "360px" }}
            >
              {activity.map((event) => {
                const typeStyles = {
                  critical: {
                    dot: "bg-rose-500",
                    row: "bg-rose-50 border-rose-100",
                    text: "text-rose-700",
                  },
                  warning: {
                    dot: "bg-amber-400",
                    row: "bg-amber-50 border-amber-100",
                    text: "text-amber-700",
                  },
                  success: {
                    dot: "bg-emerald-500",
                    row: "bg-emerald-50 border-emerald-100",
                    text: "text-emerald-700",
                  },
                  info: {
                    dot: "bg-blue-400",
                    row: "bg-blue-50 border-blue-100",
                    text: "text-blue-700",
                  },
                };
                const s = typeStyles[event.type];
                return (
                  <div
                    key={event.id}
                    className={`flex items-center gap-4 px-4 py-3 rounded-xl border ${s.row}`}
                  >
                    <span
                      className={`w-2 h-2 rounded-full flex-shrink-0 ${s.dot}`}
                    />
                    <span className="text-lg flex-shrink-0">{event.icon}</span>
                    <div className="flex-1 min-w-0">
                      <p className={`text-xs font-semibold ${s.text}`}>
                        {event.bin}
                      </p>
                      <p className="text-xs text-gray-500 truncate">
                        {event.msg}
                      </p>
                    </div>
                    <span className="text-[11px] flex-shrink-0">
                      {event.time}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Waste Classification */}
          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6 flex flex-col gap-4">
            <div>
              <h2 className="text-sm font-bold text-gray-700">
                Waste Classification
              </h2>
              <p className="text-xs text-gray-400 mt-0.5">Latest Result</p>
            </div>

            <div className="bg-gray-50 rounded-xl p-4">
              <p className="text-[10px] text-gray-400 uppercase tracking-widest mb-2">
                Last Identified
              </p>
              <div className="flex items-end gap-1 mb-3">
                <span className="text-4xl font-extrabold text-gray-800 leading-none">
                  {lastIdentified}
                </span>
              </div>
            </div>

            <div className="bg-gray-50 rounded-xl p-4">
              <p className="text-[10px] text-gray-400 uppercase tracking-widest mb-2">
                Inference By:
              </p>
              <div
                className="bg-white border border-gray-100 rounded-lg px-3 py-2 text-sm font-semibold text-gray-600"
                // style={{ fontFamily: "'DM Mono', monospace" }}
              >
                {/* Local Model (Pi 2) */}
                {inferenceId}
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="h-px bg-gray-100 mb-8 mt-8" />

      {/* ── History Table ── */}
      <div>
        <div className="flex items-center gap-2 mb-5">
          <span className="w-1.5 h-1.5 rounded-full bg-orange-500" />
          <p className="text-[11px] font-bold text-gray-400 uppercase tracking-widest">
            Full History
          </p>
        </div>

        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] font-bold text-gray-400 uppercase tracking-widest border-b border-gray-100">
                <th className="pb-3">Date Time</th>
                <th className="pb-3">Label</th>
                <th className="pb-3">Bin A</th>
                <th className="pb-3">Bin B</th>
                <th className="pb-3">Bin C</th>
              </tr>
            </thead>
            <tbody>
              {history.map((row, i) => (
                <tr
                  key={i}
                  className="border-b border-gray-50 hover:bg-gray-50 transition-colors"
                >
                  <td className="py-3 text-gray-500 font-mono text-xs">
                    {formatTime(row.timestamp)}
                  </td>
                  <td className="py-3">
                    <span className="bg-blue-50 text-blue-700 text-[10px] font-bold px-2 py-1 rounded-full">
                      {row.label || "—"}
                    </span>
                  </td>
                  <td className="py-3 text-gray-700 font-semibold">{row.a}%</td>
                  <td className="py-3 text-gray-700 font-semibold">{row.b}%</td>
                  <td className="py-3 text-gray-700 font-semibold">{row.c}%</td>
                </tr>
              ))}
              {history.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="py-8 text-center text-gray-400 text-xs"
                  >
                    No history yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
