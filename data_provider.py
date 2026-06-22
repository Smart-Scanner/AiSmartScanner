import os
import time
import logging
from enum import Enum
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import pyotp
from SmartApi import SmartConnect

class ProviderState(Enum):
    ACTIVE = "ACTIVE"
    COOLDOWN = "COOLDOWN"
    FAILED = "FAILED"

class ProviderStats:
    def __init__(self):
        self.success_count = 0
        self.failure_count = 0
        self.rate_limits_hit = 0
        self.avg_latency_ms = 0.0
        self.chunks_processed = 0
        self.symbols_processed = 0
        self.last_success_at = None
        self.consecutive_failures = 0
        self.cooldown_until = None
        
        # Internal for calculating moving average
        self._total_latency_ms = 0.0
        self._latency_samples = 0
        
    def record_success(self, latency_ms: float):
        self.success_count += 1
        self.symbols_processed += 1
        self.last_success_at = datetime.now().isoformat() + "Z"
        self.consecutive_failures = 0
        
        self._total_latency_ms += latency_ms
        self._latency_samples += 1
        self.avg_latency_ms = self._total_latency_ms / self._latency_samples
        
    def record_failure(self, is_429: bool = False):
        self.failure_count += 1
        self.consecutive_failures += 1
        if is_429:
            self.rate_limits_hit += 1

    def to_dict(self):
        return {
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "rate_limits_hit": self.rate_limits_hit,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "chunks_processed": self.chunks_processed,
            "symbols_processed": self.symbols_processed,
            "last_success_at": self.last_success_at,
            "consecutive_failures": self.consecutive_failures,
            "cooldown_until": self.cooldown_until.isoformat() + "Z" if self.cooldown_until else None
        }

class BrokerProvider:
    def __init__(self, name: str, config: dict):
        self.name = name
        self.role = config.get("ROLE", "RESEARCH").upper()
        self.state = ProviderState.ACTIVE
        self.stats = ProviderStats()
        self.in_use = False  # Scheduler lock

    def login(self) -> bool:
        raise NotImplementedError()

    def fetch_historical(self, symboltoken: str, exchange: str = "NSE") -> Optional[list]:
        if self.role == "EXECUTION":
            raise RuntimeError(f"[{self.name}] FATAL: Cannot fetch historical data using an EXECUTION provider!")
            
        if self.state == ProviderState.COOLDOWN:
            if self.stats.cooldown_until and datetime.now() > self.stats.cooldown_until:
                logging.info(f"[{self.name}] Cooldown expired. Recovering to ACTIVE.")
                self.state = ProviderState.ACTIVE
                self.stats.consecutive_failures = 0
                self.stats.cooldown_until = None
            else:
                return None
        
        if self.state == ProviderState.FAILED:
            return None

        return self._do_fetch(symboltoken, exchange)

    def _do_fetch(self, symboltoken: str, exchange: str) -> Optional[list]:
        raise NotImplementedError()

    def _handle_failure(self, is_429: bool):
        self.stats.record_failure(is_429=is_429)
        if self.stats.consecutive_failures >= 5:
            logging.warning(f"[{self.name}] 5 consecutive failures! Triggering 60s COOLDOWN.")
            self.state = ProviderState.COOLDOWN
            self.stats.cooldown_until = datetime.now() + timedelta(seconds=60)


class AngelProvider(BrokerProvider):
    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self.api_key = config.get("API_KEY")
        self.client_id = config.get("CLIENT_ID")
        self.mpin = config.get("MPIN")
        self.totp_secret = config.get("TOTP")
        self.api = None

    def login(self) -> bool:
        try:
            self.api = SmartConnect(api_key=self.api_key)
            totp = pyotp.TOTP(self.totp_secret).now()
            res = self.api.generateSession(self.client_id, self.mpin, totp)
            if res and res.get("status"):
                logging.info(f"[{self.name}] Logged in successfully.")
                self.state = ProviderState.ACTIVE
                return True
            else:
                logging.error(f"[{self.name}] Login failed: {res}")
                self.state = ProviderState.FAILED
                return False
        except Exception as e:
            logging.error(f"[{self.name}] Exception during login: {e}")
            self.state = ProviderState.FAILED
            return False

    def _do_fetch(self, symboltoken: str, exchange: str) -> Optional[list]:
        start_time = time.time()
        try:
            params = {
                "exchange": exchange,
                "symboltoken": symboltoken,
                "interval": "ONE_DAY",
                "fromdate": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M"),
                "todate": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
            res = self.api.getCandleData(params)
            latency_ms = (time.time() - start_time) * 1000
            
            if res and res.get("status") and res.get("data"):
                self.stats.record_success(latency_ms)
                return res["data"]
            elif res and res.get("errorcode") == "AB1019":
                self._handle_failure(is_429=True)
                return None
            else:
                self._handle_failure(is_429=False)
                return None
        except Exception as e:
            msg = str(e)
            if "Access denied" in msg or "access rate" in msg.lower():
                self._handle_failure(is_429=True)
            else:
                self._handle_failure(is_429=False)
            return None


class ProviderManager:
    def __init__(self):
        self.providers: Dict[str, BrokerProvider] = {}

    def discover_providers(self):
        # Scan env for unique provider prefixes
        prefixes = set()
        for key in os.environ:
            if key.startswith("PROVIDER_") and key.endswith("_TYPE"):
                prefix = key.replace("_TYPE", "")
                prefixes.add(prefix)

        for prefix in sorted(list(prefixes)):
            ptype = os.getenv(f"{prefix}_TYPE", "").upper()
            config = {
                "ROLE": os.getenv(f"{prefix}_ROLE", "RESEARCH"),
                "API_KEY": os.getenv(f"{prefix}_API_KEY", ""),
                "CLIENT_ID": os.getenv(f"{prefix}_CLIENT_ID", ""),
                "MPIN": os.getenv(f"{prefix}_MPIN", ""),
                "TOTP": os.getenv(f"{prefix}_TOTP", "")
            }
            
            if ptype == "ANGEL":
                provider = AngelProvider(prefix, config)
                self.providers[prefix] = provider
                logging.info(f"Discovered {prefix} (Type: {ptype}, Role: {config['ROLE']})")
                
    def initialize_all(self):
        for name, p in self.providers.items():
            p.login()

    def acquire_active_provider(self, required_role="RESEARCH") -> Optional[BrokerProvider]:
        """
        Scheduler lock: Finds an ACTIVE, unused provider with the right role.
        """
        for name, p in self.providers.items():
            # Check cooldown recovery
            if p.state == ProviderState.COOLDOWN and p.stats.cooldown_until:
                if datetime.now() > p.stats.cooldown_until:
                    logging.info(f"[{name}] Cooldown expired. Recovering to ACTIVE.")
                    p.state = ProviderState.ACTIVE
                    p.stats.consecutive_failures = 0
                    p.stats.cooldown_until = None
            
            if p.state == ProviderState.ACTIVE and not p.in_use and p.role == required_role:
                p.in_use = True
                return p
        return None

    def release_provider(self, provider: BrokerProvider):
        provider.in_use = False

    def get_telemetry(self) -> dict:
        telemetry = {}
        for name, p in self.providers.items():
            telemetry[name] = {
                "state": p.state.value,
                "role": p.role,
                "in_use": p.in_use,
                **p.stats.to_dict()
            }
        return telemetry

provider_manager = ProviderManager()
provider_manager.discover_providers()

