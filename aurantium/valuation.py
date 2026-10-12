"""Key-metrics maths: current value *and* history for every ratio the Key
Metrics panel shows, derived from raw yfinance data.

Pure pandas — no Qt, no network — so it runs on the provider's worker thread
and is testable with synthetic frames. :func:`build_metrics` is the entry
point; it returns a JSON-safe payload for the ``metrics:<SYM>`` topic.

Method, in one place (each metric's ``note`` repeats the part that applies):

* **Price-based ratios** are daily price ÷ a fundamental that steps when it
  becomes known, then resampled to weekly closes to keep the payload small.
* **Statement figures** (revenue, EBITDA, FCF, equity…) come from the annual
  and quarterly statements. Flows are trailing-twelve-month: the sum of four
  consecutive quarters where Yahoo has them, the fiscal-year figure otherwise.
  They step at the period end.
* **EPS (TTM)** is the sum of the last four *reported* EPS in Yahoo's earnings
  calendar — the adjusted ("street") figure analysts compare against — and
  steps on each report date. That calendar reaches back years further than
  Yahoo's statements do.
* **EPS (NTM)** is the sum of the next four quarters to be reported. Past
  quarters use the consensus that stood just before each report; quarters not
  yet in the calendar come from today's analyst estimates (next quarter,
  quarter after, and the fiscal-year totals). Older points therefore use the
  *final* pre-report consensus, which is later information than was known on
  the day plotted.
* **Beta** is rolling 60-month beta of monthly returns against the S&P 500 —
  Yahoo's own convention for its quoted beta. **Alpha** is the intercept of
  that same regression, annualized.

The current value of a metric with history is the latest point of that
history, so the table, its 5-year statistics and the chart all agree. Where
Yahoo quotes its own figure it rides along as ``quote`` for comparison.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

MONEY, PCT, RATIO, NUM, DAYS, TEXT = "money", "pct", "ratio", "num", "days", "text"

#: days of history the table's Avg/Low/High/Pctile columns summarize
STATS_DAYS = 5 * 365

#: Display order of the groups (the panel's filter chips follow it).
GROUPS = [
    "Valuation",
    "Income & Cash Flow",
    "Growth",
    "Profitability",
    "Returns on Capital",
    "Balance Sheet",
    "Efficiency",
    "Per Share",
    "Dividends & Buybacks",
    "Performance",
    "Risk",
    "Ownership & Analysts",
    "Fund",
]

#: (id, label, group, kind, method note). Display order within each group.
CATALOGUE: list[tuple[str, str, str, str, str]] = [
    # -- Valuation ---------------------------------------------------------
    ("mktcap", "Market Cap", "Valuation", MONEY, "Price × shares outstanding (shares from the latest balance sheet)."),
    ("ev", "Enterprise Value", "Valuation", MONEY, "Market cap + total debt − cash & short-term investments."),
    ("pe_ttm", "P/E (TTM)", "Valuation", RATIO, "Price ÷ EPS (TTM). EPS is the sum of the last four reported (adjusted, 'street') EPS in Yahoo's earnings calendar, stepping on each report date."),
    ("pe_ntm", "P/E (NTM)", "Valuation", RATIO, "Price ÷ EPS (NTM) — consensus EPS for the next four quarters to be reported. Past points use the final consensus before each report; upcoming quarters use today's analyst estimates."),
    ("pe_gaap", "P/E (GAAP, TTM)", "Valuation", RATIO, "Price ÷ diluted GAAP EPS over the trailing twelve months, from the income statements."),
    ("earn_yield", "Earnings Yield (TTM)", "Valuation", PCT, "EPS (TTM) ÷ price — the inverse of P/E, comparable with bond yields."),
    ("peg", "PEG (5Y exp.)", "Valuation", RATIO, "Yahoo's quoted PEG: P/E ÷ expected 5-year EPS growth. No history is published."),
    ("peg_ntm", "PEG (NTM)", "Valuation", RATIO, "P/E (NTM) ÷ expected EPS growth over the next twelve months (in %). Blank when expected growth is under 1%."),
    ("pb", "P/B", "Valuation", RATIO, "Price ÷ book value per share (stockholders' equity ÷ shares)."),
    ("ptb", "P/Tangible Book", "Valuation", RATIO, "Price ÷ tangible book value per share (equity less goodwill and intangibles)."),
    ("ps", "P/S (TTM)", "Valuation", RATIO, "Market cap ÷ revenue (TTM)."),
    ("pfcf", "P/FCF (TTM)", "Valuation", RATIO, "Market cap ÷ free cash flow (TTM). Blank while FCF is negative."),
    ("ev_ebitda", "EV/EBITDA (TTM)", "Valuation", RATIO, "Enterprise value ÷ EBITDA (TTM). Blank while EBITDA is negative."),
    ("ev_ebit", "EV/EBIT (TTM)", "Valuation", RATIO, "Enterprise value ÷ EBIT (TTM). Blank while EBIT is negative."),
    ("ev_sales", "EV/Sales (TTM)", "Valuation", RATIO, "Enterprise value ÷ revenue (TTM)."),
    ("ev_fcf", "EV/FCF (TTM)", "Valuation", RATIO, "Enterprise value ÷ free cash flow (TTM). Blank while FCF is negative."),
    ("fcf_yield", "FCF Yield (TTM)", "Valuation", PCT, "Free cash flow (TTM) ÷ market cap."),
    # -- Income & cash flow ------------------------------------------------
    ("revenue", "Revenue (TTM)", "Income & Cash Flow", MONEY, "Total revenue, trailing twelve months (four quarters, or the fiscal year)."),
    ("ebitda", "EBITDA (TTM)", "Income & Cash Flow", MONEY, "EBITDA as reported by Yahoo, trailing twelve months."),
    ("adj_ebitda", "Adj. EBITDA (TTM)", "Income & Cash Flow", MONEY, "Yahoo's Normalized EBITDA — EBITDA excluding unusual items — trailing twelve months. Company-defined 'adjusted EBITDA' in filings may differ."),
    ("net_income", "Net Income (TTM)", "Income & Cash Flow", MONEY, "Net income attributable to common shareholders, trailing twelve months."),
    ("fcf", "Free Cash Flow (TTM)", "Income & Cash Flow", MONEY, "Operating cash flow − capital expenditure, trailing twelve months."),
    # -- Growth ------------------------------------------------------------
    ("rev_growth", "Revenue Growth (YoY)", "Growth", PCT, "Revenue (TTM) vs the same trailing twelve months a year earlier."),
    ("ebitda_growth", "EBITDA Growth (YoY)", "Growth", PCT, "EBITDA (TTM) vs a year earlier. Blank when the base is negative."),
    ("ni_growth", "Net Income Growth (YoY)", "Growth", PCT, "Net income (TTM) vs a year earlier. Blank when the base is negative."),
    ("eps_growth", "EPS Growth (YoY)", "Growth", PCT, "EPS (TTM) vs four quarters earlier, from the earnings calendar."),
    ("fcf_growth", "FCF Growth (YoY)", "Growth", PCT, "Free cash flow (TTM) vs a year earlier. Blank when the base is negative."),
    ("eps_growth_ntm", "Expected EPS Growth (NTM)", "Growth", PCT, "EPS (NTM) ÷ EPS (TTM) − 1: what consensus expects over the next twelve months."),
    ("rev_cagr_3y", "Revenue CAGR (3Y)", "Growth", PCT, "Compound annual revenue growth across the last three fiscal years."),
    ("eps_cagr_3y", "EPS CAGR (3Y)", "Growth", PCT, "Compound annual growth of EPS (TTM) over three years, from the earnings calendar."),
    ("rev_growth_q", "Revenue Growth (Qtr YoY)", "Growth", PCT, "Yahoo's latest-quarter revenue growth vs the same quarter a year ago."),
    ("earn_growth_q", "Earnings Growth (Qtr YoY)", "Growth", PCT, "Yahoo's latest-quarter earnings growth vs the same quarter a year ago."),
    # -- Profitability -----------------------------------------------------
    ("gross_margin", "Gross Margin", "Profitability", PCT, "Gross profit ÷ revenue (TTM)."),
    ("ebitda_margin", "EBITDA Margin", "Profitability", PCT, "EBITDA ÷ revenue (TTM)."),
    ("op_margin", "Operating Margin", "Profitability", PCT, "Operating income ÷ revenue (TTM)."),
    ("pretax_margin", "Pre-tax Margin", "Profitability", PCT, "Pre-tax income ÷ revenue (TTM)."),
    ("net_margin", "Net Margin", "Profitability", PCT, "Net income ÷ revenue (TTM)."),
    ("fcf_margin", "FCF Margin", "Profitability", PCT, "Free cash flow ÷ revenue (TTM)."),
    ("tax_rate", "Effective Tax Rate", "Profitability", PCT, "Tax provision ÷ pre-tax income (TTM)."),
    ("rnd_pct", "R&D / Revenue", "Profitability", PCT, "Research and development expense ÷ revenue (TTM)."),
    ("sga_pct", "SG&A / Revenue", "Profitability", PCT, "Selling, general and administrative expense ÷ revenue (TTM)."),
    ("capex_pct", "Capex / Revenue", "Profitability", PCT, "Capital expenditure ÷ revenue (TTM)."),
    ("sbc_pct", "Stock Comp / Revenue", "Profitability", PCT, "Stock-based compensation ÷ revenue (TTM)."),
    ("cash_conv", "Cash Conversion (FCF/NI)", "Profitability", RATIO, "Free cash flow ÷ net income (TTM). Above 1x: earnings are backed by more than their weight in cash."),
    # -- Returns on capital ------------------------------------------------
    ("roe", "ROE", "Returns on Capital", PCT, "Net income (TTM) ÷ stockholders' equity at period end."),
    ("roa", "ROA", "Returns on Capital", PCT, "Net income (TTM) ÷ total assets at period end."),
    ("roic", "ROIC", "Returns on Capital", PCT, "EBIT × (1 − effective tax rate) ÷ invested capital (debt + equity − cash) at period end. Tax rate defaults to 21% when not measurable."),
    ("roce", "ROCE", "Returns on Capital", PCT, "EBIT ÷ capital employed (total assets − current liabilities) at period end."),
    # -- Balance sheet -----------------------------------------------------
    ("net_debt", "Net Debt", "Balance Sheet", MONEY, "Total debt − cash & short-term investments (negative = net cash)."),
    ("net_debt_ebitda", "Net Debt / EBITDA", "Balance Sheet", RATIO, "Net debt ÷ EBITDA (TTM). Blank while EBITDA is negative."),
    ("debt_ebitda", "Total Debt / EBITDA", "Balance Sheet", RATIO, "Total debt ÷ EBITDA (TTM). Blank while EBITDA is negative."),
    ("debt_equity", "Debt / Equity", "Balance Sheet", RATIO, "Total debt ÷ stockholders' equity. Blank when equity is negative."),
    ("debt_capital", "Debt / Capital", "Balance Sheet", PCT, "Total debt ÷ (total debt + stockholders' equity)."),
    ("int_cover", "Interest Coverage", "Balance Sheet", RATIO, "EBIT ÷ interest expense (TTM)."),
    ("current_ratio", "Current Ratio", "Balance Sheet", RATIO, "Current assets ÷ current liabilities."),
    ("quick_ratio", "Quick Ratio", "Balance Sheet", RATIO, "(Current assets − inventory) ÷ current liabilities."),
    ("cash_ratio", "Cash Ratio", "Balance Sheet", RATIO, "Cash & short-term investments ÷ current liabilities."),
    ("altman_z", "Altman Z-Score", "Balance Sheet", NUM, "1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MktCap/TL + 1.0·Sales/TA. Above ~3 is comfortable; below ~1.8 signals distress. Built for manufacturers — read with care for banks and asset-light firms."),
    ("working_capital", "Working Capital", "Balance Sheet", MONEY, "Current assets − current liabilities."),
    # -- Efficiency --------------------------------------------------------
    ("asset_turn", "Asset Turnover", "Efficiency", RATIO, "Revenue (TTM) ÷ total assets."),
    ("inv_turn", "Inventory Turnover", "Efficiency", RATIO, "Cost of revenue (TTM) ÷ inventory."),
    ("dso", "Days Sales Outstanding", "Efficiency", DAYS, "Receivables ÷ revenue (TTM) × 365."),
    ("dio", "Days Inventory Outstanding", "Efficiency", DAYS, "Inventory ÷ cost of revenue (TTM) × 365."),
    ("dpo", "Days Payables Outstanding", "Efficiency", DAYS, "Payables ÷ cost of revenue (TTM) × 365."),
    ("ccc", "Cash Conversion Cycle", "Efficiency", DAYS, "DSO + DIO − DPO: days between paying suppliers and collecting from customers."),
    # -- Per share ---------------------------------------------------------
    ("eps_ttm", "EPS (TTM)", "Per Share", NUM, "Sum of the last four reported (adjusted) EPS in Yahoo's earnings calendar."),
    ("eps_ntm", "EPS (NTM)", "Per Share", NUM, "Sum of consensus EPS for the next four quarters to be reported."),
    ("eps_gaap", "EPS (GAAP, TTM)", "Per Share", NUM, "Diluted GAAP EPS over the trailing twelve months, from the income statements."),
    ("rev_ps", "Revenue / Share", "Per Share", NUM, "Revenue (TTM) ÷ shares outstanding."),
    ("fcf_ps", "FCF / Share", "Per Share", NUM, "Free cash flow (TTM) ÷ shares outstanding."),
    ("bvps", "Book Value / Share", "Per Share", NUM, "Stockholders' equity ÷ shares outstanding."),
    ("tbvps", "Tangible Book / Share", "Per Share", NUM, "Tangible book value ÷ shares outstanding."),
    ("dps_ttm", "Dividends / Share (TTM)", "Per Share", NUM, "Dividends paid per share over the trailing 365 days."),
    # -- Dividends & buybacks ----------------------------------------------
    ("div_yield_ttm", "Div Yield (TTM)", "Dividends & Buybacks", PCT, "Dividends paid over the trailing 365 days ÷ price."),
    ("div_yield_ntm", "Div Yield (NTM)", "Dividends & Buybacks", PCT, "Indicated forward yield: the declared annual dividend rate (latest dividend × payments per year in the past) ÷ price."),
    ("payout", "Payout Ratio", "Dividends & Buybacks", PCT, "Dividends / share (TTM) ÷ EPS (TTM)."),
    ("fcf_payout", "FCF Payout", "Dividends & Buybacks", PCT, "Cash dividends paid ÷ free cash flow (TTM)."),
    ("div_growth", "Dividend Growth (YoY)", "Dividends & Buybacks", PCT, "Dividends / share (TTM) vs a year earlier."),
    ("div_cagr_5y", "Dividend CAGR (5Y)", "Dividends & Buybacks", PCT, "Compound annual growth of dividends / share (TTM) over five years."),
    ("buyback_yield", "Buyback Yield (TTM)", "Dividends & Buybacks", PCT, "Share repurchases (TTM) ÷ market cap."),
    ("sh_yield", "Shareholder Yield (TTM)", "Dividends & Buybacks", PCT, "Dividend yield (TTM) + buyback yield (TTM)."),
    ("share_change", "Share Count Change (YoY)", "Dividends & Buybacks", PCT, "Shares outstanding vs a year earlier. Negative = net buybacks; positive = dilution."),
    ("avg_div_yield_5y", "5Y Avg Div Yield (Yahoo)", "Dividends & Buybacks", PCT, "Yahoo's five-year average dividend yield."),
    # -- Performance -------------------------------------------------------
    ("ret_1m", "Return 1M", "Performance", PCT, "Price change over one month (excludes dividends)."),
    ("ret_3m", "Return 3M", "Performance", PCT, "Price change over three months (excludes dividends)."),
    ("ret_6m", "Return 6M", "Performance", PCT, "Price change over six months (excludes dividends)."),
    ("ret_ytd", "Return YTD", "Performance", PCT, "Price change since the last close of the previous year (excludes dividends)."),
    ("ret_1y", "Return 1Y", "Performance", PCT, "Price change over one year (excludes dividends)."),
    ("ret_3y", "Return 3Y (ann.)", "Performance", PCT, "Annualized price change over three years (excludes dividends)."),
    ("ret_5y", "Return 5Y (ann.)", "Performance", PCT, "Annualized price change over five years (excludes dividends)."),
    ("rel_spx_1y", "vs S&P 500 (1Y)", "Performance", PCT, "1-year price return minus the S&P 500's 1-year price return."),
    ("from_high", "From 52W High", "Performance", PCT, "Price vs the highest close of the trailing year."),
    ("range_pos", "52W Range Position", "Performance", PCT, "Where price sits between the trailing year's low (0%) and high (100%)."),
    ("vs_200dma", "vs 200-Day Average", "Performance", PCT, "Price vs its 200-trading-day moving average."),
    # -- Risk --------------------------------------------------------------
    ("beta", "Beta (5Y Monthly)", "Risk", NUM, "Rolling 60-month beta of monthly returns vs the S&P 500 (36 months minimum)."),
    ("alpha", "Alpha (5Y Monthly, ann.)", "Risk", PCT, "Jensen's alpha: the intercept of the same 60-month regression on the S&P 500 that gives beta, annualized (× 12). Zero risk-free rate; price returns, dividends excluded."),
    ("vol_1y", "Volatility (1Y)", "Risk", PCT, "Annualized standard deviation of daily returns over the trailing year."),
    ("max_dd_1y", "Max Drawdown (1Y)", "Risk", PCT, "Largest peak-to-trough fall in the trailing year."),
    ("ret_vol_1y", "Return / Volatility (1Y)", "Risk", RATIO, "1-year return ÷ 1-year volatility — a Sharpe ratio with a zero risk-free rate."),
    ("short_float", "Short Interest % Float", "Risk", PCT, "Shares sold short ÷ float, as last reported to Yahoo."),
    ("short_ratio", "Short Ratio (Days to Cover)", "Risk", NUM, "Shares short ÷ average daily volume."),
    # -- Ownership & analysts ----------------------------------------------
    ("insiders", "Held by Insiders", "Ownership & Analysts", PCT, "Share of shares outstanding held by insiders."),
    ("institutions", "Held by Institutions", "Ownership & Analysts", PCT, "Share of shares outstanding held by institutions."),
    ("target_mean", "Analyst Target (Mean)", "Ownership & Analysts", NUM, "Mean 12-month analyst price target."),
    ("target_upside", "Upside to Mean Target", "Ownership & Analysts", PCT, "Mean analyst target ÷ price − 1."),
    ("target_high", "Analyst Target (High)", "Ownership & Analysts", NUM, "Highest 12-month analyst price target."),
    ("target_low", "Analyst Target (Low)", "Ownership & Analysts", NUM, "Lowest 12-month analyst price target."),
    ("rec_mean", "Recommendation (1–5)", "Ownership & Analysts", NUM, "Mean analyst rating: 1 = Strong Buy, 3 = Hold, 5 = Strong Sell."),
    ("n_analysts", "Analysts Covering", "Ownership & Analysts", NUM, "Number of analysts in the target consensus."),
    # -- Fund (ETFs and mutual funds) --------------------------------------
    ("aum", "Fund Assets (AUM)", "Fund", MONEY, "Total net assets of the fund."),
    ("expense", "Expense Ratio", "Fund", PCT, "Net annual expense ratio."),
    ("fund_yield", "Fund Yield", "Fund", PCT, "Yahoo's trailing distribution yield for the fund."),
    ("nav", "NAV", "Fund", NUM, "Net asset value per share."),
    ("nav_prem", "Premium / Discount to NAV", "Fund", PCT, "Price ÷ NAV − 1."),
    ("fund_ytd", "YTD Return (Total)", "Fund", PCT, "Year-to-date total return reported for the fund."),
    ("fund_3y", "3Y Avg Return", "Fund", PCT, "Average annual total return over three years, as reported."),
    ("fund_5y", "5Y Avg Return", "Fund", PCT, "Average annual total return over five years, as reported."),
    ("fund_beta", "Beta (3Y)", "Fund", NUM, "Three-year beta reported for the fund."),
    ("fund_category", "Category", "Fund", TEXT, "Fund category."),
    ("fund_family", "Fund Family", "Fund", TEXT, "Fund sponsor."),
]

#: Backwards-compatible (id, label, group, kind) view of the catalogue.
METRICS = [(m, l, g, k) for m, l, g, k, _n in CATALOGUE]
NOTES = {m: n for m, _l, _g, _k, n in CATALOGUE}

#: Yahoo ``info`` field quoted alongside each metric, for comparison. For the
#: current-only metrics this *is* the value. A tuple is (field, divisor):
#: Yahoo reports a few fields in percent rather than as fractions.
YAHOO_QUOTE: dict[str, Any] = {
    "mktcap": "marketCap",
    "ev": "enterpriseValue",
    "pe_ttm": "trailingPE",
    "pb": "priceToBook",
    "ps": "priceToSalesTrailing12Months",
    "ev_ebitda": "enterpriseToEbitda",
    "ev_sales": "enterpriseToRevenue",
    "eps_gaap": "trailingEps",
    "gross_margin": "grossMargins",
    "ebitda_margin": "ebitdaMargins",
    "op_margin": "operatingMargins",
    "net_margin": "profitMargins",
    "roe": "returnOnEquity",
    "roa": "returnOnAssets",
    "current_ratio": "currentRatio",
    "quick_ratio": "quickRatio",
    "debt_equity": ("debtToEquity", 100.0),
    "bvps": "bookValue",
    "payout": "payoutRatio",
    "beta": "beta",
    "rev_growth_q": "revenueGrowth",
    "earn_growth_q": "earningsQuarterlyGrowth",
    "avg_div_yield_5y": ("fiveYearAvgDividendYield", 100.0),
    "short_float": "shortPercentOfFloat",
    "short_ratio": "shortRatio",
    "insiders": "heldPercentInsiders",
    "institutions": "heldPercentInstitutions",
    "target_mean": "targetMeanPrice",
    "target_high": "targetHighPrice",
    "target_low": "targetLowPrice",
    "rec_mean": "recommendationMean",
    "n_analysts": "numberOfAnalystOpinions",
    "aum": "totalAssets",
    "expense": ("netExpenseRatio", 100.0),
    "fund_yield": "yield",
    "nav": "navPrice",
    "fund_ytd": ("ytdReturn", 100.0),
    "fund_3y": "threeYearAverageReturn",
    "fund_5y": "fiveYearAverageReturn",
    "fund_beta": "beta3Year",
    "fund_category": "category",
    "fund_family": "fundFamily",
}

#: Yahoo quote types that get the Fund group (and have no company statements)
FUND_TYPES = {"ETF", "MUTUALFUND"}


# -- small helpers ---------------------------------------------------------------


def _empty() -> pd.Series:
    """An empty float series with a DatetimeIndex, so date comparisons and
    reindexing on it behave like on a populated one."""
    return pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def _naive(idx: Any) -> pd.DatetimeIndex:
    """A tz-naive, day-normalized DatetimeIndex (Yahoo mixes tz-aware price
    and calendar indexes with naive statement columns)."""
    idx = pd.DatetimeIndex(pd.to_datetime(idx))
    if idx.tz is not None:
        idx = idx.tz_localize(None)  # keep the exchange's wall-clock date
    return idx.normalize()


def _series(obj: Any) -> pd.Series:
    """Float Series with a naive sorted DatetimeIndex, NaNs dropped."""
    if obj is None or len(obj) == 0:
        return _empty()
    s = pd.Series(pd.to_numeric(pd.Series(obj).values, errors="coerce"), index=_naive(obj.index))
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.dropna()


def _stmt_row(df: Any, *names: str) -> pd.Series:
    """First of ``names`` present in a statement frame, as a date series."""
    if df is None or getattr(df, "empty", True):
        return _empty()
    for name in names:
        if name in df.index:
            s = _series(df.loc[name])
            if not s.empty:
                return s
    return _empty()


def _ttm(annual: pd.Series, quarterly: pd.Series) -> pd.Series:
    """Trailing-twelve-month flow: rolling four-quarter sums where the
    quarters are consecutive (spanning under ~10 months), fiscal-year values
    otherwise. Quarterly points win where both land on the same date."""
    parts = []
    if not annual.empty:
        parts.append(annual)
    if len(quarterly) >= 4:
        q = quarterly.sort_index()
        sums = q.rolling(4).sum()
        span = pd.Series(q.index, index=q.index).diff(3).dt.days
        sums = sums[(span <= 300) & sums.notna()]
        if not sums.empty:
            parts.append(sums)
    if not parts:
        return _empty()
    merged = pd.concat(parts)
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def _stock(annual: pd.Series, quarterly: pd.Series) -> pd.Series:
    """Balance-sheet level from both frequencies (quarterly wins on ties)."""
    merged = pd.concat([s for s in (annual, quarterly) if not s.empty] or [_empty()])
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def _step(points: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """``points`` carried forward onto ``index`` (NaN before the first)."""
    if points.empty or len(index) == 0:
        return pd.Series(np.nan, index=index)
    union = index.union(points.index)
    return points.reindex(union).ffill().reindex(index)


def _safe_div(a: pd.Series, b: pd.Series, positive_den: bool = True) -> pd.Series:
    b = b.where(b > 0) if positive_den else b.where(b != 0)
    return a / b


def _weekly(s: pd.Series) -> pd.Series:
    """Resample a daily series to week-ending (Friday) last values, keeping
    the final day exactly so the chart ends on today's value."""
    s = s.dropna()
    if s.empty:
        return s
    w = s.resample("W-FRI").last().dropna()
    if w.empty or w.index[-1] != s.index[-1]:
        w = w[w.index < s.index[-1]]
        w.loc[s.index[-1]] = s.iloc[-1]
    return w


def _to_points(s: pd.Series) -> dict:
    s = s.replace([np.inf, -np.inf], np.nan).dropna()
    if s.empty or not isinstance(s.index, pd.DatetimeIndex):
        return {"t": [], "v": []}
    t = [int(ts.replace(tzinfo=timezone.utc).timestamp()) for ts in s.index.to_pydatetime()]
    return {"t": t, "v": [float(v) for v in s.values]}


def _num(x: Any) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def stats(t: list[int], v: list[float], days: int = STATS_DAYS) -> dict:
    """Average / low / high / σ over the last ``days`` of a history, and
    the percentile rank of its latest value within that window."""
    if not v:
        return {}
    cutoff = t[-1] - days * 86400
    window = [x for ts, x in zip(t, v) if ts >= cutoff]
    if len(window) < 2:
        return {}
    arr = np.asarray(window, dtype=float)
    last = v[-1]
    return {
        "avg": float(arr.mean()),
        "low": float(arr.min()),
        "high": float(arr.max()),
        "std": float(arr.std()),
        "pctile": float((arr <= last).mean()),
        "span_days": int((t[-1] - max(cutoff, t[0])) / 86400),
    }


# -- EPS series ------------------------------------------------------------------


def _calendar(ed: Any) -> pd.DataFrame:
    """Earnings calendar → naive-dated frame with ``est`` and ``actual``,
    one row per report date, oldest first."""
    if ed is None or getattr(ed, "empty", True):
        return pd.DataFrame(
            {"est": _empty(), "actual": _empty()},
            index=pd.DatetimeIndex([]),
        )
    df = pd.DataFrame(
        {
            "est": pd.to_numeric(ed.get("EPS Estimate"), errors="coerce"),
            "actual": pd.to_numeric(ed.get("Reported EPS"), errors="coerce"),
        }
    )
    df.index = _naive(ed.index)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    # keep only rows that describe a real quarter (estimate or actual)
    return df[df["est"].notna() | df["actual"].notna()]


def ttm_eps_points(ed: Any) -> pd.Series:
    """Sum of the last four reported EPS, stepping on each report date."""
    cal = _calendar(ed)
    actual = cal["actual"].dropna()
    if len(actual) < 4:
        return _empty()
    return actual.rolling(4).sum().dropna()


def _estimate_row(ee: Any, period: str) -> Optional[float]:
    try:
        return _num(ee.loc[period, "avg"])
    except Exception:
        return None


def _ramp(last_q: float, total: float, n: int = 4) -> list[float]:
    """Split a fiscal-year estimate into ``n`` quarters that grow at a steady
    rate from ``last_q`` and sum to ``total`` — so a fast grower's early
    quarters aren't credited with the year's average. Equal parts when the
    inputs can't support a growth rate (losses, sign changes)."""
    if last_q <= 0 or total <= 0:
        return [total / n] * n
    target = total / last_q

    def total_for(g: float) -> float:
        return sum(g ** k for k in range(1, n + 1))

    lo, hi = 0.3, 3.0
    if not (total_for(lo) <= target <= total_for(hi)):
        return [total / n] * n
    for _ in range(60):
        mid = (lo + hi) / 2
        if total_for(mid) < target:
            lo = mid
        else:
            hi = mid
    g = (lo + hi) / 2
    return [last_q * g ** k for k in range(1, n + 1)]


def forward_quarters(ed: Any, ee: Any, info: dict, today: pd.Timestamp) -> pd.Series:
    """Quarter slots (report date → EPS) for the NTM calculation.

    History: every calendar quarter already reported, valued at the consensus
    that stood before it reported (actual EPS if no estimate was recorded).
    Future: synthesised from today's estimates — the next quarter (``0q``) on
    its calendar date, then ``+1q``, then enough further quarters from the
    fiscal-year totals to cover four quarters ahead of today.
    """
    cal = _calendar(ed)
    past = cal[cal.index <= today]
    slots = past["est"].where(past["est"].notna(), past["actual"]).dropna()

    future_dates = cal.index[cal.index > today]
    last = slots.index[-1] if len(slots) else today
    next_date = future_dates[0] if len(future_dates) else max(today + pd.Timedelta(days=1), last + pd.Timedelta(days=91))

    q0 = _estimate_row(ee, "0q")
    q1 = _estimate_row(ee, "+1q")
    fy0 = _estimate_row(ee, "0y")
    fy1 = _estimate_row(ee, "+1y")
    if q0 is None and len(future_dates):
        q0 = _num(cal.loc[future_dates[0], "est"])

    # How many of the current fiscal year's quarters have already reported?
    reported_fy0 = _empty()
    lfye = _num(info.get("lastFiscalYearEnd"))
    if lfye is not None:
        fy_start = pd.Timestamp(datetime.fromtimestamp(lfye, tz=timezone.utc).date())
        # reports within ~10 weeks of FY end still belong to the prior year
        reported_fy0 = cal["actual"][(cal.index > fy_start + pd.Timedelta(days=70)) & (cal.index <= today)].dropna()

    fills: list[Optional[float]] = [q0, q1]
    n_rem = 4 - len(reported_fy0) - 2
    if fy0 is not None and q0 is not None and q1 is not None and n_rem > 0:
        per = (fy0 - reported_fy0.sum() - q0 - q1) / n_rem
        fills += [per] * n_rem
    last_q = next((v for v in reversed(fills) if v is not None), None)
    if fy1 is not None:
        fills += _ramp(last_q, fy1) if last_q is not None else [fy1 / 4] * 4
    elif fy0 is not None:
        fills += [fy0 / 4] * 4

    future = {}
    date = next_date
    for val in fills:
        if val is None:
            break
        future[date] = val
        date = date + pd.Timedelta(days=91)
        if len(future) >= 5:
            break
    out = pd.concat([slots, pd.Series(future, dtype=float)])
    return out[~out.index.duplicated(keep="first")].sort_index()


def ntm_eps_points(slots: pd.Series) -> pd.Series:
    """NTM EPS stepping on each slot date: at a report date, the sum of the
    four quarters *after* it (the one just reported drops out)."""
    if len(slots) < 5:
        return _empty()
    vals = slots.values
    idx = slots.index
    out = {}
    for i in range(len(slots) - 4):
        out[idx[i]] = float(vals[i + 1 : i + 5].sum())
    # before the first slot the four quarters starting there are ahead
    out[idx[0] - pd.Timedelta(days=1)] = float(vals[0:4].sum())
    return pd.Series(out).sort_index()


# -- dividends / risk --------------------------------------------------------------


def dividend_series(divs: pd.Series, index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    """(trailing-365-day dividends per share, indicated annual rate) on ``index``.

    The indicated rate is the latest dividend × the number paid over the
    trailing year — the "annualized current dividend" convention. Both are
    zero, not NaN, for stretches with no dividends inside the window.
    """
    if divs.empty:
        z = pd.Series(0.0, index=index)
        return z, z
    daily = divs.groupby(level=0).sum()
    union = index.union(daily.index)
    per_day = daily.reindex(union).fillna(0.0)
    ttm = per_day.rolling("365D").sum()
    count = (per_day > 0).astype(float).rolling("365D").sum()
    latest = daily.reindex(union).ffill()
    indicated = (latest * count).where(count > 0, 0.0)
    return ttm.reindex(index).fillna(0.0), indicated.reindex(index).fillna(0.0)


def _monthly_returns(px: pd.Series, bench: pd.Series) -> pd.DataFrame:
    m = pd.concat(
        [px.resample("ME").last(), bench.resample("ME").last()], axis=1, keys=["s", "b"]
    ).dropna()
    return m.pct_change().dropna()


def rolling_beta(px: pd.Series, bench: pd.Series, window: int = 60, min_periods: int = 36) -> pd.Series:
    """Rolling beta of month-end returns vs a benchmark."""
    if px.empty or bench.empty:
        return _empty()
    r = _monthly_returns(px, bench)
    if len(r) < min_periods:
        return _empty()
    cov = r["s"].rolling(window, min_periods=min_periods).cov(r["b"])
    var = r["b"].rolling(window, min_periods=min_periods).var()
    return (cov / var).dropna()


def rolling_alpha(px: pd.Series, bench: pd.Series, window: int = 60, min_periods: int = 36) -> pd.Series:
    """Rolling annualized Jensen's alpha of month-end returns vs a benchmark:
    the intercept of the same regression ``rolling_beta`` takes the slope of,
    × 12. Zero risk-free rate, like the return/volatility ratio."""
    if px.empty or bench.empty:
        return _empty()
    r = _monthly_returns(px, bench)
    if len(r) < min_periods:
        return _empty()
    roll = r.rolling(window, min_periods=min_periods)
    mean = roll.mean()
    beta = r["s"].rolling(window, min_periods=min_periods).cov(r["b"]) / roll.var()["b"]
    return ((mean["s"] - beta * mean["b"]) * 12).dropna()


# -- the payload --------------------------------------------------------------------


def _yoy(s: pd.Series, years: int = 1, tol_days: int = 60) -> pd.Series:
    """Growth of each point vs the point ``years`` earlier (±``tol_days``).
    Blank where the base is not positive — growth off a loss is meaningless."""
    if len(s) < 2:
        return _empty()
    s = s.sort_index()
    out = {}
    for d, v in s.items():
        target = d - pd.Timedelta(days=365 * years)
        prior = s[(s.index >= target - pd.Timedelta(days=tol_days)) & (s.index <= target + pd.Timedelta(days=tol_days))]
        if prior.empty:
            continue
        base = prior.iloc[(abs((prior.index - target).days)).argmin()]
        if base > 0:
            out[d] = v / base - 1
    return pd.Series(out, dtype=float).sort_index() if out else _empty()


def _cagr(s: pd.Series, years: int) -> pd.Series:
    g = _yoy(s, years=years)
    return ((1 + g) ** (1 / years) - 1).where(g > -1) if not g.empty else g


def _lag(daily: pd.Series, days: int) -> pd.Series:
    """``daily`` as it stood ``days`` calendar days before each date."""
    if daily.empty:
        return daily
    back = daily.index - pd.Timedelta(days=days)
    vals = daily.reindex(back, method="ffill").values
    out = pd.Series(vals, index=daily.index)
    return out.where(back >= daily.index[0])


def _max_drawdown(close: pd.Series, window: int = 252) -> pd.Series:
    def dd(a: np.ndarray) -> float:
        peak = np.maximum.accumulate(a)
        return float((a / peak - 1).min())

    return close.rolling(window, min_periods=int(window * 0.8)).apply(dd, raw=True)


def build_metrics(raw: dict) -> dict:
    """Assemble the ``metrics:<SYM>`` payload from raw yfinance pieces.

    ``raw`` keys (all optional): ``info`` dict; ``prices`` daily frame with
    ``Close`` (split-, not dividend-adjusted) and ``Dividends``; ``bench``
    daily S&P 500 close series; ``earnings_dates`` and ``earnings_estimate``
    frames; ``income``/``balance``/``cashflow`` as ``{"annual", "quarterly"}``
    frames.
    """
    info = raw.get("info") or {}
    prices = raw.get("prices")
    cols = getattr(prices, "columns", [])
    close = _series(prices["Close"]) if prices is not None and "Close" in cols else _empty()
    divs = _series(prices["Dividends"]) if prices is not None and "Dividends" in cols else _empty()
    divs = divs[divs > 0]
    bench = _series(raw.get("bench")) if raw.get("bench") is not None else _empty()
    idx = close.index
    today = idx[-1] if len(idx) else pd.Timestamp.today().normalize()

    def stmt(kind: str, freq: str) -> Any:
        return (raw.get(kind) or {}).get(freq)

    def flow(*names: str, kind: str = "income") -> pd.Series:
        return _ttm(_stmt_row(stmt(kind, "annual"), *names), _stmt_row(stmt(kind, "quarterly"), *names))

    def level(*names: str) -> pd.Series:
        return _stock(_stmt_row(stmt("balance", "annual"), *names), _stmt_row(stmt("balance", "quarterly"), *names))

    def ratio(a: pd.Series, b: pd.Series, positive_den: bool = True) -> pd.Series:
        """Statement-cadence ratio on ``a``'s dates (``b`` carried to them)."""
        if a.empty or b.empty:
            return _empty()
        return _safe_div(a, _step(b, a.index), positive_den).dropna()

    # -- statement series --------------------------------------------------
    revenue = flow("Total Revenue", "Operating Revenue")
    cogs = flow("Cost Of Revenue", "Reconciled Cost Of Revenue")
    gross = flow("Gross Profit")
    ebitda = flow("EBITDA")
    adj_ebitda = flow("Normalized EBITDA")
    ebit = flow("EBIT", "Operating Income")
    op_inc = flow("Operating Income", "EBIT")
    pretax = flow("Pretax Income")
    tax = flow("Tax Provision")
    net_inc = flow("Net Income Common Stockholders", "Net Income")
    rnd = flow("Research And Development")
    sga = flow("Selling General And Administration")
    interest = flow("Interest Expense", "Interest Expense Non Operating").abs()
    gaap_eps = flow("Diluted EPS")
    fcf = flow("Free Cash Flow", kind="cashflow")
    capex = flow("Capital Expenditure", kind="cashflow").abs()
    sbc = flow("Stock Based Compensation", kind="cashflow")
    buybacks = flow("Repurchase Of Capital Stock", kind="cashflow").abs()
    divs_paid = flow("Cash Dividends Paid", kind="cashflow").abs()

    equity = level("Stockholders Equity", "Common Stock Equity")
    tangible = level("Tangible Book Value")
    assets = level("Total Assets")
    liabilities = level("Total Liabilities Net Minority Interest")
    cur_assets = level("Current Assets")
    cur_liab = level("Current Liabilities")
    inventory = level("Inventory")
    receivables = level("Accounts Receivable", "Receivables")
    payables = level("Accounts Payable", "Payables")
    retained = level("Retained Earnings")
    debt = level("Total Debt")
    cash = level("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
    shares = level("Ordinary Shares Number", "Share Issued")
    if shares.empty and _num(info.get("sharesOutstanding")):
        shares = pd.Series([float(info["sharesOutstanding"])], index=[today])

    hist: dict[str, pd.Series] = {}

    # statement-cadence metrics (step at period ends)
    hist["rev_growth"] = _yoy(revenue)
    hist["ebitda_growth"] = _yoy(ebitda)
    hist["ni_growth"] = _yoy(net_inc)
    hist["fcf_growth"] = _yoy(fcf)
    annual_rev = _stmt_row(stmt("income", "annual"), "Total Revenue", "Operating Revenue")
    hist["rev_cagr_3y"] = _cagr(annual_rev, 3)
    hist["gross_margin"] = ratio(gross, revenue)
    hist["ebitda_margin"] = ratio(ebitda, revenue)
    hist["op_margin"] = ratio(op_inc, revenue)
    hist["pretax_margin"] = ratio(pretax, revenue)
    hist["net_margin"] = ratio(net_inc, revenue)
    hist["fcf_margin"] = ratio(fcf, revenue)
    tax_rate = ratio(tax, pretax).clip(lower=0.0, upper=0.6)
    hist["tax_rate"] = tax_rate
    hist["rnd_pct"] = ratio(rnd, revenue)
    hist["sga_pct"] = ratio(sga, revenue)
    hist["capex_pct"] = ratio(capex, revenue)
    hist["sbc_pct"] = ratio(sbc, revenue)
    hist["cash_conv"] = ratio(fcf, net_inc)
    hist["roe"] = ratio(net_inc, equity)
    hist["roa"] = ratio(net_inc, assets)
    if not ebit.empty:
        t = _step(tax_rate, ebit.index).fillna(0.21) if not tax_rate.empty else pd.Series(0.21, index=ebit.index)
        nopat = ebit * (1 - t)
        invested = _step(debt, ebit.index).fillna(0.0) + _step(equity, ebit.index) - _step(cash, ebit.index).fillna(0.0)
        hist["roic"] = _safe_div(nopat, invested).dropna()
        hist["roce"] = _safe_div(ebit, _step(assets, ebit.index) - _step(cur_liab, ebit.index)).dropna()
        hist["int_cover"] = ratio(ebit, interest)

    nd = (debt - cash.reindex(debt.index).fillna(0.0)).dropna() if not debt.empty else _empty()
    hist["net_debt"] = nd
    hist["net_debt_ebitda"] = ratio(nd, ebitda) if not nd.empty else _empty()
    hist["debt_ebitda"] = ratio(debt, ebitda)
    hist["debt_equity"] = ratio(debt, equity)
    if not debt.empty and not equity.empty:
        hist["debt_capital"] = _safe_div(debt, debt + _step(equity, debt.index)).dropna()
    hist["current_ratio"] = ratio(cur_assets, cur_liab)
    if not cur_assets.empty:
        hist["quick_ratio"] = ratio(cur_assets - _step(inventory, cur_assets.index).fillna(0.0), cur_liab)
    hist["cash_ratio"] = ratio(cash, cur_liab)
    if not cur_assets.empty and not cur_liab.empty:
        hist["working_capital"] = (cur_assets - _step(cur_liab, cur_assets.index)).dropna()
    # net debt/EBITDA etc. read like the balance sheet: flows carried onto
    # balance dates would be circular, so take flows on the flow's own dates
    hist["asset_turn"] = ratio(revenue, assets)
    hist["inv_turn"] = ratio(cogs, inventory)
    dso = (ratio(receivables, revenue) * 365) if not receivables.empty else _empty()
    dio = (ratio(inventory, cogs) * 365) if not inventory.empty else _empty()
    dpo = (ratio(payables, cogs) * 365) if not payables.empty else _empty()
    hist["dso"], hist["dio"], hist["dpo"] = dso, dio, dpo
    if not dso.empty and not dpo.empty:
        ccc = (dso + _step(dio, dso.index).fillna(0.0) - _step(dpo, dso.index)).dropna()
        hist["ccc"] = ccc
    hist["fcf_payout"] = ratio(divs_paid, fcf).where(lambda s: s > 0) if not divs_paid.empty else _empty()
    hist["share_change"] = _yoy(shares)
    hist["eps_gaap"] = gaap_eps
    hist["rev_ps"] = ratio(revenue, shares)
    hist["fcf_ps"] = ratio(fcf, shares) if not fcf.empty else _empty()

    if len(idx):
        step = lambda s: _step(s, idx)  # noqa: E731
        price_pos = close.where(close > 0)
        sh = step(shares)
        mktcap = close * sh
        net_debt_d = step(debt).fillna(0.0) - step(cash).fillna(0.0)
        ev = (mktcap + net_debt_d).where(step(debt).notna() | step(cash).notna())

        ttm_eps = ttm_eps_points(raw.get("earnings_dates"))
        slots = forward_quarters(raw.get("earnings_dates"), raw.get("earnings_estimate"), info, today)
        ntm_eps = ntm_eps_points(slots)
        ahead = slots[slots.index > today]
        if len(ahead) >= 4:
            # the "now" point: the next four quarters as of today, not as of
            # the last report date
            ntm_eps = ntm_eps[ntm_eps.index < today]
            ntm_eps.loc[today] = float(ahead.iloc[:4].sum())
        eps_d, ntm_d = step(ttm_eps), step(ntm_eps)
        exp_growth = _safe_div(ntm_d, eps_d) - 1

        daily = {
            "mktcap": mktcap,
            "ev": ev,
            "pe_ttm": _safe_div(close, eps_d),
            "pe_ntm": _safe_div(close, ntm_d),
            "pe_gaap": _safe_div(close, step(gaap_eps)),
            "earn_yield": eps_d / price_pos,
            # below ~1% expected growth the PEG explodes into noise
            "peg_ntm": _safe_div(_safe_div(close, ntm_d), (exp_growth * 100).where(exp_growth >= 0.01)),
            "pb": _safe_div(close, step(equity) / sh),
            "ptb": _safe_div(close, step(tangible) / sh),
            "ps": _safe_div(mktcap, step(revenue)),
            "pfcf": _safe_div(mktcap, step(fcf)),
            "ev_ebitda": _safe_div(ev, step(ebitda)),
            "ev_ebit": _safe_div(ev, step(ebit)),
            "ev_sales": _safe_div(ev, step(revenue)),
            "ev_fcf": _safe_div(ev, step(fcf)),
            "fcf_yield": step(fcf) / mktcap.where(mktcap > 0),
            "eps_growth_ntm": exp_growth.where(eps_d > 0),
            "bvps": step(equity) / sh,
            "tbvps": step(tangible) / sh,
            "buyback_yield": step(buybacks) / mktcap.where(mktcap > 0),
        }

        dps, indicated = dividend_series(divs, idx)
        declared = _num(info.get("dividendRate"))
        if declared and declared > 0:
            # today's point uses the declared annual rate, which already
            # includes a raise announced but not yet paid
            indicated = indicated.copy()
            indicated.iloc[-1] = declared
        daily["div_yield_ttm"] = dps / price_pos
        daily["div_yield_ntm"] = indicated / price_pos
        daily["dps_ttm"] = dps
        daily["payout"] = _safe_div(dps, eps_d).where(dps > 0)
        daily["sh_yield"] = daily["div_yield_ttm"] + daily["buyback_yield"].fillna(0.0)
        daily["sh_yield"] = daily["sh_yield"].where(daily["buyback_yield"].notna() | (dps > 0))
        dps_pos = dps.where(dps > 0)
        daily["div_growth"] = dps_pos / _lag(dps_pos, 365) - 1
        daily["div_cagr_5y"] = (dps_pos / _lag(dps_pos, 5 * 365)) ** (1 / 5) - 1

        for key, days in (("ret_1m", 30), ("ret_3m", 91), ("ret_6m", 182), ("ret_1y", 365)):
            daily[key] = close / _lag(close, days) - 1
        daily["ret_3y"] = (close / _lag(close, 3 * 365)) ** (1 / 3) - 1
        daily["ret_5y"] = (close / _lag(close, 5 * 365)) ** (1 / 5) - 1
        year_end = close.groupby(close.index.year).last().shift(1)
        base = pd.Series(close.index.year, index=close.index).map(year_end)
        daily["ret_ytd"] = close / base - 1
        if not bench.empty:
            b = bench.reindex(idx, method="ffill")
            daily["rel_spx_1y"] = daily["ret_1y"] - (b / _lag(b, 365) - 1)
        hi = close.rolling(252, min_periods=200).max()
        lo = close.rolling(252, min_periods=200).min()
        daily["from_high"] = close / hi - 1
        daily["range_pos"] = _safe_div(close - lo, hi - lo)
        daily["vs_200dma"] = close / close.rolling(200, min_periods=200).mean() - 1
        rets = close.pct_change()
        vol = rets.rolling(252, min_periods=200).std() * math.sqrt(252)
        daily["vol_1y"] = vol
        daily["max_dd_1y"] = _max_drawdown(close)
        daily["ret_vol_1y"] = daily["ret_1y"] / vol

        # daily series → weekly to keep the payload small
        hist.update({k: _weekly(v) for k, v in daily.items()})
        hist["beta"] = rolling_beta(close, bench)
        hist["alpha"] = rolling_alpha(close, bench)
        hist["eps_ttm"] = ttm_eps
        hist["eps_ntm"] = ntm_eps[ntm_eps.index <= today]
        hist["eps_growth"] = (ttm_eps / ttm_eps.shift(4) - 1).where(ttm_eps.shift(4) > 0).dropna()
        hist["eps_cagr_3y"] = (
            (ttm_eps / ttm_eps.shift(12)) ** (1 / 3) - 1
        ).where((ttm_eps.shift(12) > 0) & (ttm_eps > 0)).dropna()

        # Altman Z at each balance-sheet date, with that day's market cap
        if not assets.empty:
            a = assets.index
            mc_at = (close.reindex(a, method="ffill") * _step(shares, a))
            z = (
                1.2 * (_step(cur_assets, a) - _step(cur_liab, a)) / assets
                + 1.4 * _step(retained, a) / assets
                + 3.3 * _step(ebit, a) / assets
                + 0.6 * mc_at / _step(liabilities, a)
                + 1.0 * _step(revenue, a) / assets
            )
            hist["altman_z"] = z.dropna()

    # statement flows themselves
    hist["revenue"] = revenue
    hist["ebitda"] = ebitda
    hist["adj_ebitda"] = adj_ebitda
    hist["net_income"] = net_inc
    hist["fcf"] = fcf

    price = _num(close.iloc[-1]) if len(close) else _num(info.get("currentPrice"))
    quote_type = str(info.get("quoteType") or "").upper()
    is_fund = quote_type in FUND_TYPES

    metrics = []
    for mid, label, group, kind, note in CATALOGUE:
        if group == "Fund" and not is_fund:
            continue
        if is_fund and mid in ("mktcap", "ev"):
            continue  # a fund's size is its AUM (Fund group), not price × shares
        points = _to_points(hist.get(mid, _empty()))
        quote = _quote(info, mid, kind)
        if mid == "peg":
            quote = _num(info.get("trailingPegRatio")) or _num(info.get("pegRatio"))
        elif mid == "target_upside":
            tgt = _num(info.get("targetMeanPrice"))
            quote = tgt / price - 1 if tgt and price else None
        elif mid == "nav_prem":
            nav = _num(info.get("navPrice"))
            quote = price / nav - 1 if nav and price else None
        value = points["v"][-1] if points["v"] else quote
        metrics.append(
            {
                "id": mid,
                "label": label,
                "group": group,
                "kind": kind,
                "value": value,
                "quote": quote,
                "asof": points["t"][-1] if points["t"] else None,
                "note": note,
                "history": points,
                "stats": stats(points["t"], points["v"]),
            }
        )

    # Company-statement metrics don't apply to funds: keep Yahoo's quoted
    # fund-level P/E, P/B and yield, drop the rest rather than show blanks.
    return {
        "symbol": raw.get("symbol", ""),
        "name": info.get("shortName") or info.get("longName") or "",
        "currency": info.get("financialCurrency") or info.get("currency") or "",
        "quote_type": quote_type,
        "price": price,
        "groups": [g for g in GROUPS if g != "Fund" or is_fund],
        "metrics": metrics,
    }


def _quote(info: dict, mid: str, kind: str) -> Any:
    spec = YAHOO_QUOTE.get(mid)
    if spec is None:
        return None
    field, div = (spec, 1.0) if isinstance(spec, str) else spec
    raw = info.get(field)
    if kind == TEXT:
        return str(raw) if raw else None
    v = _num(raw)
    return None if v is None else v / div


def history_rows(metric: dict) -> Iterable[tuple[str, float]]:
    """(ISO date, value) pairs of a metric's history — for copy/export."""
    for ts, v in zip(metric["history"]["t"], metric["history"]["v"]):
        yield datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"), v
