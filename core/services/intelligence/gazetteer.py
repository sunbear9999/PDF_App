"""Local place-name gazetteer built from GeoNames (CC BY 4.0).

The build keeps populated places (``cities500``), first-level administrative
areas and countries, with their Latin-script alternate names, in a small
SQLite file keyed by a folded name (case, accents and spacing removed).
Lookups only answer "which places carry this name"; whether a mention *is*
that place is decided elsewhere from document evidence (see
``entities/gazetteer_source.py``).
"""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import threading
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

MIN_PLACES = 1000             # a smaller build means a truncated or wrong file
MAX_NAME_LENGTH = 60
_LATIN = re.compile(r"^[A-Za-zÀ-ɏḀ-ỿ'’ .\-]+$")
_CODE = re.compile(r"^[A-Z0-9]{2,4}$")          # IATA / ISO codes listed as alternate names
BUSY_WINDOW_S = 10.0
CITY_COLUMNS = 19


def fold(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.casefold().replace("’", "'"))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[.\-]", " ", plain).split())


@dataclass(frozen=True)
class Place:
    geonameid: int
    name: str
    feature_code: str
    country: str
    admin1: str
    population: int


def build_geonames(files: Dict[str, Path], out_path: Path) -> int:
    """Build the index from ``cities`` (zip), ``admin1`` and ``countries`` files."""
    con = sqlite3.connect(str(out_path))
    try:
        con.executescript(
            "CREATE TABLE places(id INTEGER PRIMARY KEY, name TEXT, feature TEXT, country TEXT, admin1 TEXT,"
            " population INTEGER);"
            "CREATE TABLE names(key TEXT, id INTEGER);")
        count = _load_cities(con, files["cities"]) + _load_admin1(con, files["admin1"])
        count += _load_countries(con, files["countries"])
        if count < MIN_PLACES:
            raise ValueError(f"GeoNames build has only {count} places.")
        con.execute("CREATE INDEX names_key ON names(key)")
        con.commit()
        con.execute("VACUUM")
        return count
    finally:
        con.close()


def _insert(con, place_id, name, feature, country, admin1, population, alternates=()):
    con.execute("INSERT OR IGNORE INTO places VALUES (?,?,?,?,?,?)",
                (place_id, name, feature, country, admin1, population))
    keys = {fold(name)}
    for alternate in alternates:
        if alternate and len(alternate) <= MAX_NAME_LENGTH and _LATIN.match(alternate) and not _CODE.match(alternate):
            keys.add(fold(alternate))
    con.executemany("INSERT INTO names VALUES (?,?)", [(key, place_id) for key in keys if len(key) > 1])


def _load_cities(con, path: Path) -> int:
    count = 0
    with zipfile.ZipFile(path) as archive:
        member = next(n for n in archive.namelist() if n.endswith(".txt"))
        with archive.open(member) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                row = line.rstrip("\n").split("\t")
                if len(row) != CITY_COLUMNS:
                    raise ValueError("Unexpected GeoNames cities format.")
                _insert(con, int(row[0]), row[1], row[7], row[8], row[10], int(row[14] or 0),
                        [row[2], *row[3].split(",")])
                count += 1
    return count


def _load_admin1(con, path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8") as stream:
        for row in csv.reader(stream, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) == 4 and "." in row[0]:
                country, admin1 = row[0].split(".", 1)
                _insert(con, int(row[3]), row[1], "ADM1", country, admin1, 0, [row[2]])
                count += 1
    return count


def _load_countries(con, path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = line.rstrip("\n").split("\t")
            if line.startswith("#") or len(row) < 17 or not row[16].isdigit():
                continue
            _insert(con, int(row[16]), row[4], "PCLI", row[0], "", int(row[7] or 0))
            count += 1
    return count


class Gazetteer:
    """Read-only lookups; loads lazily and can be released by the memory manager."""

    def __init__(self, index_dir: str) -> None:
        self.path = os.path.join(index_dir, "index.sqlite")
        self._con = None
        self._lock = threading.Lock()
        self._last_used = 0.0
        self._cache: Dict[str, List[Place]] = {}

    def is_available(self) -> bool:
        return os.path.isfile(self.path)

    def lookup(self, name: str) -> List[Place]:
        key = fold(name)
        if key in self._cache:
            return self._cache[key]
        with self._lock:
            self._last_used = time.monotonic()
            if self._con is None:
                if not self.is_available():
                    return []
                self._con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            rows = self._con.execute(
                "SELECT p.id, p.name, p.feature, p.country, p.admin1, p.population FROM names n "
                "JOIN places p ON p.id = n.id WHERE n.key = ?", (key,)).fetchall()
        places = [Place(*row) for row in rows]
        self._cache[key] = places
        return places

    def reload(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
            self._con = None
            self._cache.clear()

    def release(self, _level=None) -> int:
        freed = self.memory_bytes()
        self.reload()
        return freed

    def memory_bytes(self) -> int:
        return 0 if self._con is None else 200 * len(self._cache) + 8 * 1024 * 1024

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used if self._last_used else 0.0

    def busy(self) -> bool:
        return self._con is not None and self.idle_seconds() < BUSY_WINDOW_S
