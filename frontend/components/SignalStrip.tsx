import { AnalyzeResponse } from "../lib/api";

export default function SignalStrip({ data }: { data: AnalyzeResponse }) {
  const pct = (n: number) => `${(n * 100).toFixed(1)}%`;

  return (
    <div className="signal-strip">
      <div className="signal-cell">
        <div className="label">Risk Grade</div>
        <div className="value">{data.risk.risk_grade}</div>
      </div>
      <div className="signal-cell">
        <div className="label">Beta</div>
        <div className="value">{data.capm.beta.toFixed(2)}</div>
      </div>
      <div className="signal-cell">
        <div className="label">Cost of Equity (CAPM)</div>
        <div className="value">{pct(data.capm.cost_of_equity)}</div>
      </div>
      <div className="signal-cell">
        <div className="label">Sharpe Ratio</div>
        <div className="value">{data.risk.sharpe_ratio.toFixed(2)}</div>
      </div>
      <div className="signal-cell">
        <div className="label">Max Drawdown</div>
        <div className={`value negative`}>{pct(data.risk.max_drawdown)}</div>
      </div>
      <div className="signal-cell">
        <div className="label">Backtest Hit ({data.backtest.as_of_date})</div>
        <div className={`value ${data.backtest.directional_hit ? "positive" : "negative"}`}>
          {data.backtest.directional_hit ? "Correct" : "Missed"}
        </div>
      </div>
    </div>
  );
}
