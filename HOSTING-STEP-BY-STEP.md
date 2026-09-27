# Host Barakah Screen for free (phone + any PC)

You will do **4 blocks**:

1. Keep the project files ready  
2. Put them on GitHub (free)  
3. Connect GitHub to Render (free https website)  
4. Open the public link on your phone

No coding after that. First open after the site sleeps can take up to 1 minute.

---

## STEP 1 — Files you must have in one folder

Unzip `barakah-screen-pwa.zip`. Inside `halal-screener` you need at least:

| File | Why |
|---|---|
| `server.py` | The website engine |
| `static/index.html` | Halal screen page |
| `static/ideas.html` | Top-10 bar chart |
| `static/manifest.json` | Phone “app” icon |
| `static/sw.js` | Offline cache |
| `static/icon.svg` | Icon |
| `Procfile` | Tells Render: run `python server.py` |
| `requirements.txt` | Tells Render this is Python (no extra packages) |
| `runtime.txt` | Python 3.11 |
| `render.yaml` | Optional Render settings |

Do **not** upload the huge `cache/` folder if you have one.

---

## STEP 2 — Create a GitHub account and a repository

1. Open https://github.com/signup and create a free account.  
2. Sign in. Click the **+** (top right) → **New repository**.  
3. Repository name: `barakah-screen`  
4. Public is fine for this educational app.  
5. Do **not** tick “Add a README” (you already have files).  
6. Click **Create repository**.

### Upload files without using Git (easiest)

1. On the empty repo page click **uploading an existing file**.  
2. Drag **everything inside** the `halal-screener` folder (not the zip, the files).  
   You should see `server.py` at the top level of the repo, not inside another extra folder.  
3. If GitHub only lets you drop files (not folders), click **Add file → Create new file** is not needed.  
   Use **Upload files**, then drag the `static` folder as well. GitHub web upload supports folders in most browsers.  
4. Commit message: `first upload`  
5. Click **Commit changes**.

Check the repo looks like this:

```
barakah-screen/
  server.py
  Procfile
  requirements.txt
  runtime.txt
  render.yaml
  README.md
  static/
    index.html
    ideas.html
    manifest.json
    sw.js
    icon.svg
```

If `server.py` is buried in `halal-screener/halal-screener/`, Render will fail. Move files up.

---

## STEP 3 — Create the free website on Render

1. Open https://render.com and **Sign up** with the **same GitHub** account.  
2. Confirm your email if asked.  
3. Dashboard → **New +** → **Web Service**.  
4. Connect GitHub if asked → allow Render to see `barakah-screen`.  
5. Select the `barakah-screen` repository.  
6. Fill the form:

   | Field | Value |
   |---|---|
   | Name | `barakah-screen` |
   | Region | any (Oregon or Frankfurt is fine) |
   | Branch | `main` (or `master`) |
   | Runtime | **Python 3** |
   | Build Command | `true` |
   | Start Command | `python server.py` |
   | Instance type | **Free** |

7. Click **Deploy Web Service**.  
8. Wait 2–5 minutes. Status should become **Live**.  
9. Copy the URL on the page, like:  
   `https://barakah-screen.onrender.com`

If deploy fails, open **Logs**. Usual fixes:

- Start command must be exactly `python server.py`  
- `server.py` must be in the **root** of the GitHub repo  
- Free plan sometimes queues; wait and click **Manual Deploy → Deploy latest commit**

---

## STEP 4 — Open it on phone or another PC

Replace with *your* Render URL:

- Halal screen: `https://YOUR-NAME.onrender.com/`  
- Ideas / bars: `https://YOUR-NAME.onrender.com/ideas.html`

On iPhone: Safari → Share → **Add to Home Screen**.  
On Android: Chrome menu → **Add to Home screen**.

### Sleeping (free plan)

After ~15 minutes with nobody visiting, Render **turns the site off**.  
The next visit shows a loading page for 30–60 seconds. That is normal and free.

---

## STEP 5 — Optional: keep it on your own PC instead (no Render)

Only if you do not want GitHub.

1. On the PC, keep `python server.py` running.  
2. Install Cloudflare tunnel (one-time):  
   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/  
3. In a second Command Prompt:

   ```text
   cloudflared tunnel --url http://127.0.0.1:8787
   ```

4. It prints a `https://….trycloudflare.com` link. That works on your phone **as long as the PC stays awake**.

Same Wi‑Fi only (no public internet):

```text
http://YOUR-PC-LAN-IP:8787
```

Find the IP on Windows: `ipconfig` → IPv4 Address.

---

## What you do NOT need

- No paid domain  
- No Apple / Google developer account  
- No `pip install`  
- No database  
- Do not share `127.0.0.1` — that is only the computer that is running Python

---

## After it is live — first use

1. Open the main page, set max price, Screen watchlist once (slow).  
2. Open `/ideas.html`, leave auto-refresh on.  
3. First Ideas load of the day is slow (halal list). Later minutes are faster.
