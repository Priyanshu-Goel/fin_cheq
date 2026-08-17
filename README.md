# AI Equity Research Assistant

Ingests financials + price history for any BSE/NSE listed company, runs a
JPM/Nomura-style quantitative analysis (ratios, CAPM, risk, red flags), retrieves
context from ingested annual reports / earnings-call transcripts, and generates
a downloadable **Excel workbook** and **PDF research note** — plus a backtest of
the model's own signal accuracy.

---

## 1. Architecture

```
┌─────────────────┐        ┌──────────────────────────────┐        ┌───────────────┐
│   Frontend       │  HTTP  │   Backend (FastAPI)          │  SQL   │   Supabase     │
│   Next.js        │ ─────► │   Render.com                 │ ─────► │ Postgres +     │
│   Vercel         │ ◄───── │                               │ ◄───── │ pgvector       │
└─────────────────┘        │  ┌─────────────────────────┐  │        └───────────────┘
                            │  │ data_sources/           │  │
                            │  │  - yfinance (prices)    │──┼──► Yahoo Finance (free)
                            │  │  - indianapi.in client  │──┼──► Fundamentals (cheap paid API)
                            │  │  - screener_scraper.py  │──┼──► Screener.in (free fallback)
                            │  │  - reports_fetcher.py   │──┼──► Annual reports / transcripts
                            │  └─────────────────────────┘  │
                            │  ┌─────────────────────────┐  │
                            │  │ analysis/               │  │
                            │  │  ratios, CAPM, risk,     │  │
                            │  │  red_flags, backtest     │  │
                            │  └─────────────────────────┘  │
                            │  ┌─────────────────────────┐  │
                            │  │ rag/                    │  │
                            │  │  chunk → embed → store   │──┼──► CometAPI (Claude proxy)
                            │  │  → retrieve → generate   │  │    (Claude Haiku - cheapest)
                            │  └─────────────────────────┘  │
                            │  ┌─────────────────────────┐  │
                            │  │ reports/                │  │
                            │  │  excel_report.py         │  │
                            │  │  pdf_report.py           │  │
                            │  └─────────────────────────┘  │
                            └───────────────────────────────┘
```

## 2. Why this stack (read before you change anything)

**There is no official, cheap, unified API for NSE/BSE + Screener data.** The
options are:

| Source | Cost | What it gives | Caveat |
|---|---|---|---|
| `yfinance` (Yahoo Finance) | Free | 5-yr+ daily OHLC for any `TICKER.NS` / `TICKER.BO` | Unofficial, occasionally rate-limited, but very stable in practice |
| `indianapi.in` (RapidAPI: "Indian Stock Market API") | Free tier, then ~₹500+/mo | Screener-style ratios, financials, key metrics, shareholding, analyst views | Paid tiers needed once you exceed free-tier request quota |
| Scraping Screener.in directly | Free | Same ratios, more manual | Fragile to layout changes; heavier maintenance; respect their robots.txt/ToS and rate-limit yourself |
| NSE/BSE official sites | Free | Corporate announcements, annual report PDFs | No REST API — HTML scraping only, and both sites aggressively rate-limit/bot-block |

**Decision made in this codebase:** use `yfinance` for price history (always
free, no key needed), try `indianapi.in` first for fundamentals (clean JSON,
cheap), and fall back to the Screener scraper automatically if the API key
isn't configured or a request fails. This gives you a working pipeline on
day one with **zero cost**, and a clean upgrade path (just add one API key)
once you want fewer scraping headaches.

## 3. What "backtesting" means here

For every company, the pipeline re-runs its own logic as of `N` years ago
(default: 3 years ago) using only data available at that point, generates the
same buy/hold/sell-style signal and CAPM-implied expected return, then compares
that to what **actually** happened to the stock price and fundamentals since.
It reports:

- **Directional hit-rate** — % of past signals that got the direction right
- **MAE / RMSE** of the CAPM-implied expected return vs actual realized return
- A per-company backtest sheet in the Excel output, and an aggregate accuracy
  score across companies you've run, stored in Supabase so it improves in
  visibility over time.

This is a legitimate, standard way to backtest a research signal — it is
**not** the same as backtesting a trading strategy's P&L, and the README/report
says so explicitly to avoid overstating what the numbers mean.

## 4. Local setup

### Backend
```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env    # fill in the values, see section 6
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

Visit http://localhost:3000, type a company name (e.g. "Infosys" or "TCS"),
and the app calls the backend, which fetches data, runs analysis, generates
the RAG note, and returns download links for the Excel + PDF.

## 5. Deploying (for beginners) — Vercel + Render + Supabase, all free tiers to start

### Step 1 — Supabase (database)
1. Go to supabase.com → New Project. Pick a region close to India (Singapore).
2. In the SQL editor, run:
   ```sql
   create extension if not exists vector;
   create table if not exists documents (
     id bigserial primary key,
     company_ticker text not null,
     source text,
     content text,
     embedding vector(384)
   );
   create table if not exists backtest_results (
     id bigserial primary key,
     company_ticker text,
     run_date date,
     predicted_return numeric,
     actual_return numeric,
     hit boolean
   );
   create or replace function match_documents(
     query_embedding vector(384), match_ticker text, match_count int
   ) returns table(content text, similarity float)
   language sql stable as $$
     select content, 1 - (embedding <=> query_embedding) as similarity
     from documents
     where company_ticker = match_ticker
     order by embedding <=> query_embedding
     limit match_count;
   $$;
   ```
3. Copy your Project URL and `service_role` key (Settings → API) into `.env`.

### Step 2 — Render (backend)
1. Push this repo to GitHub.
2. render.com → New → Web Service → connect the repo → root directory `backend`.
3. Build command: `pip install -r requirements.txt`
   Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables from `.env.example` in the Render dashboard
   (Supabase URL/key, CometAPI key, optional Indian API key).
5. Deploy. You'll get a URL like `https://your-app.onrender.com`.
   Note: Render's free tier sleeps after inactivity — first request after
   idle takes ~30-50s to wake up. Fine for a personal project; upgrade to a
   paid instance later if you need it always warm.

### Step 3 — Vercel (frontend)
1. vercel.com → New Project → import the same GitHub repo → root directory
   `frontend`.
2. Add environment variable `NEXT_PUBLIC_API_URL` = your Render URL from Step 2.
3. Deploy. Vercel gives you a live URL immediately.

### Step 4 (optional) — scheduled ingestion
Render supports **Cron Jobs** (a separate service type) — you can add one
that hits `/refresh-daily-prices` on a schedule so price data stays current
without the user needing to trigger it manually.

## 6. Environment variables (`.env.example`)
See the `.env.example` file in this repo root — copy it into `backend/.env`.

## 7. Frontend notes
- Pinned to **Next.js 16.3.1 / React 19.2.4** deliberately — Next.js 14.x is
  past end-of-life (EOL Oct 2025) and had several patched RCE-class CVEs in
  late 2025/early 2026. Don't downgrade without checking current advisories
  at nextjs.org/blog.
- `recharts@2.15.4` shows a "no longer actively maintained" deprecation
  warning on install (0 security vulnerabilities as of this writing) — v3
  exists but requires a migration; fine to defer.
- Production build verified locally (`npm run build`) before hand-off:
  compiles clean, both routes prerender as static content, 0 npm audit
  vulnerabilities.

## 8. Repo structure
```
backend/app/
  main.py              FastAPI app + routes
  config.py            env var loading
  models.py            pydantic request/response schemas
  db.py                Supabase/Postgres + pgvector helpers
  pipeline.py           orchestrates the full run for one company
  data_sources/
    price_data.py       yfinance 5yr daily OHLC
    fundamentals_api.py indianapi.in client
    screener_scraper.py free fallback scraper
    reports_fetcher.py  pulls annual report / transcript text for RAG
  analysis/
    ratios.py           liquidity/profitability/leverage/valuation ratios, 5yr trend
    capm.py             beta regression vs Nifty50, cost of equity, expected return
    risk.py             volatility, Sharpe, max drawdown, VaR
    red_flags.py        rule-based red flag detector
    backtest.py         historical accuracy of the signal
  rag/
    chunker.py, embeddings.py, vector_store.py, note_generator.py
  reports/
    excel_report.py, pdf_report.py
frontend/               Next.js app (search page + report dashboard)
```

## 9. Honest limitations (please read)
- This is a **research aid**, not investment advice — the generated note
  should say so, and it does (see `rag/note_generator.py` prompt).
- Free-tier scraping/API sources can and will occasionally break or rate-limit;
  the code retries and falls back but isn't bulletproof — treat data gaps as
  expected, not a bug you need to chase forever.
- CAPM/beta/backtest numbers are standard textbook methodology, not a
  proprietary edge — professional desks combine dozens of these with human
  judgment; this pipeline gives you the same building blocks, not a black-box
  "buy signal."
