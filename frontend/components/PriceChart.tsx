"use client";

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import { PricePoint } from "../lib/api";

export default function PriceChart({ data }: { data: PricePoint[] }) {
  if (!data || data.length === 0) {
    return <div className="status-box">No price history available for this symbol.</div>;
  }

  // Show every ~Nth label to avoid a cluttered x-axis (weekly data over 5yrs
  // is ~250 points - label roughly one per year).
  const tickInterval = Math.max(1, Math.floor(data.length / 6));

  return (
    <div style={{ width: "100%", height: 260 }}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#A87C1F" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#A87C1F" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#DAD5C7" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fontFamily: "JetBrains Mono, monospace", fill: "#4B5563" }}
            interval={tickInterval}
            tickFormatter={(d: string) => d.slice(0, 7)}
            axisLine={{ stroke: "#DAD5C7" }}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fontFamily: "JetBrains Mono, monospace", fill: "#4B5563" }}
            domain={["auto", "auto"]}
            axisLine={false}
            tickLine={false}
            width={56}
          />
          <Tooltip
            contentStyle={{
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 12,
              border: "1px solid #0B1220",
              borderRadius: 2,
            }}
            formatter={(value: number) => [`₹${value.toFixed(2)}`, "Adj. Close"]}
          />
          <Area
            type="monotone"
            dataKey="close"
            stroke="#0B1220"
            strokeWidth={1.5}
            fill="url(#priceFill)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
