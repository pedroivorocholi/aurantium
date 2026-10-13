"""Plain-language explanations for jargon shown in the UI.

Hovering a financial-statement line ("Minority Interest"), a column header
("IV%", "Surprise%") or any other term listed here pops a small tooltip with
a one- or two-sentence explanation. Only terms that aren't common sense are
listed — "Total Revenue" needs no help, "Total Equity Gross Minority
Interest" does.

Lookup is forgiving: case, spacing and punctuation are ignored, so
yfinance's run-together labels ("Tradeand Other Payables Non Current")
match their properly spaced entries. Use :func:`explain` for the raw text
and :func:`tooltip` for the rich-text tooltip.
"""

from __future__ import annotations

import html
import re
from typing import Optional

_MINORITY = (
    "Minority interest (non-controlling interest) is the slice of a subsidiary"
    " the company consolidates but doesn't fully own — e.g. the 20% of an 80%-owned"
    " unit that belongs to outside shareholders."
)

# Keys are written as displayed; they're normalized at import.
_TERMS: dict[str, str] = {
    # -- income statement ----------------------------------------------------
    "Cost Of Revenue": "Direct cost of producing what was sold — materials, manufacturing labour, hosting. Also called COGS.",
    "Reconciled Cost Of Revenue": "Cost of revenue restated by the data vendor so it's comparable across companies.",
    "Gross Profit": "Revenue minus cost of revenue — what's left before overhead, R&D and interest.",
    "Operating Expense": "Running costs that aren't tied to each unit sold: SG&A, R&D, and similar overhead.",
    "Selling General And Administration": "SG&A — sales, marketing, head-office and admin costs.",
    "Selling And Marketing Expense": "Cost of sales teams, advertising and promotion.",
    "General And Administrative Expense": "Head-office overhead: executives, finance, legal, HR.",
    "Other G And A": "General & administrative costs not broken out elsewhere.",
    "Research And Development": "R&D — spending on new products and technology, expensed as incurred.",
    "Operating Income": "Profit from the core business, before interest and taxes. Close to EBIT.",
    "Total Operating Income As Reported": "Operating income exactly as the company reported it, before the vendor's normalizing adjustments.",
    "EBIT": "Earnings Before Interest and Taxes — operating profit, ignoring how the company is financed.",
    "EBITDA": "Earnings Before Interest, Taxes, Depreciation and Amortization — a rough proxy for operating cash generation.",
    "Normalized EBITDA": "EBITDA with one-off (unusual) items stripped out.",
    "Normalized Income": "Net income with one-off (unusual) items and their tax effect stripped out.",
    "Net Interest Income": "Interest earned minus interest paid. The core revenue line for banks.",
    "Net Non Operating Interest Income Expense": "Interest earned on cash minus interest paid on debt — financing, not operations.",
    "Interest Expense Non Operating": "Interest paid on borrowings, reported below operating income.",
    "Interest Income Non Operating": "Interest earned on cash and investments, reported below operating income.",
    "Other Income Expense": "Gains and losses outside the core business — investment gains, FX, one-offs.",
    "Other Non Operating Income Expenses": "Miscellaneous income or costs unrelated to the core business.",
    "Special Income Charges": "One-off items such as restructuring, impairments or legal settlements.",
    "Total Unusual Items": "Sum of one-off gains and losses the vendor flags as non-recurring.",
    "Total Unusual Items Excluding Goodwill": "One-off gains and losses, not counting goodwill write-downs.",
    "Tax Effect Of Unusual Items": "How much the one-off items changed the tax bill.",
    "Tax Rate For Calcs": "Effective tax rate the vendor uses to compute normalized figures.",
    "Write Off": "Value of assets written down to zero because they're no longer recoverable.",
    "Impairment Of Capital Assets": "Write-down of plant, equipment or other long-lived assets worth less than their book value.",
    "Restructuring And Mergern Acquisition": "Costs of reorganizing the business or of doing M&A deals.",
    "Gain On Sale Of Security": "Profit or loss realized on selling investments.",
    "Earnings From Equity Interest": "The company's share of profit from affiliates it owns 20–50% of (equity method).",
    "Pretax Income": "Profit before income tax.",
    "Tax Provision": "Income tax expense booked for the period (not necessarily cash paid).",
    "Net Income Continuous Operations": "Net income from businesses the company is keeping.",
    "Net Income From Continuing Operations": "Net income from businesses the company is keeping.",
    "Net Income From Continuing And Discontinued Operation": "Net income including businesses being sold or shut down.",
    "Net Income Including Noncontrolling Interests": "Net income before handing minority shareholders their share. " + _MINORITY,
    "Net Income From Continuing Operation Net Minority Interest": "Continuing-operations profit attributable to the parent's shareholders, after the minority share.",
    "Minority Interests": "Profit belonging to minority (non-controlling) holders, deducted from net income. " + _MINORITY,
    "Net Income Common Stockholders": "Profit available to common shareholders, after preferred dividends.",
    "Diluted NI Availto Com Stockholders": "Profit available to common shareholders on a fully diluted basis (as if options and convertibles were exercised).",
    "Preferred Stock Dividends": "Dividends owed to preferred shareholders, paid before common holders get anything.",
    "Otherunder Preferred Stock Dividend": "Other adjustments between net income and income to common shareholders.",
    "Basic EPS": "Earnings Per Share — net income to common ÷ average shares outstanding.",
    "Diluted EPS": "EPS counting shares that options, warrants and convertibles could add. Usually the headline EPS.",
    "Basic Average Shares": "Average common shares outstanding during the period.",
    "Diluted Average Shares": "Average shares including those options and convertibles could create.",
    "Average Dilution Earnings": "Earnings adjustment from dilutive securities (e.g. convertible interest added back).",
    "Reconciled Depreciation": "Depreciation and amortization restated by the vendor for comparability.",
    "Depreciation And Amortization In Income Statement": "Non-cash cost spreading the price of assets over their useful lives.",
    "Depreciation Amortization Depletion Income Statement": "Non-cash cost of using up physical assets, intangibles and natural resources.",
    "Amortization": "Non-cash cost spreading the price of intangible assets (patents, customer lists) over their lives.",
    "Provision For Doubtful Accounts": "Expense set aside for customer bills that probably won't be paid.",
    "Excise Taxes": "Taxes levied on specific goods (fuel, alcohol, tobacco) collected from customers.",
    "Rent Expense Supplemental": "Rent paid on leased property and equipment.",
    # -- balance sheet -------------------------------------------------------
    "Minority Interest": "Equity in consolidated subsidiaries owned by outsiders. " + _MINORITY,
    "Total Equity Gross Minority Interest": "Total equity including the minority (non-controlling) holders' share.",
    "Total Liabilities Net Minority Interest": "Total liabilities, with minority interest kept out (it sits in equity).",
    "Stockholders Equity": "Book value owned by the parent's shareholders: assets minus liabilities minus minority interest.",
    "Common Stock Equity": "Shareholders' equity belonging to common (not preferred) holders.",
    "Total Capitalization": "Long-term debt plus equity — the permanent capital funding the business.",
    "Invested Capital": "Debt plus equity put into the business; the base for ROIC.",
    "Tangible Book Value": "Equity minus goodwill and intangibles — what's left if those were worth nothing.",
    "Net Tangible Assets": "Total assets minus intangibles and liabilities.",
    "Working Capital": "Current assets minus current liabilities — short-term liquidity cushion.",
    "Net Debt": "Total debt minus cash. Negative means the company holds more cash than debt.",
    "Total Debt": "All interest-bearing borrowings plus lease obligations.",
    "Capital Stock": "Par value of issued shares (common + preferred) — usually a tiny accounting figure.",
    "Additional Paid In Capital": "Cash received for shares above their par value.",
    "Retained Earnings": "Cumulative profits kept in the business rather than paid out as dividends.",
    "Treasury Stock": "Shares the company has bought back and holds; recorded as negative equity.",
    "Treasury Shares Number": "Count of bought-back shares held in treasury.",
    "Ordinary Shares Number": "Common shares outstanding.",
    "Share Issued": "Total shares ever issued, including those later bought back.",
    "Gains Losses Not Affecting Retained Earnings": "Accumulated other comprehensive income (AOCI) — unrealized gains/losses on FX, pensions and securities that bypass the income statement.",
    "Other Equity Adjustments": "Equity changes not classed elsewhere, often FX translation.",
    "Goodwill": "Price paid in acquisitions above the fair value of what was bought. Not amortized; written down if impaired.",
    "Other Intangible Assets": "Non-physical assets other than goodwill — patents, brands, customer relationships.",
    "Goodwill And Other Intangible Assets": "Goodwill plus identifiable intangibles like patents and brands.",
    "Net PPE": "Property, Plant & Equipment after accumulated depreciation.",
    "Gross PPE": "Property, Plant & Equipment at original cost, before depreciation.",
    "Accumulated Depreciation": "Total depreciation charged against PP&E to date.",
    "Construction In Progress": "Assets being built that aren't in service (or depreciated) yet.",
    "Leases": "Right-of-use assets for leased property and equipment.",
    "Investments And Advances": "Long-term investments and loans made to other entities.",
    "Long Term Equity Investment": "Stakes in other companies held for the long run (often equity-method affiliates).",
    "Investmentin Financial Assets": "Holdings of bonds, stocks and other financial instruments.",
    "Available For Sale Securities": "Investments marked to market, with gains parked in equity (AOCI) until sold.",
    "Held To Maturity Securities": "Bonds the company plans to hold until they mature; carried at cost.",
    "Receivables": "Money customers and others owe the company.",
    "Accounts Receivable": "Money customers owe for goods or services already delivered.",
    "Allowance For Doubtful Accounts Receivable": "Reserve for receivables expected to go unpaid.",
    "Inventory": "Goods held for sale, in production, or as raw materials.",
    "Work In Process": "Partly finished goods still in production.",
    "Prepaid Assets": "Expenses paid in advance (insurance, rent) that will be used up later.",
    "Restricted Cash": "Cash the company can't freely use — pledged as collateral or held for a set purpose.",
    "Hedging Assets Current": "Fair value of derivatives used to hedge, maturing within a year.",
    "Assets Held For Sale Current": "Assets the company has decided to sell within a year.",
    "Cash Cash Equivalents And Short Term Investments": "Cash plus money-market funds, T-bills and other liquid short-term holdings.",
    "Cash Equivalents": "Very liquid investments maturing within three months — practically cash.",
    "Other Short Term Investments": "Marketable securities maturing within a year.",
    "Non Current Deferred Taxes Assets": "Future tax savings from past losses or timing differences.",
    "Non Current Deferred Taxes Liabilities": "Taxes owed in future because of timing differences (e.g. faster tax depreciation).",
    "Deferred Revenue": "Cash received for goods or services not yet delivered — a liability until earned.",
    "Current Deferred Revenue": "Prepaid customer money to be earned within a year.",
    "Non Current Deferred Revenue": "Prepaid customer money to be earned after more than a year.",
    "Current Deferred Liabilities": "Deferred revenue and deferred taxes due within a year.",
    "Non Current Deferred Liabilities": "Deferred revenue and deferred taxes due after more than a year.",
    "Accounts Payable": "Money owed to suppliers for goods and services received.",
    "Payables And Accrued Expenses": "Supplier bills plus expenses incurred but not yet paid.",
    "Current Accrued Expenses": "Costs incurred (wages, utilities) but not yet paid.",
    "Trade And Other Payables Non Current": "Supplier and other payables due after more than a year.",
    "Current Debt": "Borrowings due within the next year.",
    "Long Term Debt": "Borrowings due after more than a year.",
    "Current Debt And Capital Lease Obligation": "Debt plus lease obligations due within the next year.",
    "Long Term Debt And Capital Lease Obligation": "Debt plus lease obligations due after more than a year.",
    "Capital Lease Obligations": "Liabilities for finance (capital) leases — economically a form of debt.",
    "Current Capital Lease Obligation": "Lease liabilities due within the next year.",
    "Long Term Capital Lease Obligation": "Lease liabilities due after more than a year.",
    "Commercial Paper": "Short-term unsecured IOUs (under 270 days) sold by large companies to fund day-to-day needs.",
    "Other Current Borrowings": "Short-term loans not classed as commercial paper or current long-term debt.",
    "Long Term Provisions": "Liabilities of uncertain timing or amount — lawsuits, decommissioning, warranties.",
    "Pension And Other Post Retirement Benefit Plans Current": "Pension and retiree-healthcare obligations payable within a year.",
    "Employee Benefits": "Obligations to employees, chiefly pensions and retiree healthcare.",
    "Derivative Product Liabilities": "Fair value of derivative contracts the company owes on.",
    "Due To Related Parties Current": "Money owed to affiliates, insiders or controlling shareholders, due within a year.",
    "Total Non Current Liabilities Net Minority Interest": "Liabilities due after more than a year.",
    # -- cash flow -----------------------------------------------------------
    "Operating Cash Flow": "Cash generated by the core business during the period.",
    "Investing Cash Flow": "Cash spent on (negative) or received from (positive) capex, acquisitions and investments.",
    "Financing Cash Flow": "Cash from or to funders: borrowing, repaying debt, issuing or buying back shares, dividends.",
    "Free Cash Flow": "Operating cash flow minus capital expenditure — cash available for dividends, buybacks or debt paydown.",
    "Capital Expenditure": "Capex — cash spent on physical assets like plants, equipment and data centers.",
    "Purchase Of PPE": "Cash spent buying property, plant and equipment (capex).",
    "Net PPE Purchase And Sale": "Capex minus proceeds from selling property and equipment.",
    "Net Business Purchase And Sale": "Cash spent on acquisitions minus cash received from divestitures.",
    "Net Investment Purchase And Sale": "Securities bought minus securities sold.",
    "Change In Working Capital": "Cash tied up in (negative) or released from (positive) receivables, inventory and payables.",
    "Stock Based Compensation": "Pay in shares or options. A real cost to shareholders (dilution) but non-cash, so it's added back here.",
    "Deferred Tax": "Tax expense booked but not yet paid in cash (or vice versa), added back to reconcile to cash.",
    "Deferred Income Tax": "Tax expense booked but not yet paid in cash (or vice versa), added back to reconcile to cash.",
    "Depreciation Amortization Depletion": "Non-cash charges added back to net income to get to cash flow.",
    "Asset Impairment Charge": "Non-cash write-down of assets, added back to net income.",
    "Other Non Cash Items": "Miscellaneous non-cash charges added back to net income.",
    "Operating Gains Losses": "Gains or losses on asset sales, removed because the cash shows up under investing.",
    "Gain Loss On Investment Securities": "Investment gains or losses, removed because they're not operating cash.",
    "Unrealized Gain Loss On Investment Securities": "Paper gains or losses on investments still held — no cash changed hands.",
    "Repurchase Of Capital Stock": "Cash spent buying back the company's own shares.",
    "Issuance Of Capital Stock": "Cash raised by selling new shares.",
    "Net Common Stock Issuance": "Shares issued minus shares bought back.",
    "Issuance Of Debt": "Cash raised from new borrowings.",
    "Repayment Of Debt": "Cash used to pay down borrowings.",
    "Net Issuance Payments Of Debt": "Debt raised minus debt repaid.",
    "Net Short Term Debt Issuance": "Net change in short-term borrowings like commercial paper.",
    "Cash Dividends Paid": "Cash paid out to shareholders as dividends.",
    "Net Other Financing Charges": "Other financing flows, e.g. taxes withheld on employee share vesting.",
    "Interest Paid Supplemental Data": "Cash actually paid in interest (can differ from the interest expense booked).",
    "Income Tax Paid Supplemental Data": "Cash actually paid in income tax (can differ from the tax provision).",
    "Effect Of Exchange Rate Changes": "Change in cash purely from currency moves on foreign cash balances.",
    "Beginning Cash Position": "Cash at the start of the period.",
    "End Cash Position": "Cash at the end of the period.",
    "Cash Flow From Continuing Operating Activities": "Operating cash flow from businesses the company is keeping.",
    "Cash Flow From Continuing Investing Activities": "Investing cash flow from businesses the company is keeping.",
    "Cash Flow From Continuing Financing Activities": "Financing cash flow from businesses the company is keeping.",
    "Dividend Paid Cfo": "Dividends paid that this filer classifies inside operating cash flow (allowed under IFRS).",
    "Dividend Received Cfo": "Dividends received from investments, counted in operating cash flow.",
    "Common Stock Payments": "Cash spent buying back common shares.",
    "Net Preferred Stock Issuance": "Preferred shares issued minus preferred shares redeemed.",
    "Net Other Investing Changes": "Investing flows not classed elsewhere.",
    "Gain Loss On Sale Of Business": "Accounting gain or loss on a divestiture, removed because the cash shows up under investing.",
    "Provisionand Write Offof Assets": "Non-cash provisions and write-downs (for a bank, mostly loan-loss provisions), added back.",
    # -- balance sheet extras ------------------------------------------------
    "Current Assets": "Assets expected to turn into cash within a year — cash, receivables, inventory.",
    "Current Liabilities": "Obligations due within a year.",
    "Total Non Current Assets": "Long-lived assets: PP&E, goodwill, long-term investments.",
    "Common Stock": "Par value of issued common shares — a tiny accounting figure, not market value.",
    "Preferred Stock": "Shares with a fixed dividend paid ahead of common holders; no or limited voting rights.",
    "Preferred Stock Equity": "Book value of preferred shares — equity that ranks ahead of common.",
    "Preferred Shares Number": "Preferred shares outstanding.",
    "Line Of Credit": "Drawn balance on a revolving bank credit facility.",
    "Trading Securities": "Securities held for short-term trading, marked to market through profit.",
    "Cash Cash Equivalents And Federal Funds Sold": "Bank cash plus overnight loans to other banks (fed funds sold).",
    "Cash Financial": "Cash held by a financial company, reported separately from equivalents.",
    "Gross Accounts Receivable": "Receivables before deducting the allowance for unpaid bills.",
    "Non Current Accounts Receivable": "Receivables due after more than a year.",
    "Non Current Deferred Assets": "Deferred tax assets and other deferred charges benefiting future years.",
    "Non Current Prepaid Assets": "Expenses paid in advance covering more than a year.",
    "Non Current Pension And Other Postretirement Benefit Plans": "Pension and retiree-healthcare obligations payable after more than a year.",
    "Due To Related Parties Non Current": "Money owed to affiliates or insiders, due after more than a year.",
    "Total Tax Payable": "Taxes owed but not yet paid.",
    # -- bank income statement -----------------------------------------------
    "Occupancy And Equipment": "A bank's branch, office and equipment costs.",
    "Other Non Interest Expense": "A bank's operating costs other than interest — tech, marketing, fees.",
    "Non Interest Expense": "A bank's operating costs: staff, premises, technology. Banks' version of opex.",
    "Non Interest Income": "Bank revenue from fees, trading and advisory rather than lending.",
    "Credit Losses Provision": "Money set aside for loans expected to go bad.",
    "Professional Expense And Contract Services Expense": "Fees for consultants, lawyers and outsourced services.",
    "Other Special Charges": "Other one-off charges not classed elsewhere.",
    "Operating Revenue": "Revenue from the core business, before other income.",
    # -- market data / column headers ----------------------------------------
    "Chg": "Change — price move versus the previous close.",
    "Chg%": "Percent change versus the previous close.",
    "IV": "Implied volatility — the yearly price swing the option market is pricing in.",
    "IV%": "Implied volatility — the yearly price swing the option market is pricing in.",
    "OI": "Open interest — option contracts currently outstanding.",
    "Vol": "Volume — shares or contracts traded today.",
    "Bid": "Highest price a buyer is currently offering.",
    "Ask": "Lowest price a seller is currently accepting.",
    "Strike": "The price at which the option lets you buy (call) or sell (put) the underlying.",
    "Δ": "Delta — option price change per $1 move in the underlying; also roughly the odds of expiring in the money.",
    "Γ": "Gamma — how fast delta changes as the underlying moves.",
    "Θ": "Theta — value the option loses per day from time decay.",
    "Vega": "Option price change per 1-point move in implied volatility.",
    "EPS": "Earnings Per Share — net income to common shareholders ÷ shares outstanding.",
    "EPS Est": "Analysts' consensus EPS estimate before the report.",
    "EPS Actual": "The EPS the company actually reported.",
    "Surprise%": "How far reported EPS beat (+) or missed (−) the consensus estimate.",
    "% Held": "Share of the company's outstanding stock this holder owns.",
    "5Y %ile": "Where today's value ranks in its 5-year history — 0% is the lowest seen, 100% the highest.",
    "5Y Avg": "Average value over the past five years.",
    "Mkt Cap": "Market capitalization — share price × shares outstanding.",
    "P/E": "Price-to-earnings — share price ÷ EPS. How many years of current profits the price represents.",
    "EV": "Enterprise value — market cap plus net debt; the price to buy the whole business.",
    "EV/EBITDA": "Enterprise value ÷ EBITDA — a valuation multiple that ignores capital structure.",
    "YTD": "Year to date — since the last close of the previous year.",
}


def _norm(text: str) -> str:
    # Keep % and Greek letters (isalnum covers those); drop spaces/punctuation
    return "".join(ch for ch in text.casefold() if ch.isalnum() or ch == "%")


GLOSSARY: dict[str, tuple[str, str]] = {_norm(k): (k, v) for k, v in _TERMS.items()}

_CHANGE_IN = re.compile(r"^\s*changes?\s+in\s+(.+)$", re.IGNORECASE)
_NET_MINORITY = "netminorityinterest"


def explain(term: Optional[str]) -> Optional[str]:
    """Return a plain-language explanation of ``term``, or ``None``."""
    if not term:
        return None
    key = _norm(term)
    if not key:
        return None
    hit = GLOSSARY.get(key)
    if hit:
        return hit[1]
    if key.endswith(_NET_MINORITY) and key != _NET_MINORITY:
        base = GLOSSARY.get(key[: -len(_NET_MINORITY)])
        if base:
            return base[1] + " Excludes the minority (non-controlling) holders' share."
    m = _CHANGE_IN.match(term)
    if m:
        what = m.group(1).strip()
        return (
            f"Cash effect of the change in {what} over the period. Negative means"
            " it absorbed cash (e.g. receivables or inventory grew); positive means"
            " it released cash (e.g. payables grew)."
        )
    return None


def tooltip(term: Optional[str], body: Optional[str] = None) -> str:
    """Rich-text tooltip for ``term``: bold heading + explanation.

    ``body`` overrides the glossary text — for callers that already carry a
    more specific note (e.g. the Debt view's formulas). Returns ``""`` when
    there's nothing to say, so ``setToolTip(tooltip(x))`` shows no empty box.
    """
    text = body if body else explain(term)
    if not text or not term:
        return ""
    head = html.escape(str(term).strip())
    lines = html.escape(text).replace("\n", "<br>")
    # <qt> forces rich text so Qt word-wraps the tooltip instead of
    # stretching it into one long line across the screen.
    return f"<qt><b>{head}</b><br>{lines}</qt>"
