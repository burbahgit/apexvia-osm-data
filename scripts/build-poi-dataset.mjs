// Türkiye yer verisi (benzinlik / konaklama / kafe) — OpenStreetMap'ten
// `assets/poi/tr-poi.json` üretir. Uygulama bu dosyayı paketin içinde taşır ve
// harita pinlerini Google Places yerine buradan gösterir. Her sürümden önce
// çalıştırılır:
//
//   npm run poi:update
//
// Veri © OpenStreetMap katkıda bulunanlar, ODbL lisansıyla — uygulamada atıf
// gösterilmesi zorunlu.

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

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
          headers: { 'User-Agent': 'apexvia-osm-data (burak.bahali@gmail.com)' },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!Array.isArray(data.elements)) throw new Error('elements yok');
        if (data.elements.length < category.minCount) {
          throw new Error(`yalnız ${data.elements.length} eleman (en az ${category.minCount})`);
        }
        console.log(`${category.name}: ${data.elements.length} eleman (${endpoint})`);
        return { elements: data.elements, osmBase: data.osm3s?.timestamp_osm_base ?? null };
      } catch (error) {
        lastError = error;
        console.warn(`${category.name}: ${endpoint} deneme ${attempt} başarısız — ${error.message}`);
        await new Promise((r) => setTimeout(r, 5000 * attempt));
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

const dataset = {
  generatedAt: new Date().toISOString(),
  osmBase: osmBases.sort()[0] ?? null,
  attribution: '© OpenStreetMap contributors, ODbL',
  counts,
  places: rows,
};

mkdirSync(dirname(OUTPUT), { recursive: true });
writeFileSync(OUTPUT, JSON.stringify(dataset));
console.log(`Yazıldı: ${OUTPUT} — ${rows.length} yer`, counts);
