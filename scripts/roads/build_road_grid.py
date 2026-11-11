"""T-130 (tasarım §81–§82): Türkiye yol ızgarası ve dağ geçitleri.

`assets/roads/tr-roads.json` üretir. Uygulama rota skorlamasında yol sınıfını,
kötü yüzeyi ve dağ geçitlerini çalışma anında Overpass'a sormak yerine bu
dosyadan okur. Kaynak Geofabrik Türkiye özeti; `npm run roads:update` çalıştırır.

Veri © OpenStreetMap katkıda bulunanlar, ODbL.

Izgara: hücre 0,00225° (enlem) × 0,003° (boylam) ≈ 250 m. Hücre değeri 6 bit:
bit0 motorway, bit1 trunk, bit2 primary, bit3 secondary,
bit4 tertiary/unclassified (hücreden geçen sınıflar), bit5 kötü yüzey.

Kötü yüzey (T-167, tasarım §179): bit yalnız hücredeki EN YÜKSEK sınıftaki yol kötü etiketliyse konur. Önce
hücreden geçen herhangi bir sınıflı yolun etiketinden konuyordu; D300 (trunk, asfalt) yanından paralel geçen toprak
`unclassified` yollar yüzünden 5,5 km "bozuk" görünüyordu.
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

`towns` (T-161): şehir ve kasaba noktaları (`place=city/town`), [enlem, boylam, ad]. Bozuk yol
bölümü "Akseki yakınında" diye adlandırılır; köyler (~35.000) dosyayı ~1,5 MB büyüteceği ve çoğu
sürücüye tanıdık gelmeyeceği için alınmaz (Burak, 30 Ekim 2026).

`coast` (T-316 U-3, Burak 11 Kasım 2026): sahil hücreleri, `urban` ile aynı değersiz satır düzeninde. Kıyı çizgisine
(`natural=coastline`, yalnız deniz; göller K-5 gereği v1'de yok) `COAST_METERS` içinde olan VE sınıflı yol geçen hücreler.
Sahil Yolları tarzının kıyı payı bundan okunur (`osm_sinyaller.py` prototipiyle aynı kural, §2.3).
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
MIN_TOWNS = 500
MIN_COAST_CELLS = 20_000
COAST_METERS = 1000
TOWN_PLACES = {'city', 'town'}


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
        # T-167: hücrede kötü etiketli yolların sınıf bitleri; bit 5 `final_cells`te en yüksek sınıfa göre konur.
        self.bad_classes: dict[tuple[int, int], int] = {}
        self.ways = 0
        self.junction_cells: set[tuple[int, int]] = set()
        self.residential: dict[tuple[int, int], float] = {}
        # T-316 U-3: kıyı çizgisi noktaları, ~100 m'de bir (hücre 250 m).
        self.coast_points: list[tuple[float, float]] = []

    def way(self, w):
        if w.tags.get('natural') == 'coastline':
            self.add_coast(w)
            return
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
        bad = w.tags.get('surface') in BAD_VALUES or w.tags.get('smoothness') in BAD_VALUES
        try:
            points = [(node.lat, node.lon) for node in w.nodes]
        except osmium.InvalidLocationError:
            return
        self.ways += 1
        cells = self.cells
        cells_along(points, lambda key: cells.__setitem__(key, cells.get(key, 0) | value))
        if bad:
            bad_classes = self.bad_classes
            cells_along(points, lambda key: bad_classes.__setitem__(key, bad_classes.get(key, 0) | value))

    def final_cells(self) -> dict[tuple[int, int], int]:
        """Hücre değerleri; kötü yüzey biti yalnız en yüksek sınıf (en küçük bit) kötü etiketliyse (T-167)."""
        out = dict(self.cells)
        for key, bad in self.bad_classes.items():
            classes = out[key]
            if bad & (classes & -classes):
                out[key] = classes | (1 << BAD_BIT)
        return out

    def add_residential(self, w) -> None:
        try:
            points = [(node.lat, node.lon) for node in w.nodes]
        except osmium.InvalidLocationError:
            return
        # Kısa sokak bölümleri; uzunluk, orta noktasının hücresine yazılır.
        for a, b in zip(points, points[1:]):
            key = (math.floor((a[0] + b[0]) / 2 / DLAT), math.floor((a[1] + b[1]) / 2 / DLON))
            self.residential[key] = self.residential.get(key, 0.0) + haversine(a, b)

    def add_coast(self, w) -> None:
        try:
            points = [(node.lat, node.lon) for node in w.nodes]
        except osmium.InvalidLocationError:
            return
        for a, b in zip(points, points[1:]):
            steps = max(1, int(haversine(a, b) / 100))
            for i in range(steps):
                t = i / steps
                self.coast_points.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        if points:
            self.coast_points.append(points[-1])

    def coast_cells(self, radius: float = COAST_METERS) -> set[tuple[int, int]]:
        """Kıyı çizgisine `radius` içinde olan ve sınıflı yol geçen hücreler (merkezden ölçülür)."""
        cells: set[tuple[int, int]] = set()
        roads = self.cells
        ky = 110540.0
        for la, lo in self.coast_points:
            kx = 111320.0 * math.cos(math.radians(la))
            di = int(radius / (DLAT * ky)) + 1
            dj = int(radius / (DLON * kx)) + 1
            ci, cj = math.floor(la / DLAT), math.floor(lo / DLON)
            for i in range(-di, di + 1):
                for j in range(-dj, dj + 1):
                    key = (ci + i, cj + j)
                    if key in cells or key not in roads:
                        continue
                    if math.hypot(((cj + j + 0.5) * DLON - lo) * kx, ((ci + i + 0.5) * DLAT - la) * ky) <= radius:
                        cells.add(key)
        return cells

    def urban_cells(self) -> set[tuple[int, int]]:
        urban = set(self.junction_cells)
        urban.update(key for key, meters in self.residential.items() if meters >= URBAN_RESIDENTIAL_METERS)
        return urban


def _ele(value: str | None) -> float | None:
    # OSM `ele` serbest metin ("2994", "2994 m", "2.994", "2,994"); yalnız sayıyı al, anlamsızsa None.
    # CR-2 (1.4.5): "2.994" ve "2,994" binlik ayırıcıdır (3 m değil); yalnız boşluk olan değer çökertmesin.
    parts = (value or '').split()
    if not parts:
        return None
    token = parts[0]
    if len(token) in (5, 6) and token[-4] in '.,' and token.replace('.', '').replace(',', '').isdigit():
        token = token.replace('.', '').replace(',', '')
    digits = ''.join(ch for ch in token.replace(',', '.') if ch.isdigit() or ch == '.')
    try:
        number = float(digits)
    except ValueError:
        return None
    return round(number) if 0 < number < 9000 else None


def mountain_passes(pbf: Path) -> list[list]:
    # T-312: [enlem, boylam, yükseklik|None, ad|None]. Süzme C++ tarafında: yalnız `mountain_pass` etiketli noktalar.
    processor = osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(
        osmium.filter.KeyFilter('mountain_pass')
    )
    found: dict[tuple[float, float], list] = {}
    for node in processor:
        if node.tags.get('mountain_pass') != 'yes' or not node.location.valid():
            continue
        key = (round(node.location.lat, 5), round(node.location.lon, 5))
        found[key] = [key[0], key[1], _ele(node.tags.get('ele')), node.tags.get('name') or None]
    passes = [found[key] for key in sorted(found)]
    fill_missing_elevation(passes)
    return passes


SRTM = ROOT / '.cache/graphhopper/srtm'
SRTM_SIZE = 1201
SRTM_VOID = -32768


def srtm_at(lat: float, lon: float, tiles: dict) -> float | None:
    """SRTM3 (~90 m) yüksekliği, dört komşu örnekten çift doğrusal; karo ya da değer yoksa None."""
    import struct
    import zipfile
    base_lat, base_lon = math.floor(lat), math.floor(lon)
    key = (base_lat, base_lon)
    if key not in tiles:
        name = f"{'N' if base_lat >= 0 else 'S'}{abs(base_lat):02d}{'E' if base_lon >= 0 else 'W'}{abs(base_lon):03d}.hgt"
        path = SRTM / (name + '.zip')
        tiles[key] = None
        if path.exists():
            with zipfile.ZipFile(path) as archive:
                tiles[key] = archive.read(archive.namelist()[0])
    tile = tiles[key]
    if tile is None:
        return None
    x = (lon - base_lon) * (SRTM_SIZE - 1)
    y = (base_lat + 1 - lat) * (SRTM_SIZE - 1)
    x0, y0 = min(int(x), SRTM_SIZE - 2), min(int(y), SRTM_SIZE - 2)
    fx, fy = x - x0, y - y0
    values = []
    for row, col in ((y0, x0), (y0, x0 + 1), (y0 + 1, x0), (y0 + 1, x0 + 1)):
        (value,) = struct.unpack_from('>h', tile, 2 * (row * SRTM_SIZE + col))
        if value == SRTM_VOID:
            return None
        values.append(value)
    top = values[0] + (values[1] - values[0]) * fx
    bottom = values[2] + (values[3] - values[2]) * fx
    return top + (bottom - top) * fy


def fill_missing_elevation(passes: list[list]) -> None:
    """T-327 / D-35 (Burak, 11 Kasım 2026): OSM'de `ele`'si olmayan geçide SRTM3 yüksekliği; beşinci alan 'srtm' (kaynak).
    SRTM kamu malıdır (NASA/USGS). Karo yoksa (GraphHopper önbelleği eksik) geçit eskisi gibi yüksekliksiz kalır."""
    tiles: dict = {}
    filled = 0
    for entry in passes:
        if entry[2] is not None:
            continue
        value = srtm_at(entry[0], entry[1], tiles)
        if value is not None and 0 < value < 9000:
            entry[2] = round(value)
            entry.append('srtm')
            filled += 1
    missing = sum(1 for entry in passes if entry[2] is None)
    print(f'Geçit yüksekliği: {filled} geçide SRTM, {missing} geçit yüksekliksiz')


def towns(pbf: Path) -> list[list]:
    # Süzme C++ tarafında: Python'a yalnız `place` etiketli noktalar gelir.
    processor = osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(osmium.filter.KeyFilter('place'))
    found = {
        (round(node.location.lat, 4), round(node.location.lon, 4), node.tags.get('name').strip())
        for node in processor
        if node.tags.get('place') in TOWN_PLACES and node.tags.get('name') and node.location.valid()
    }
    return [list(town) for town in sorted(found)]


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
    coast_cells = collector.coast_cells()
    passes = mountain_passes(pbf)
    town_list = towns(pbf)
    if (len(collector.cells) < MIN_CELLS or len(passes) < MIN_PASSES or len(town_list) < MIN_TOWNS
            or len(coast_cells) < MIN_COAST_CELLS):
        sys.exit(f'Beklenenden az veri: {len(collector.cells)} hücre, {len(passes)} geçit, {len(town_list)} kasaba, '
                 f'{len(coast_cells)} sahil hücresi.')

    cells = collector.final_cells()
    grid = encode(cells)
    dataset = {
        'format': 1,
        'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'osmBase': base,
        'attribution': '© OpenStreetMap contributors, ODbL',
        'cell': {'dLat': DLAT, 'dLon': DLON},
        'counts': {
            'ways': collector.ways,
            'cells': len(collector.cells),
            'badCells': sum(1 for v in cells.values() if v & (1 << BAD_BIT)),
            'urbanCells': len(urban_cells),
            'coastCells': len(coast_cells),
            'passes': len(passes),
            'towns': len(town_list),
        },
        'passes': passes,
        'towns': town_list,
        'grid': base64.b64encode(grid).decode('ascii'),
        'urban': base64.b64encode(encode_keys(urban_cells)).decode('ascii'),
        'coast': base64.b64encode(encode_keys(coast_cells)).decode('ascii'),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'Yazıldı: {OUTPUT} — {len(grid):,} bayt ızgara, {dataset["counts"]}, '
          f'OSM {base}, {time.time() - started:.0f} sn')


if __name__ == '__main__':
    main()
