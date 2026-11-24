"""T-357 (tasarım §252): Türkiye sınır çizgisi (kara + karasuları).

`assets/roads/tr-border.json` üretir. Uygulama rota çizgisinin ülke dışına çıkıp çıkmadığına buradan bakar (T-319'un "veri
yakınlığı" denetimi sınır kapısından geçen sığ turu yakalamıyordu: Edirne → Pazarkule → Vyssa). Kaynak Geofabrik Türkiye özeti
(`.cache/osm/turkey-latest.osm.pbf`), OSM ilişkisi 174737 (`boundary=administrative`, `admin_level=2`); karasularını da kapsar,
böylece Gökçeada feribotu ve Van Gölü içeride kalır. `npm run border:update` çalıştırır.

Veri © OpenStreetMap katkıda bulunanlar, ODbL.

Bellek için üç geçiş: (1) ilişkinin üye yolları ve rolleri, (2) bu yolların düğüm kimlikleri, (3) yalnız bu düğümlerin konumları.
Yollar uç uca eklenerek halkalara dönüştürülür; her halka Douglas–Peucker ile ~`SIMPLIFY_METERS` hassasiyete indirilir.
Çıktı: {"format": 1, "source", "osmBase", "simplifyMeters", "outer": [[[enlem, boylam], …], …], "inner": [...]}.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

import osmium

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
PBF = os.path.join(ROOT, '.cache', 'osm', 'turkey-latest.osm.pbf')
OUT = os.path.join(ROOT, 'assets', 'roads', 'tr-border.json')
RELATION_ID = 174737
SIMPLIFY_METERS = 100


class RelationPass(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.members = {}  # way id -> role

    def relation(self, r):
        if r.id != RELATION_ID:
            return
        for m in r.members:
            if m.type == 'w':
                self.members[m.ref] = m.role or 'outer'  # rolsüz üye OSM'de dış halka sayılır


class WayPass(osmium.SimpleHandler):
    def __init__(self, wanted):
        super().__init__()
        self.wanted = wanted
        self.ways = {}

    def way(self, w):
        if w.id in self.wanted:
            self.ways[w.id] = [n.ref for n in w.nodes]


class NodePass(osmium.SimpleHandler):
    def __init__(self, wanted):
        super().__init__()
        self.wanted = wanted
        self.nodes = {}

    def node(self, n):
        if n.id in self.wanted:
            self.nodes[n.id] = (n.location.lat, n.location.lon)


def assemble(ways):
    """Yolları uç uca ekleyip kapalı halkalar döndürür; kapanmayan parça varsa hata (eksik veri sessizce poligon olmasın)."""
    pending = [list(w) for w in ways]
    rings = []
    while pending:
        ring = pending.pop()
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, w in enumerate(pending):
                if w[0] == ring[-1]:
                    ring += w[1:]
                elif w[-1] == ring[-1]:
                    ring += list(reversed(w))[1:]
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring
                elif w[0] == ring[0]:
                    ring = list(reversed(w))[:-1] + ring
                else:
                    continue
                pending.pop(i)
                changed = True
                break
        if ring[0] != ring[-1]:
            raise SystemExit(f'Kapanmayan sınır parçası ({len(ring)} düğüm); özet sınırı tam içermiyor olabilir.')
        rings.append(ring)
    return rings


def meters(a, b):
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 111_320, (a[1] - b[1]) * 111_320 * math.cos(lat))


def point_segment_meters(p, a, b):
    lat = math.radians(p[0])
    kx, ky = 111_320 * math.cos(lat), 111_320
    ax, ay, bx, by, px, py = a[1] * kx, a[0] * ky, b[1] * kx, b[0] * ky, p[1] * kx, p[0] * ky
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0 if length2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify(points, tolerance):
    """Douglas–Peucker (yinelemeli, yığın taşmasın)."""
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        worst, index = 0, None
        for i in range(start + 1, end):
            d = point_segment_meters(points[i], points[start], points[end])
            if d > worst:
                worst, index = d, i
        if index is not None and worst > tolerance:
            keep[index] = True
            stack += [(start, index), (index, end)]
    return [p for p, k in zip(points, keep) if k]


def main():
    if not os.path.exists(PBF):
        sys.exit(f'Kaynak yok: {PBF} (önce `npm run roads:update` indirir).')
    rel = RelationPass()
    rel.apply_file(PBF)
    if not rel.members:
        sys.exit(f'İlişki {RELATION_ID} özette yok.')
    ways = WayPass(set(rel.members))
    ways.apply_file(PBF)
    missing_ways = set(rel.members) - set(ways.ways)
    if missing_ways:
        sys.exit(f'{len(missing_ways)} sınır yolu özette yok; sınır eksik kalırdı.')
    wanted_nodes = {n for refs in ways.ways.values() for n in refs}
    nodes = NodePass(wanted_nodes)
    nodes.apply_file(PBF)
    missing_nodes = wanted_nodes - set(nodes.nodes)
    if missing_nodes:
        sys.exit(f'{len(missing_nodes)} sınır düğümü özette yok; sınır eksik kalırdı.')

    out = {'outer': [], 'inner': []}
    for role in ('outer', 'inner'):
        # CR-1: yalnız sınır rolleri; başka roldeki yol (yanlış eklenmiş) halkaya girip "kapanmayan parça" hatası vermesin.
        role_ways = [ways.ways[w] for w, r in rel.members.items() if r == role]
        others = sorted({r for r in rel.members.values() if r not in ('outer', 'inner')})
        if role == 'outer' and others:
            print(f'Uyarı: sınır dışı roller atlandı: {others}')
        for ring in assemble(role_ways):
            coords = [nodes.nodes[n] for n in ring]
            simple = simplify(coords, SIMPLIFY_METERS)
            if len(simple) < 4:
                continue  # sadeleştirmede iki noktaya inen 100 m'den küçük adacık halkası
            out[role].append([[round(lat, 5), round(lon, 5)] for lat, lon in simple])
    raw_points = sum(len(r) for r in ways.ways.values())
    kept = sum(len(r) for role in out.values() for r in role)
    base = datetime.fromtimestamp(os.path.getmtime(PBF), tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    data = {
        'format': 1,
        'source': f'OpenStreetMap relation {RELATION_ID} (© OpenStreetMap contributors, ODbL)',
        'osmBase': base,
        'simplifyMeters': SIMPLIFY_METERS,
        **out,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    print(f'{OUT}: dış halka {len(out["outer"])}, iç halka {len(out["inner"])}, nokta {raw_points} -> {kept}, '
          f'{os.path.getsize(OUT) // 1024} KB')


if __name__ == '__main__':
    main()
