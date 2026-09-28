// Türkiye yer verisi (benzinlik / konaklama / kafe) — OpenStreetMap'ten
// `assets/poi/tr-poi.json` üretir. Uygulama bu dosyayı paketin içinde taşır ve
// harita pinlerini Google Places yerine buradan gösterir. Her sürümden önce
// çalıştırılır:
//
//   npm run poi:update
//
// Veri © OpenStreetMap katkıda bulunanlar, ODbL lisansıyla — uygulamada atıf
// gösterilmesi zorunlu.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const { dedupePoiRows } = createRequire(import.meta.url)('./poi-dedupe.cjs');

const OUTPUT = resolve(dirname(fileURLToPath(import.meta.url)), '../assets/poi/tr-poi.json');

// Halka açık Overpass sunucusu sık sık 504 veriyor; sunucular sırayla
// denenir, her biri birkaç kez.
const ENDPOINTS = [
  'https://overpass-api.de/api/interpreter',
  'https://overpass.private.coffee/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
];
const ATTEMPTS_PER_ENDPOINT = 3;

// Uygulamadaki `PlaceCategory` ile aynı adlar; dosyada tek harfle saklanır.
// `minCount`: bir sunucunun döndürdüğü sonuç bundan azsa başarısız deneme
// sayılır. Yedek sunuculardan biri (kumi.systems, 24 Eylül 2026)
// Türkiye alanını tanımadığı için 200 ile BOŞ liste döndürüyordu; betik bunu
// kabul edip sıfır benzinlikli bir dosya yazabilirdi. Eşikler 24 Eylül 2026
// sayılarının (12.078 / 10.862 / 13.378) yaklaşık üçte ikisi.
const CATEGORIES = [
  { code: 'g', name: 'gasStation', selector: 'nwr[amenity=fuel]', minCount: 8000 },
  {
    code: 'l',
    name: 'lodging',
    selector: 'nwr[tourism~"^(hotel|motel|guest_house|hostel|apartment)$"]',
    minCount: 7000,
  },
  { code: 'r', name: 'restStop', selector: 'nwr[amenity=cafe]', minCount: 9000 },
];

const OSM_TYPE_PREFIX = { node: 'n', way: 'w', relation: 'r' };

// Yedek sunucular ana sunucudan aylarca geride olabilir: private.coffee 28
// Eylül 2026'da 1 Haziran verisi döndürdü ve betik bunu kabul edip dosyayı
// eskitiyordu. Mevcut dosyadan eski (ya da tarihsiz) yanıt başarısız deneme
// sayılır; yenileme veriyi hiçbir zaman geriye götürmez.
function readPreviousOsmBase() {
  if (!existsSync(OUTPUT)) return null;
  try {
    return JSON.parse(readFileSync(OUTPUT, 'utf8')).osmBase ?? null;
  } catch {
    return null;
  }
}
const PREVIOUS_OSM_BASE = readPreviousOsmBase();

function buildQuery(selector) {
  return `[out:json][timeout:300];
area["ISO3166-1"="TR"][admin_level=2]->.tr;
${selector}(area.tr);
out center tags;`;
}

async function fetchCategory(category) {
  const body = new URLSearchParams({ data: buildQuery(category.selector) });
  let lastError;
  for (const endpoint of ENDPOINTS) {
    for (let attempt = 1; attempt <= ATTEMPTS_PER_ENDPOINT; attempt++) {
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          body,
          headers: { 'User-Agent': 'apexvia-osm-data (bhldev.app@gmail.com)' },
        });
        if (!response.ok) {
          const error = new Error(`HTTP ${response.status}`);
          error.rateLimited = response.status === 429;
          throw error;
        }
        const data = await response.json();
        if (!Array.isArray(data.elements)) throw new Error('elements yok');
        if (data.elements.length < category.minCount) {
          throw new Error(`yalnız ${data.elements.length} eleman (en az ${category.minCount})`);
        }
        const osmBase = data.osm3s?.timestamp_osm_base ?? null;
        if (PREVIOUS_OSM_BASE && (!osmBase || osmBase < PREVIOUS_OSM_BASE)) {
          throw new Error(`veri eski (${osmBase ?? 'tarih yok'}; mevcut dosya ${PREVIOUS_OSM_BASE})`);
        }
        console.log(`${category.name}: ${data.elements.length} eleman, OSM ${osmBase} (${endpoint})`);
        return { elements: data.elements, osmBase };
      } catch (error) {
        lastError = error;
        console.warn(`${category.name}: ${endpoint} deneme ${attempt} başarısız — ${error.message}`);
        // 429: önceki ağır sorgu sunucudaki sıra hakkını bir süre tutuyor
        // (28 Eylül 2026: üçüncü kategori 5–15 sn aralıkla üç kez 429 aldı).
        const waitMs = error.rateLimited ? 60000 * attempt : 5000 * attempt;
        await new Promise((r) => setTimeout(r, waitMs));
      }
    }
  }
  throw new Error(`${category.name} çekilemedi: ${lastError?.message}`);
}

const round5 = (value) => Math.round(value * 1e5) / 1e5;

function isClosed(tags) {
  return Object.keys(tags).some(
    (key) => key.startsWith('disused:') || key.startsWith('abandoned:') || key.startsWith('was:')
  );
}

// Motosiklet yalnız LPG/CNG satan bir istasyonda yakıt
// alamaz. OSM'de 12.080 istasyondan yalnız ~1.140'ında yakıt türü etiketi var
// ve bunların çoğu "motorin + LPG" diye etiketlenmiş normal benzinlikler
// (benzin etiketi unutulmuş). Bu yüzden "benzin etiketi yok" kural olamaz;
// yalnız iki durum elenir:
//  1. Etiketler açıkça yalnız LPG/CNG diyor (`yes` olan tek yakıt türleri bunlar).
//  2. Hiç yakıt etiketi yok, ama adı/markası bir otogaz markası ve benzin
//     işareti (petrol, akaryakıt, GO gibi akaryakıt markaları) taşımıyor.
//     "Go İpragaz" gibi istasyonlar GO'nun benzinlikleri; elenmezler.
const GAS_ONLY_FUEL_KEYS = new Set(['fuel:lpg', 'fuel:cng']);
const AUTOGAS_NAME = /(aygaz|milangaz|[iİı]pragaz|mogaz|likitgaz|oto ?gaz|autogas|\blpg\b)/i;
const PETROL_NAME =
  /(petrol|akaryak|benzin|\bgo\b|opet|shell|\bbp\b|total|aytemiz|lukoil|\bpo\b|alpet|sunpet|moil|kadoil|euroil)/i;

function sellsNoPetrol(tags) {
  const fuelKeys = Object.keys(tags).filter((key) => key.startsWith('fuel:'));
  if (fuelKeys.length > 0) {
    const fuelYes = fuelKeys.filter((key) => tags[key] === 'yes');
    return fuelYes.length > 0 && fuelYes.every((key) => GAS_ONLY_FUEL_KEYS.has(key));
  }
  const label = [tags.name, tags.brand, tags.operator].filter(Boolean).join(' ');
  return AUTOGAS_NAME.test(label) && !PETROL_NAME.test(label);
}

/** Dosya satırı: [id, kategori, enlem, boylam, ad|null, marka|null, telefon|null]. */
function toRow(element, category) {
  const tags = element.tags ?? {};
  if (isClosed(tags)) return null;
  if (category.code === 'g' && sellsNoPetrol(tags)) return null;
  const lat = element.lat ?? element.center?.lat;
  const lon = element.lon ?? element.center?.lon;
  if (typeof lat !== 'number' || typeof lon !== 'number') return null;
  const name = tags['name:tr'] ?? tags.name ?? null;
  const brand = category.code === 'g' ? (tags.brand ?? tags.operator ?? null) : null;
  const phone = tags.phone ?? tags['contact:phone'] ?? null;
  return [
    `${OSM_TYPE_PREFIX[element.type]}${element.id}`,
    category.code,
    round5(lat),
    round5(lon),
    name,
    brand,
    phone,
  ];
}

const rows = [];
// ODbL: verinin hangi OSM anından alındığı. Kategoriler ayrı
// sorgularla çekildiği için en eskisi yazılır.
const osmBases = [];
const counts = {};
for (const category of CATEGORIES) {
  const { elements, osmBase } = await fetchCategory(category);
  if (osmBase) osmBases.push(osmBase);
  const categoryRows = elements.map((element) => toRow(element, category)).filter(Boolean);
  counts[category.name] = categoryRows.length;
  rows.push(...categoryRows);
}

// T-119: aynı yerin nokta + bina kayıtları (haritada çift pin) teke indirilir.
const { rows: places, merged } = dedupePoiRows(rows);
const codeToName = Object.fromEntries(CATEGORIES.map((category) => [category.code, category.name]));
for (const name of Object.keys(counts)) counts[name] = 0;
for (const row of places) counts[codeToName[row[1]]] += 1;
console.log(`Çift kayıt birleştirildi: ${merged.length}`);

const dataset = {
  generatedAt: new Date().toISOString(),
  osmBase: osmBases.sort()[0] ?? null,
  attribution: '© OpenStreetMap contributors, ODbL',
  counts,
  places,
};

mkdirSync(dirname(OUTPUT), { recursive: true });
writeFileSync(OUTPUT, JSON.stringify(dataset));
console.log(`Yazıldı: ${OUTPUT} — ${places.length} yer`, counts);
