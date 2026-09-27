# Barakah Screen — self-hosted halal US stock screener

Educational web app. Screens **US-listed SEC filers** using:

1. **Business-activity gate** — SIC code + company-name keywords (banks, insurance, alcohol, tobacco, gambling, many weapons names).
2. **Financial ratios** under three *styles* of published methodologies:
   - **AAOIFI-style:** debt / market cap < 30%, cash+securities / market cap < 30%, impure income < 5%
   - **MSCI Islamic-style:** same idea vs **total assets**, plus receivables+cash / assets
   - **DJIM-style:** ~33% caps vs market cap

Data: official **SEC EDGAR XBRL** (debt, cash, assets, revenue, interest-like tags) + last **Yahoo Finance** price for market cap.

This is **not a fatwa**, not licensed index data, and not investment advice. Segment-level haram revenue (a few percent of a grocery chain’s alcohol aisle, a cloud company’s interest income footnotes, weapons exposure inside an industrial conglomerate) is often **missing from XBRL** and still needs a 10-K read.

## Run

```bash
cd halal-screener
python3 server.py
```

Open http://127.0.0.1:8787

On a phone on the same Wi‑Fi as that computer, use the computer’s LAN IP instead:
`http://192.168.x.x:8787` then **Add to Home Screen**.

Optional: `PORT=8080 python3 server.py`

## Phone app (PWA)

This is an installable **Progressive Web App**, not an App Store / Play Store listing.

- **iPhone (Safari):** Share → Add to Home Screen
- **Android (Chrome):** menu → Add to Home screen / Install app

A store APK/IPA needs your own Apple or Google developer account.

No extra pip packages. Python 3.10+ stdlib only.

## Host for free (phone + any PC)

Easiest free public URL: **Render**.

1. Create a free account at https://render.com
2. New → **Web Service** → deploy from a GitHub repo of this `halal-screener` folder  
   (or zip-upload if they still allow it)
3. Settings:
   - Runtime: **Python**
   - Build command: leave empty (or `true`)
   - Start command: `python server.py`
4. Render sets `PORT` for you. After deploy you get a link like  
   `https://barakah-xxxx.onrender.com`  
   Ideas page: `https://barakah-xxxx.onrender.com/ideas.html`

Free Render apps **sleep after ~15 minutes** of no traffic. First open after sleep can take 30–60 seconds. That is normal on the free plan.

Other free options:

| Option | Good for | Catch |
|---|---|---|
| [Render](https://render.com) | Real https link on phone | Sleeps when idle |
| [Railway](https://railway.app) trial | Same idea | Card often required |
| [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) | Your PC stays the host | PC must stay on |
| Same Wi‑Fi LAN IP | Phone at home | Not from outside the house |

Do not use a public tunnel if you put private keys in the app. This project has none.

Yahoo and SEC may rate-limit a public URL more than your home PC.

## API

- `GET /api/search?q=NVDA`
- `GET /api/screen?symbol=AAPL`
- `GET /api/batch` — first 20 watchlist names
- `GET /api/batch?symbols=AAPL,MSFT,JPM`

Filings are cached under `cache/` (12–24h). Prices cache ~2 minutes.

## What you should still do yourself

- Confirm the latest 10-K notes for interest income and non-permissible segments.
- Purify dividends by the impure-income fraction (when known).
- Avoid margin, short selling, and interest-bearing cash sweeps.
- Re-screen after earnings — market-cap standards move when the price moves.
