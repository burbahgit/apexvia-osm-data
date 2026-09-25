# Apexvia OSM data — Turkey points of interest

Fuel stations, lodging and cafés in Turkey, extracted from
[OpenStreetMap](https://www.openstreetmap.org/). This is the exact dataset bundled in the Apexvia
Android app, published here as required by the Open Database License.

[Türkçe açıklama aşağıda.](#türkçe)

## Contents

| File | What it is |
|---|---|
| `assets/poi/tr-poi.json` | The dataset: 36,228 places (11,985 fuel stations, 10,863 places to stay, 13,380 cafés) |
| `scripts/build-poi-dataset.mjs` | The script that produces the dataset |
| `LICENSE-DATA.md` | Data licence (ODbL 1.0) |
| `LICENSE` | Script licence (MIT) |

- Generated: 2026-09-24T20:22:48.024Z
- OpenStreetMap data as of: 2026-09-24T20:19:21Z

## Source

Three queries to the public [Overpass API](https://overpass-api.de/), each limited to the area
`["ISO3166-1"="TR"][admin_level=2]`, returning `out center tags`:

| Category | Overpass selector |
|---|---|
| Fuel stations | `nwr[amenity=fuel]` |
| Lodging | `nwr[tourism~"^(hotel\|motel\|guest_house\|hostel\|apartment)$"]` |
| Cafés | `nwr[amenity=cafe]` |

## Processing

- Places with a `disused:`, `abandoned:` or `was:` tag prefix are removed.
- Fuel stations that sell only LPG or CNG are removed, because a motorcycle cannot refuel there:
  either their `fuel:*=yes` tags list only `fuel:lpg`/`fuel:cng`, or they have no `fuel:*` tags and
  their name, brand or operator is an autogas brand (Aygaz, Milangaz, İpragaz, Mogaz, Likitgaz,
  "otogaz", "autogas", "LPG") with no sign of petrol ("petrol", "akaryakıt", or a petrol brand such
  as GO, Opet, Shell, BP, Total, Aytemiz). When `fuel:*` tags exist, the name rule is not used.
- Ways and relations are reduced to their centre point.
- Coordinates are rounded to 5 decimal places (about 1 m).
- Only these tags are kept: name (`name:tr`, else `name`), brand (`brand`, else `operator`; fuel
  stations only) and phone (`phone`, else `contact:phone`).

## Format

```json
{
  "generatedAt": "ISO 8601 time of the run",
  "osmBase": "ISO 8601 time of the OpenStreetMap data",
  "attribution": "© OpenStreetMap contributors, ODbL",
  "counts": { "gasStation": 0, "lodging": 0, "restStop": 0 },
  "places": [["n123", "g", 39.92077, 32.85411, "Name", "Brand", "+90 …"]]
}
```

Each place is `[id, category, latitude, longitude, name, brand, phone]`. `id` is the OpenStreetMap
element type (`n` node, `w` way, `r` relation) followed by its id, so `n123` is
<https://www.openstreetmap.org/node/123>. `category` is `g` fuel station, `l` lodging or `r` café.
Missing values are `null`. Older files have no `osmBase`.

## Regenerating

Requires Node.js 18 or newer; there are no dependencies.

```sh
node scripts/build-poi-dataset.mjs
```

The script overwrites `assets/poi/tr-poi.json`. It tries several public Overpass servers in turn
and refuses to write a file if a category comes back with far fewer places than expected (a
server that does not know the Turkey area answers with an empty list).

## Licence

**Data:** © OpenStreetMap contributors. The dataset is made available under the
[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/) (ODbL); its individual
contents are under the [Database Contents License 1.0](https://opendatacommons.org/licenses/dbcl/1-0/).
See `LICENSE-DATA.md` and <https://www.openstreetmap.org/copyright>. If you use it, credit
"© OpenStreetMap contributors", and share any database you derive from it under the ODbL.

**Script:** MIT, see `LICENSE`.

This project is not affiliated with or endorsed by the OpenStreetMap Foundation.

---

## Türkçe

Türkiye'deki benzin istasyonları, konaklama yerleri ve kafeler;
[OpenStreetMap](https://www.openstreetmap.org/)'ten çıkarılmıştır. Apexvia Android uygulamasının
içinde gelen verinin birebir aynısıdır ve Açık Veritabanı Lisansı (ODbL) gereği burada yayınlanır.

**İçerik**

- `assets/poi/tr-poi.json`: veri dosyası. 36.228 yer (11.985 benzin istasyonu,
  10.863 konaklama, 13.380 kafe).
- `scripts/build-poi-dataset.mjs`: dosyayı üreten betik.
- `LICENSE-DATA.md`: veri lisansı (ODbL 1.0). `LICENSE`: betik lisansı (MIT).
- Üretim zamanı: 2026-09-24T20:22:48.024Z. OpenStreetMap verisinin tarihi: 2026-09-24T20:19:21Z.

**Kaynak ve işleme**

Veri, herkese açık Overpass API'den Türkiye alanıyla sınırlı üç sorguyla alınır (sorgular yukarıdaki
tabloda). Kapanmış yerler (`disused:`, `abandoned:`, `was:` önekli etiketler) ve yalnız LPG/CNG
satan istasyonlar çıkarılır; motosiklet bu istasyonlarda yakıt alamaz. Yalnız LPG/CNG satan
istasyon şöyle belirlenir: yakıt etiketlerinde `yes` olanlar yalnız `fuel:lpg`/`fuel:cng` ise ya
da hiç yakıt etiketi yokken adı, markası veya işletmecisi bir otogaz markasıysa ve benzin işareti
("petrol", "akaryakıt", GO, Opet, Shell gibi bir akaryakıt markası) taşımıyorsa. Koordinatlar
virgülden sonra 5 basamağa (yaklaşık 1 m) yuvarlanır. Yalnız ad, marka (yalnız benzin istasyonları)
ve telefon tutulur.

**Yeniden üretme**

Node.js 18 veya üstü yeterlidir, bağımlılık yoktur: `node scripts/build-poi-dataset.mjs`. Betik
`assets/poi/tr-poi.json` dosyasının üzerine yazar. Bir kategori beklenenden çok az yerle dönerse
dosyayı yazmaz.

**Lisans**

Veri: © OpenStreetMap katkıda bulunanlar. Veritabanı
[Açık Veritabanı Lisansı 1.0](https://opendatacommons.org/licenses/odbl/1-0/) (ODbL), tek tek
içerikler [Veritabanı İçerik Lisansı 1.0](https://opendatacommons.org/licenses/dbcl/1-0/) (DbCL)
altındadır. Kullanırsanız "© OpenStreetMap katkıda bulunanlar" atfını verin; bu veriden
türettiğiniz veritabanlarını da ODbL ile paylaşın. Ayrıntı: <https://www.openstreetmap.org/copyright>.

Betik: MIT (`LICENSE`).

Bu proje OpenStreetMap Vakfı ile bağlantılı değildir ve vakıf tarafından onaylanmamıştır.
