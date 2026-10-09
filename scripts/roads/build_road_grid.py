"""T-130 (tasarım §81–§82): Türkiye yol ızgarası ve dağ geçitleri.

`assets/roads/tr-roads.json` üretir. Uygulama rota skorlamasında yol sınıfını,
kötü yüzeyi ve dağ geçitlerini çalışma anında Overpass'a sormak yerine bu
dosyadan okur. Kaynak Geofabrik Türkiye özeti; `npm run roads:update` çalıştırır.

Veri © OpenStreetMap katkıda bulunanlar, ODbL.

Izgara: hücre 0,00225° (enlem) × 0,003° (boylam) ≈ 250 m. Hücre değeri 6 bit:
bit0 motorway, bit1 trunk, bit2 primary, bit3 secondary,
bit4 tertiary/unclassified (hücreden geçen sınıflar), bit5 kötü yüzey.
Kodlama (ikili, base64 olarak JSON'da): satırlar enleme göre artan; satır
başlığı varint(enlem indeksi farkı), varint(hücre sayısı); hücre
varint((boylam indeksi farkı << 6) | değer) — satırın ilk hücresinde fark,
boylam indeksinin kendisidir.

`urban` (T-139, §130): şehir içi hücreler, aynı satır düzeninde ama değersiz (hücre
varint(boylam indeksi farkı)). Hücreden kavşak bağlantı yolu (`*_link`) ya da göbek
(`junction=roundabout/circular`) geçiyorsa ya da hücrede en az `URBAN_RESIDENTIAL_METERS`
konut sokağı (`residential`, `living_street`) varsa şehir içidir; bu hücrelerdeki virajlar
viraj ölçüsüne girmez (rampa, göbek ve sokak dönüşü gerçek viraj değildir). Ayrı liste,
ızgaraya yedinci bit eklemekten küçük: 7 bitlik değer her hücreyi 2 bayta itiyordu
(dosya 3,3 → 4,7 MB).
"""
import base64
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import osmium

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'assets/roads/tr-roads.json'
CACHE = ROOT / '.cache/osm'
PBF_URL = 'https://download.geofabrik.de/europe/turkey-latest.osm.pbf'
USER_AGENT = 'apexvia-osm-data (bhldev.app@gmail.com)'

DLAT = 0.00225
DLON = 0.003
CLASS_BIT = {
    'motorway': 0, 'motorway_link': 0,
    'trunk': 1, 'trunk_link': 1,
    'primary': 2, 'primary_link': 2,
    'secondary': 3, 'secondary_link': 3,
    'tertiary': 4, 'tertiary_link': 4, 'unclassified': 4,
}
BAD_BIT = 5
URBAN_KINDS = {'residential', 'living_street'}
# §130: 250 m'lik hücrede bu kadar konut sokağı şehir dokusu demektir (köy ~200–500 m;
# Türkiye'deki dolu hücrelerin %75'i 509 m'nin altında). 500 ve 1000 de ölçüldü.
URBAN_RESIDENTIAL_METERS = 750
BAD_VALUES = {
    'unpaved', 'gravel', 'fine_gravel', 'dirt', 'ground', 'earth', 'mud', 'sand', 'compacted',
    'bad', 'very_bad', 'horrible', 'very_horrible', 'impassable',
}
# Bir üretimin en az bu kadar hücre ve geçit bulması beklenir (28 Eylül 2026:
# 1,71 milyon hücre); daha azı bozuk/eksik bir kaynak dosyası demektir.
MIN_CELLS = 1_200_000
MIN_PASSES = 100


def download_pbf() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    pbf = CACHE / 'turkey-latest.osm.pbf'
    # Önbellekteki dosyayla yeniden üretim (ör. biçim değişikliğinde verinin kendisi değişmesin).
    if os.environ.get('ROADS_USE_CACHE') == '1' and pbf.exists():
        print(f'Önbellekteki dosya kullanılıyor (ROADS_USE_CACHE): {pbf}')
        return pbf
    request = urllib.request.Request(PBF_URL + '.md5', headers={'User-Agent': USER_AGENT})
    expected = urllib.request.urlopen(request, timeout=60).read().decode().split()[0]
    if pbf.exists() and md5(pbf) == expected:
        print(f'Önbellekteki dosya güncel: {pbf}')
        return pbf
    print(f'İndiriliyor: {PBF_URL}')
    request = urllib.request.Request(PBF_URL, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=600) as response, open(pbf, 'wb') as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    actual = md5(pbf)
    if actual != expected:
        pbf.unlink()
        sys.exit(f'MD5 tutmuyor (beklenen {expected}, gelen {actual}); dosya silindi.')
    return pbf


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def osm_base(pbf: Path) -> str | None:
    header = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING).header()
    stamp = header.get('osmosis_replication_timestamp')
    return stamp or None


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def cells_along(points, add) -> None:
    for (a_lat, a_lon), (b_lat, b_lon) in zip(points, points[1:]):
        # Hücre boyunun yarısından sık örneklenir, çapraz geçişte hücre atlanmaz.
        steps = int(max(abs(b_lat - a_lat) / DLAT, abs(b_lon - a_lon) / DLON) * 2) + 1
        for i in range(steps + 1):
            t = i / steps
            add((math.floor((a_lat + (b_lat - a_lat) * t) / DLAT),
                 math.floor((a_lon + (b_lon - a_lon) * t) / DLON)))


class Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.cells: dict[tuple[int, int], int] = {}
        self.ways = 0
        self.junction_cells: set[tuple[int, int]] = set()
        self.residential: dict[tuple[int, int], float] = {}

    def way(self, w):
        highway = w.tags.get('highway')
        if highway in URBAN_KINDS:
            self.add_residential(w)
            return
        if highway and (highway.endswith('_link') or w.tags.get('junction') in ('roundabout', 'circular')):
            try:
                cells_along([(node.lat, node.lon) for node in w.nodes], self.junction_cells.add)
            except osmium.InvalidLocationError:
                pass
        bit = CLASS_BIT.get(highway)
        if bit is None:
            return
        value = 1 << bit
        if w.tags.get('surface') in BAD_VALUES or w.tags.get('smoothness') in BAD_VALUES:
            value |= 1 << BAD_BIT
        try:
            points = [(node.lat, node.lon) for node in w.nodes]
        except osmium.InvalidLocationError:
            return
        self.ways += 1
        cells = self.cells
        cells_along(points, lambda key: cells.__setitem__(key, cells.get(key, 0) | value))

    def add_residential(self, w) -> None:
        try:
            points = [(node.lat, node.lon) for node in w.nodes]
        except osmium.InvalidLocationError:
            return
        # Kısa sokak bölümleri; uzunluk, orta noktasının hücresine yazılır.
        for a, b in zip(points, points[1:]):
            key = (math.floor((a[0] + b[0]) / 2 / DLAT), math.floor((a[1] + b[1]) / 2 / DLON))
            self.residential[key] = self.residential.get(key, 0.0) + haversine(a, b)

    def urban_cells(self) -> set[tuple[int, int]]:
        urban = set(self.junction_cells)
        urban.update(key for key, meters in self.residential.items() if meters >= URBAN_RESIDENTIAL_METERS)
        return urban


def mountain_passes(pbf: Path) -> list[list[float]]:
    # Süzme C++ tarafında: Python'a yalnız `mountain_pass` etiketli noktalar gelir.
    processor = osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(
        osmium.filter.KeyFilter('mountain_pass')
    )
    found = {
        (round(node.location.lat, 5), round(node.location.lon, 5))
        for node in processor
        if node.tags.get('mountain_pass') == 'yes' and node.location.valid()
    }
    return [list(point) for point in sorted(found)]


def varint(n: int, out: bytearray) -> None:
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return


def encode_keys(keys: set[tuple[int, int]]) -> bytes:
    rows: dict[int, list[int]] = {}
    for lat_index, lon_index in keys:
        rows.setdefault(lat_index, []).append(lon_index)
    out = bytearray()
    previous_lat = 0
    for lat_index in sorted(rows):
        row = sorted(rows[lat_index])
        varint(lat_index - previous_lat, out)
        varint(len(row), out)
        previous_lon = 0
        for lon_index in row:
            varint(lon_index - previous_lon, out)
            previous_lon = lon_index
        previous_lat = lat_index
    return bytes(out)


def encode(cells: dict[tuple[int, int], int]) -> bytes:
    rows: dict[int, list[tuple[int, int]]] = {}
    for (lat_index, lon_index), value in cells.items():
        rows.setdefault(lat_index, []).append((lon_index, value))
    out = bytearray()
    previous_lat = 0
    for lat_index in sorted(rows):
        row = sorted(rows[lat_index])
        varint(lat_index - previous_lat, out)
        varint(len(row), out)
        previous_lon = 0
        for lon_index, value in row:
            varint(((lon_index - previous_lon) << 6) | value, out)
            previous_lon = lon_index
        previous_lat = lat_index
    return bytes(out)


def main() -> None:
    started = time.time()
    pbf = download_pbf()
    base = osm_base(pbf)

    if OUTPUT.exists() and base:
        previous = json.loads(OUTPUT.read_text(encoding='utf-8')).get('osmBase')
        if previous and base < previous:
            sys.exit(f'Kaynak veri eski ({base}); mevcut dosya {previous}. Dosya değiştirilmedi.')

    collector = Collector()
    collector.apply_file(str(pbf), locations=True, idx='sparse_mem_array')
    urban_cells = collector.urban_cells()
    passes = mountain_passes(pbf)
    if len(collector.cells) < MIN_CELLS or len(passes) < MIN_PASSES:
        sys.exit(f'Beklenenden az veri: {len(collector.cells)} hücre, {len(passes)} geçit.')

    grid = encode(collector.cells)
    dataset = {
        'format': 1,
        'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'osmBase': base,
        'attribution': '© OpenStreetMap contributors, ODbL',
        'cell': {'dLat': DLAT, 'dLon': DLON},
        'counts': {
            'ways': collector.ways,
            'cells': len(collector.cells),
            'badCells': sum(1 for v in collector.cells.values() if v & (1 << BAD_BIT)),
            'urbanCells': len(urban_cells),
            'passes': len(passes),
        },
        'passes': passes,
        'grid': base64.b64encode(grid).decode('ascii'),
        'urban': base64.b64encode(encode_keys(urban_cells)).decode('ascii'),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'Yazıldı: {OUTPUT} — {len(grid):,} bayt ızgara, {dataset["counts"]}, '
          f'OSM {base}, {time.time() - started:.0f} sn')


if __name__ == '__main__':
    main()
