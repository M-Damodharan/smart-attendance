"""Turso LibSQL Cloud Database Adapter for Smart Attendance.
Provides seamless drop-in SQLite compatibility connecting to Turso Cloud over HTTP pipeline.
Enables permanent 24/7 cloud persistence on Render Free Tier with zero data loss.
"""
import os
import json
import time
import base64
import sqlite3
import threading
from typing import Any, List, Optional, Sequence, Union
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Global reusable HTTP Keep-Alive session pool
_SESSION_POOL = None

# Micro-caching for near-instant repeat query resolution (4.0s TTL)
_READ_CACHE_LOCK = threading.Lock()
_READ_CACHE = {}
_READ_CACHE_TTL = 4.0  # seconds


def clear_turso_cache():
    """Immediately invalidates all cached query results."""
    with _READ_CACHE_LOCK:
        _READ_CACHE.clear()


def _get_session() -> requests.Session:
    global _SESSION_POOL
    if _SESSION_POOL is None:
        s = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=100,
            max_retries=Retry(total=2, backoff_factor=0.2, status_forcelist=[502, 503, 504])
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _SESSION_POOL = s
    return _SESSION_POOL


class TursoRow(dict):
    """Drop-in replacement for sqlite3.Row supporting key and index access."""
    def __init__(self, cols: List[str], values: List[Any]):
        super().__init__()
        self._cols = cols
        self._values = values
        for c, v in zip(cols, values):
            self[c] = v

    def __getitem__(self, key: Union[str, int]) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def keys(self) -> List[str]:
        return self._cols

    def values(self) -> List[Any]:
        return self._values


class TursoCursor:
    def __init__(self, conn, rows: List[TursoRow], last_insert_rowid: Optional[int], affected_row_count: int, cols: Optional[List[str]] = None):
        self._conn = conn
        self._rows = rows
        self._index = 0
        self.lastrowid = last_insert_rowid
        self.rowcount = affected_row_count
        self.columns = cols or (rows[0].keys() if rows else [])
        self.description = tuple((c, None, None, None, None, None, None) for c in self.columns) if self.columns else None

    def __iter__(self):
        return iter(self._rows)

    def fetchone(self) -> Optional[TursoRow]:
        if self._index < len(self._rows):
            row = self._rows[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self) -> List[TursoRow]:
        remaining = self._rows[self._index:]
        self._index = len(self._rows)
        return remaining

    def fetchmany(self, size: int = 1) -> List[TursoRow]:
        end = min(self._index + size, len(self._rows))
        chunk = self._rows[self._index:end]
        self._index = end
        return chunk

    def close(self):
        self._rows = []


class TursoConnection:
    def __init__(self, host: str, token: str):
        if host.startswith("libsql://"):
            host = host.replace("libsql://", "")
        elif host.startswith("https://"):
            host = host.replace("https://", "")
        self.host = host
        self.token = token
        self.url = f"https://{self.host}/v2/pipeline"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def _convert_param(self, p: Any) -> dict:
        if p is None:
            return {"type": "null"}
        elif isinstance(p, bool):
            return {"type": "integer", "value": "1" if p else "0"}
        elif isinstance(p, int):
            return {"type": "integer", "value": str(p)}
        elif isinstance(p, float):
            return {"type": "float", "value": p}
        elif isinstance(p, bytes):
            return {"type": "blob", "base64": base64.b64encode(p).decode("ascii")}
        else:
            return {"type": "text", "value": str(p)}

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None, bypass_cache: bool = False) -> TursoCursor:
        clean_sql = sql.strip()
        # Ignore local SQLite pragmas that are not needed on Turso Cloud
        if clean_sql.upper().startswith("PRAGMA") and ("JOURNAL_MODE" in clean_sql.upper() or "FOREIGN_KEYS" in clean_sql.upper() or "BUSY_TIMEOUT" in clean_sql.upper()):
            return TursoCursor(self, [], None, 0)

        is_select = clean_sql.upper().startswith("SELECT")
        is_write = any(clean_sql.upper().startswith(w) for w in ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"))

        # Invalidate read cache immediately on any write operation
        if is_write:
            clear_turso_cache()

        # Check in-memory read cache for repeat SELECT queries
        cache_key = None
        if is_select and not bypass_cache and "RANDOM()" not in clean_sql.upper() and clean_sql != "SELECT 1":
            cache_key = (clean_sql, tuple(str(p) for p in params) if params else ())
            now_ts = time.time()
            with _READ_CACHE_LOCK:
                cached = _READ_CACHE.get(cache_key)
                if cached and (now_ts - cached["ts"] < _READ_CACHE_TTL):
                    cloned_rows = [TursoRow(list(r.keys()), list(r.values())) for r in cached["rows"]]
                    return TursoCursor(self, cloned_rows, cached["lastrowid"], cached["affected"], cols=cached["cols"])

        stmt = {"sql": sql}
        if params is not None:
            stmt["args"] = [self._convert_param(p) for p in params]

        payload = {"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]}
        session = _get_session()
        try:
            resp = session.post(self.url, data=json.dumps(payload), headers=self.headers, timeout=10)
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"Turso Cloud request failed: {e}") from e

        results = data.get("results", [])
        if not results:
            return TursoCursor(self, [], None, 0)

        first_res = results[0]
        if first_res.get("type") == "error":
            err = first_res.get("error", {})
            msg = err.get("message", "Unknown error")
            if any(k in msg.lower() for k in ("unique constraint", "primary key", "foreign key", "check constraint")):
                raise sqlite3.IntegrityError(f"Turso Cloud Integrity Error: {msg}")
            raise sqlite3.OperationalError(f"Turso Cloud SQL Error: {msg}")

        resp_obj = first_res.get("response", {})
        result_obj = resp_obj.get("result", {})

        cols = [c.get("name", f"col_{i}") for i, c in enumerate(result_obj.get("cols", []))]
        raw_rows = result_obj.get("rows", [])
        parsed_rows: List[TursoRow] = []

        for r in raw_rows:
            vals = []
            for item in r:
                t = item.get("type")
                val = item.get("value")
                if t == "integer":
                    vals.append(int(val))
                elif t == "float":
                    vals.append(float(val))
                elif t == "null":
                    vals.append(None)
                elif t == "blob":
                    vals.append(base64.b64decode(item.get("base64", "")))
                else:
                    vals.append(val)
            parsed_rows.append(TursoRow(cols, vals))

        last_id = result_obj.get("last_insert_rowid")
        affected = result_obj.get("affected_row_count", 0)

        # Store in read cache
        if is_select and cache_key:
            with _READ_CACHE_LOCK:
                if len(_READ_CACHE) > 500:
                    _READ_CACHE.clear()
                _READ_CACHE[cache_key] = {
                    "rows": parsed_rows,
                    "lastrowid": int(last_id) if last_id is not None else None,
                    "affected": affected,
                    "cols": cols,
                    "ts": time.time(),
                }

        cursor_rows = [TursoRow(list(r.keys()), list(r.values())) for r in parsed_rows]
        return TursoCursor(self, cursor_rows, int(last_id) if last_id is not None else None, affected, cols=cols)

    def clear_cache(self):
        clear_turso_cache()

    def executescript(self, script: str):
        statements = [s.strip() for s in script.split(";") if s.strip()]
        for stmt in statements:
            self.execute(stmt)

    def commit(self):
        """Turso HTTP executes each pipeline as an atomic transaction."""
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def connect_turso(host: str, token: str) -> TursoConnection:
    return TursoConnection(host, token)
