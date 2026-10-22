# On-time scans: the external timer (10 minutes, one time)

**The problem.** GitHub's built-in schedule is not punctual - your scans have started 4 to 9 hours late. A BTST message has to be on your phone before about 2:30 PM, so the scans are now also started by a free outside timer that calls GitHub at the exact minute. GitHub's own schedule stays in place as a backup, and the program makes sure you never get two copies of the same scan.

**What you need:** your GitHub account (the one that owns `nifty-scnr`) and a free account at https://cron-job.org. The token below is stored **only** inside cron-job.org - never in the repository, never in chat.

---

## Step 1 - make a limited GitHub token (2 minutes)

1. GitHub → your profile picture → **Settings** → **Developer settings** (bottom of the left menu) → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. **Token name:** any name your account has not used yet, for example `nifty-scnr-timer-2026` (it is only a label; nothing depends on it). **Expiration:** 1 year (put a reminder in your calendar to renew it).
3. **Resource owner:** your account. **Repository access:** *Only select repositories* → choose **nifty-scnr** only.
4. **Permissions → Repository permissions → Actions: Read and write.** (Leave everything else as "No access"; "Metadata: Read-only" is added automatically.)
5. **Generate token** and copy it now (it starts with `github_pat_`). GitHub shows it only once.

This token can do exactly one thing: start workflows in this one repository. It cannot read your code or your broker secrets.

## Step 2 - create the BTST timer on cron-job.org (4 minutes)

Sign in → **Create cronjob**.

| Field | Value |
| :-- | :-- |
| Title | `BTST 2:00 PM IST` |
| URL | `https://api.github.com/repos/spy15007-arch/nifty-scnr/actions/workflows/btst-scan.yml/dispatches` |
| Execution schedule | **User-defined**: days **Mon-Fri**, hour **14**, minute **0** |
| Time zone | **Asia/Kolkata** (set in the cronjob's *Schedule* / account settings - it must not be UTC) |

Open **Advanced** and set (in the Headers section each header is two boxes: **Key** = the name before the colon, **Value** = everything after it; for `Authorization` the value is the word `Bearer`, one space, then your token):

| Field | Value |
| :-- | :-- |
| Request method | `POST` |
| Request body | `{"ref":"main","inputs":{"source":"timer"}}` |
| Headers (add each) | `Accept: application/vnd.github+json` |
| | `Authorization: Bearer github_pat_...your token...` |
| | `X-GitHub-Api-Version: 2022-11-28` |
| | `Content-Type: application/json` |
| | `User-Agent: cron-job.org` |

Save. A successful call returns **204 No Content** (cron-job.org shows it as a success).

## Step 3 - create the Swing timer (1 minute)

Duplicate the job above and change only: **Title** `Swing 4:05 PM IST`, **URL** ending in `.../workflows/swing-scan.yml/dispatches`, schedule hour **16**, minute **5**.

The Swing scan reads the day's final candles, so it belongs after the close. It also builds tomorrow's BTST watchlist.

## Step 4 - test it

On cron-job.org press **Test run** on the BTST job. In GitHub → **Actions** you should see a **BTST Scan** run start within a few seconds (during market hours it will send you a real message; outside market hours it sends the "based on the last close" version). If you would rather not get a message, test after the evening scan has run on a non-trading day.

Check tomorrow: the BTST message should arrive around **2:05 PM IST**.

---

## Good to know

* **Two copies never arrive.** If GitHub's own backup schedule or the timer already ran today's scan, the second run stops quietly (the log says why). A BTST run started by GitHub's schedule after 2:20 PM is skipped on purpose - it would be too late to act on.
* **First day.** The first afternoon BTST has no watchlist yet, so it scans the whole market and stops fetching at 2:25 PM (it still messages on time, from whatever it fetched, and says so). Run **Swing Scan** once from the Actions tab (press *Run workflow*) any time after the market closes - that creates the watchlist and saves its candles, and every later BTST is fast (a few live-quote calls instead of one call per stock).
* **Market holidays.** The timer fires on exchange holidays too. The message then says the newest price bar is not from today and lists nothing new.
* **Share counts.** The share counts in the messages assume ₹1 lakh of capital. To change that: GitHub → repository **Settings** → **Secrets and variables** → **Actions** → **Variables** → New repository variable `TRADING_CAPITAL_INR` (for example `500000`).

## If something goes wrong

| What cron-job.org shows | Meaning and fix |
| :-- | :-- |
| 401 | Token wrong or expired - make a new one (Step 1) and paste it into the header |
| 403 | The token lacks **Actions: Read and write**, or it is not allowed on this repository - redo Step 1 |
| 404 | Wrong URL (check `spy15007-arch/nifty-scnr` and `btst-scan.yml`) or the token cannot see the repository |
| 422 | The request body is wrong - it must be exactly `{"ref":"main","inputs":{"source":"timer"}}` |
| 204 but no run in Actions | Look at the repository's **Actions** tab filters; also confirm Actions are enabled for the repository |

**If the token is ever exposed:** GitHub → Settings → Developer settings → Fine-grained tokens → **Revoke** it, then make a new one. Nothing else needs to change.
