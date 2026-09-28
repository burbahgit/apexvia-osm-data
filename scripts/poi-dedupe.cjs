// T-119: OSM'de aynı yer çoğu zaman iki kez var: bir nokta (node) ve yerin
// bina ya da alan kaydı (way/relation). Haritada çift pin çıkıyordu (24 Eylül
// 2026 verisinde 282 çift). Aynı kategori + aynı ad (katlanmış) + en fazla
// MAX_DISTANCE_M uzaklıktaki kayıtlar teke indirilir. Adsız kayıtlar hiç
// birleştirilmez: aynı adı taşımayan iki yakın yer çoğu zaman gerçekten ayrı.
//
// `build-poi-dataset.mjs` (ESM) ve Jest (CommonJS) aynı dosyayı kullansın diye
// düz CommonJS.

const MAX_DISTANCE_M = 40;

// Satır: [id, kategori, enlem, boylam, ad|null, marka|null, telefon|null]
const ID = 0;
const CATEGORY = 1;
const LAT = 2;
const LON = 3;
const NAME = 4;
const BRAND = 5;
const PHONE = 6;

/** Karşılaştırma anahtarı: Türkçe küçük harf, ı→i, boşluklar tekleşir. */
function foldName(name) {
  return name
    .toLocaleLowerCase('tr')
    .replace(/̇/g, '')
    .replace(/ı/g, 'i')
    .replace(/\s+/g, ' ')
    .trim();
}

function distanceMeters(a, b) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(b[LAT] - a[LAT]);
  const dLon = toRad(b[LON] - a[LON]);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[LAT])) * Math.cos(toRad(b[LAT])) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

/** Tutulacak kayıt: nokta (n) bina/alandan (w/r) önce; eşitse küçük id (kararlı çıktı). */
function preferred(a, b) {
  const rank = (row) => (row[ID].startsWith('n') ? 0 : 1);
  if (rank(a) !== rank(b)) return rank(a) < rank(b) ? a : b;
  return a[ID] <= b[ID] ? a : b;
}

/**
 * Çiftleri teke indirir. Tutulan kayıtta eksik marka/telefon, silinen kayıttan
 * tamamlanır. Girdi sırası korunur (tutulan kayıt ilk görüldüğü yerde kalır).
 * @returns {{ rows: unknown[][], merged: Array<{ kept: string, dropped: string }> }}
 */
function dedupePoiRows(rows, maxDistanceM = MAX_DISTANCE_M) {
  const groups = new Map();
  rows.forEach((row, index) => {
    if (!row[NAME]) return;
    const key = `${row[CATEGORY]}|${foldName(row[NAME])}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(index);
  });

  const drop = new Set();
  const replacement = new Map(); // index -> birleştirilmiş satır
  const merged = [];

  for (const indices of groups.values()) {
    if (indices.length < 2) continue;
    // Aynı adlı kayıtlar arasında zincirleme değil, kümeye ilk girenle
    // karşılaştırma: 40 m'lik halkalar birbirine eklenip uzun bir hat oluşmasın.
    const clusters = [];
    for (const index of indices) {
      const row = rows[index];
      const cluster = clusters.find((c) => distanceMeters(rows[c[0]], row) <= maxDistanceM);
      if (cluster) cluster.push(index);
      else clusters.push([index]);
    }
    for (const cluster of clusters) {
      if (cluster.length < 2) continue;
      let keptIndex = cluster[0];
      for (const index of cluster.slice(1)) {
        if (preferred(rows[index], rows[keptIndex]) === rows[index]) keptIndex = index;
      }
      const kept = [...rows[keptIndex]];
      for (const index of cluster) {
        if (index === keptIndex) continue;
        const other = rows[index];
        if (kept[BRAND] == null && other[BRAND] != null) kept[BRAND] = other[BRAND];
        if (kept[PHONE] == null && other[PHONE] != null) kept[PHONE] = other[PHONE];
        drop.add(index);
        merged.push({ kept: kept[ID], dropped: other[ID] });
      }
      replacement.set(keptIndex, kept);
    }
  }

  const out = [];
  rows.forEach((row, index) => {
    if (drop.has(index)) return;
    out.push(replacement.get(index) ?? row);
  });
  return { rows: out, merged };
}

module.exports = { dedupePoiRows, foldName, MAX_DISTANCE_M };
