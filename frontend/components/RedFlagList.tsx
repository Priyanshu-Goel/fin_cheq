import { RedFlag } from "../lib/api";

export default function RedFlagList({ flags }: { flags: RedFlag[] }) {
  return (
    <div>
      {flags.map((f, i) => (
        <div key={i} className={`flag sev-${f.severity}`}>
          <div className="flag-title">
            <span className={`pill sev-${f.severity}`} style={{ marginRight: 8 }}>
              {f.severity}
            </span>
            {f.title}
          </div>
          <div className="flag-detail">{f.detail}</div>
        </div>
      ))}
    </div>
  );
}
