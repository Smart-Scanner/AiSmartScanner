import os
import re

def refactor_yf_guard():
    file_path = r"intelligence\yf_guard.py"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update yf_record_failure to support source and fix the state machine bug
    old_record_failure = '''def yf_record_failure() -> None:
    """
    Call this on any yfinance exception.
    Increments failure count; opens circuit when threshold exceeded.
    """
    global _failure_count, _cooldown_until
    with _lock:
        _failure_count += 1
        if _failure_count >= _THRESHOLD:
            _cooldown_until = time.time() + _COOLDOWN
            log.warning(
                "yf_guard: Circuit OPEN after %d failures. "
                "yfinance suspended for %.0fs.",
                _failure_count, _COOLDOWN,
            )'''

    new_record_failure = '''def yf_record_failure(source: str = "unknown") -> None:
    """
    Call this on any yfinance exception.
    Increments failure count; opens circuit when threshold exceeded.
    """
    global _failure_count, _cooldown_until
    with _lock:
        _failure_count += 1
        if _failure_count == _THRESHOLD:
            _cooldown_until = time.time() + _COOLDOWN
            log.warning(
                "yf_guard: Circuit OPEN after %d failures. "
                "yfinance suspended for %.0fs. Source: %s",
                _failure_count, _COOLDOWN, source
            )
        elif _failure_count > _THRESHOLD:
            now = time.time()
            if now >= _cooldown_until:
                # Probe failed. Reset cooldown.
                _cooldown_until = now + _COOLDOWN
                log.warning("yf_guard: HALF-OPEN probe failed from source %s. Circuit re-opened for %.0fs.", source, _COOLDOWN)
            else:
                log.debug("yf_guard: Unguarded call failed from %s while circuit OPEN. Cooldown unchanged.", source)'''

    content = content.replace(old_record_failure, new_record_failure)

    # 2. Add get_yf_ticker and get_yf_download wrappers
    new_wrappers = '''
import yfinance as yf

class YFinanceCircuitOpenError(RuntimeError):
    pass

def get_yf_ticker(symbol: str, source: str = "unknown"):
    """
    Centralized wrapper for yf.Ticker.
    Raises YFinanceCircuitOpenError if circuit is open.
    """
    if not yf_is_available():
        log.debug("yf_guard: Rejected yf.Ticker for %s from source %s (Circuit OPEN)", symbol, source)
        raise YFinanceCircuitOpenError(f"yf_guard circuit OPEN. Ticker fetch aborted for {symbol} (source: {source})")
    
    session = get_yf_session()
    return yf.Ticker(symbol, session=session)

def get_yf_download(tickers, source: str = "unknown", **kwargs):
    """
    Centralized wrapper for yf.download.
    Raises YFinanceCircuitOpenError if circuit is open.
    """
    if not yf_is_available():
        log.debug("yf_guard: Rejected yf.download for %s from source %s (Circuit OPEN)", tickers, source)
        raise YFinanceCircuitOpenError(f"yf_guard circuit OPEN. Download aborted for {tickers} (source: {source})")
    
    session = get_yf_session()
    kwargs['session'] = session
    return yf.download(tickers, **kwargs)
'''

    content = content + new_wrappers

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print("Refactored yf_guard.py")

if __name__ == "__main__":
    refactor_yf_guard()
