import os
import re

def patch_files():
    # 1. master_sync.py
    file_path = "master_sync.py"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        content = content.replace(
            "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session",
            "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session, get_yf_ticker, YFinanceCircuitOpenError"
        )
        content = content.replace(
            'ticker = yf.Ticker(f"{symbol}.NS", session=get_yf_session())',
            'ticker = get_yf_ticker(f"{symbol}.NS", source="master_sync")'
        )
        content = content.replace(
            "except Exception as exc:",
            "except YFinanceCircuitOpenError as exc:\n            log.warning(\"MasterSync yfinance circuit open for %s: %s\", symbol, exc)\n            return None\n        except Exception as exc:"
        )
        content = content.replace(
            "yf_record_failure()",
            'yf_record_failure(source="master_sync")'
        )
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

    # 2. live_feed.py
    file_path = "live_feed.py"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        content = content.replace(
            "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session",
            "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session, get_yf_ticker, YFinanceCircuitOpenError"
        )
        content = content.replace(
            'ticker = yf.Ticker(f"{clean}.NS", session=get_yf_session())',
            'ticker = get_yf_ticker(f"{clean}.NS", source="live_feed")'
        )
        content = content.replace(
            'df = yf.Ticker(f"{clean}.NS", session=get_yf_session()).history(period="1y")',
            'df = get_yf_ticker(f"{clean}.NS", source="live_feed").history(period="1y")'
        )
        content = content.replace(
            "except Exception as exc:",
            "except YFinanceCircuitOpenError as exc:\n        log.debug(\"live_feed yfinance circuit open for %s: %s\", clean, exc)\n    except Exception as exc:"
        )
        content = content.replace(
            "except Exception:",
            "except YFinanceCircuitOpenError:\n                pass\n            except Exception:"
        )
        content = content.replace(
            "yf_record_failure()",
            'yf_record_failure(source="live_feed")'
        )
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

    print("Patched master_sync and live_feed.")

if __name__ == "__main__":
    patch_files()
