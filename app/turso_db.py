"""Turso LibSQL Cloud Database Adapter for Smart Attendance.
Provides seamless drop-in SQLite compatibility connecting to Turso Cloud over HTTP pipeline.
Enables permanent 24/7 cloud persistence on Render Free Tier with zero data loss.
"""
import os
import json
import base64
import sqlite3
import urllib.request
import urllib.error
from typing import Any, List, Optional, Sequence, Union


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
    def __init__(self, conn, rows: List[TursoRow], last_insert_rowid: Optional[int], affected_row_count: int):
        self._conn = conn
        self._rows = rows
        self._index = 0
        self.lastrowid = last_insert_rowid
        self.rowcount = affected_row_count

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

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> TursoCursor:
        # Ignore local SQLite pragmas that are not needed on Turso Cloud
        clean_sql = sql.strip()
        if clean_sql.upper().startswith("PRAGMA") and ("JOURNAL_MODE" in clean_sql.upper() or "FOREIGN_KEYS" in clean_sql.upper() or "BUSY_TIMEOUT" in clean_sql.upper()):
            return TursoCursor(self, [], None, 0)

        stmt = {"sql": sql}
        if params is not None:
            stmt["args"] = [self._convert_param(p) for p in params]

        payload = {"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]}
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
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
        return TursoCursor(self, parsed_rows, int(last_id) if last_id is not None else None, affected)

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
