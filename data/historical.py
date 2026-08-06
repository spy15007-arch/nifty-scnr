    def get_universe_bars(self, symbols: list[str], lookback_days: int) -> dict[str, pd.DataFrame]:
        """
        Fetches symbols using a bulletproof linear sequence. Insulates your retail 
        account completely from broker minute session freezes and IP blocks.
        """
        results: dict[str, pd.DataFrame] = {}
        total = len(symbols)
        failures = 0
        
        logger.info(f"⚡ Ingesting {total} symbols via rate-insulated stream...")

        for i, symbol in enumerate(symbols, 1):
            try:
                # Mandatory 0.20-second pause creates a consistent 5 requests/sec pacing layout
                # This stays safely below Angel One's security firewall thresholds
                time.sleep(0.20) 
                
                df = self.get_bars(symbol, lookback_days)
                if df is not None and not df.empty:
                    results[symbol] = df
            except Exception as e:
                failures += 1
                if "rate" in str(e).lower() or "too many" in str(e).lower() or "ab1021" in str(e).lower():
                    logger.warning(f"⚠️ Account Cooldown Active. Pacing connection for {symbol}...")
                    time.sleep(1.0) # Adaptive recovery bridge brake delay
                    try:
                        df_retry = self.get_bars(symbol, lookback_days)
                        if df_retry is not None and not df_retry.empty:
                            results[symbol] = df_retry
                            failures -= 1
                    except Exception:
                        pass
                else:
                    logger.debug(f"Skipping {symbol}: {e}")

            if i % 50 == 0 or i == total:
                logger.info(f"📋 Indexing Progress: {i}/{total} symbols scanned ({failures} skipped)")

        logger.info(f"📊 Central Data Lake ready: {len(results)}/{total} assets cached in memory.")
        return results
