"""
Sector clustering: when 3+ stocks in the SAME NSE sectoral index get
flagged by the scanner on the same day, that's real evidence of a
sector-wide catalyst (institutional money rotating into a theme) -
not isolated single-stock noise. Sector-wide moves are exactly what
tends to produce bigger, more sustained percentage gains than an
isolated technical setup.

Uses NSE Indices Limited's own sectoral index constituent lists
(niftyindices.com) - the same classification the market itself uses
when talking about "sector rotation," rather than an arbitrary
hardcoded list. Fetched fresh each run (not hardcoded) so it never
goes stale as sector index membership gets periodically rebalanced.

FAULT TOLERANT: only 2 of these URL slugs (nifty500, niftyauto) were
directly confirmed working at build time; the rest follow the same
verified naming pattern but weren't individually checked. Any sector
that 404s or fails is just skipped - the feature still works with
whichever sectors DID fetch successfully, rather than the whole
feature breaking over one wrong slug.
"""
from __future__ import annotations
import logging
from collections import Counter

logger = logging.getLogger(__name__)

SECTOR_INDEX_SLUGS = {
    "IT": "niftyitlist",
    "Auto": "niftyautolist",
    "Pharma": "niftypharmalist",
    "FMCG": "niftyfmcglist",
    "Bank": "niftybanklist",
    "Metal": "niftymetallist",
    "Realty": "niftyrealtylist",
    "Energy": "niftyenergylist",
    "Media": "niftymedialist",
    "PSU Bank": "niftypsubanklist",
    "Private Bank": "niftyprivatebanklist",
    "Financial Services": "niftyfinservicelist",
    "Healthcare": "niftyhealthcarelist",
    "Oil & Gas": "niftyoilgaslist",
}

BASE_URL = "https://www.niftyindices.com/IndexConstituent"


def fetch_sector_map(headers: dict | None = None, timeout: int = 15) -> dict[str, str]:
    """
    Returns {symbol: sector_name} across all sectors that fetched
    successfully. A stock may technically belong to more than one
    sectoral index - first successful match wins, which is fine for
    clustering purposes (we just need A reasonable grouping, not a
    perfectly authoritative single-sector assignment).
    """
    import requests
    import io
    import pandas as pd

    headers = headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    symbol_to_sector: dict[str, str] = {}
    fetched_count = 0

    for sector_name, slug in SECTOR_INDEX_SLUGS.items():
        url = f"{BASE_URL}/ind_{slug}.csv"
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            symbol_col = "Symbol" if "Symbol" in df.columns else None
            if symbol_col is None:
                logger.warning(f"Sector fetch for {sector_name}: no 'Symbol' column found, skipping")
                continue
            symbols = df[symbol_col].dropna().unique().tolist()
            for sym in symbols:
                if sym not in symbol_to_sector:  # first match wins
                    symbol_to_sector[sym] = sector_name
            fetched_count += 1
        except Exception as e:
            logger.warning(f"Sector fetch failed for {sector_name} ({url}): {e} - skipping this sector")
            continue

    logger.info(f"Sector map built: {fetched_count}/{len(SECTOR_INDEX_SLUGS)} sectors fetched, {len(symbol_to_sector)} symbols mapped")
    return symbol_to_sector


def apply_sector_clustering(recs: list, sector_map: dict[str, str], min_cluster: int = 3, boost: float = 1.10) -> list:
    """
    Boosts conviction for candidates whose sector has `min_cluster`+
    OTHER candidates also flagged today. Mutates recs in place and
    returns them re-sorted by the updated probability.
    """
    if not sector_map:
        return recs

    sectors_seen = [sector_map.get(r.symbol) for r in recs if sector_map.get(r.symbol)]
    sector_counts = Counter(sectors_seen)

    for r in recs:
        sector = sector_map.get(r.symbol)
        if sector and sector_counts.get(sector, 0) >= min_cluster:
            r.probability = min(0.99, r.probability * boost)
            r.top_reasons.append(
                f"Sector clustering: {sector_counts[sector]} {sector} stocks flagged together today - "
                f"suggests a genuine sector-wide catalyst, not isolated noise"
            )

    recs.sort(key=lambda r: r.probability, reverse=True)
    return recs
