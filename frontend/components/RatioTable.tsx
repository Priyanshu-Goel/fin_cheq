import { RatioTrend } from "../lib/api";

export default function RatioTable({ ratios }: { ratios: RatioTrend[] }) {
  const allYears = Array.from(
    new Set(ratios.flatMap((r) => Object.keys(r.values_by_year)))
  ).sort();

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Metric</th>
          {allYears.map((y) => (
            <th key={y}>{y}</th>
          ))}
          <th>Trend</th>
        </tr>
      </thead>
      <tbody>
        {ratios.map((r) => (
          <tr key={r.metric}>
            <td>{r.metric}</td>
            {allYears.map((y) => (
              <td key={y}>{r.values_by_year[y] ?? "—"}</td>
            ))}
            <td>
              <span className={`pill ${r.trend}`}>{r.trend}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
