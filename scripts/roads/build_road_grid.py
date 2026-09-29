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
"""
import base64
import hashlib
import json
import math
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


class Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.cells: dict[tuple[int, int], int] = {}
        self.ways = 0

    def way(self, w):
        bit = CLASS_BIT.get(w.tags.get('highway'))
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
        for (a_lat, a_lon), (b_lat, b_lon) in zip(points, points[1:]):
            # Hücre boyunun yarısından sık örneklenir, çapraz geçişte hücre atlanmaz.
            steps = int(max(abs(b_lat - a_lat) / DLAT, abs(b_lon - a_lon) / DLON) * 2) + 1
            for i in range(steps + 1):
                t = i / steps
                key = (math.floor((a_lat + (b_lat - a_lat) * t) / DLAT),
                       math.floor((a_lon + (b_lon - a_lon) * t) / DLON))
                cells[key] = cells.get(key, 0) | value


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
            'passes': len(passes),
        },
        'passes': passes,
        'grid': base64.b64encode(grid).decode('ascii'),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'Yazıldı: {OUTPUT} — {len(grid):,} bayt ızgara, {dataset["counts"]}, '
          f'OSM {base}, {time.time() - started:.0f} sn')


if __name__ == '__main__':
    main()
