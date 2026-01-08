export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // CORS
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
    if (request.method === "OPTIONS") return new Response("", { headers: corsHeaders });

    // 常量（按你 Python）
    const ZONE_WIDTH_DEG = 36.0;
    const LANGFANG_LON_EAST_BOUNDARY = 116.7;

    // 你想要：1) 优先 q；2) 支持 lon
    const q = url.searchParams.get("q");
    if (q && q.trim()) {
      const geo = await nominatimGeocodeWithGeoJSON(q.trim());
      if (!geo) return json({ error: "place not found", query: q }, 404, corsHeaders);

      const centerLon = Number(geo.lon);
      const centerZone = lonToZoneInterval(centerLon, LANGFANG_LON_EAST_BOUNDARY, ZONE_WIDTH_DEG);

      // 1) 先尝试用 geojson 取样点做“最短覆盖区间”（更紧）
      const lons = extractLonsFromGeoJSON(geo.geojson);
      let bbox = null;
      let bboxNote = null;

      if (lons.length > 0) {
        const tight = circularMinCoverInterval(lons);
        if (tight) {
          const west = normalizeLonEdge(tight[0]);
          const east = normalizeLonEdge(tight[1]);
          bbox = { west, east, text: prettyLonRange(west, east), source: "geojson_min_cover" };
        }
      }

      // 2) 如果 geojson 取不到点，就退回 Nominatim boundingbox
      if (!bbox && Array.isArray(geo.boundingbox) && geo.boundingbox.length === 4) {
        const west = normalizeLonEdge(Number(geo.boundingbox[2]));
        const east = normalizeLonEdge(Number(geo.boundingbox[3]));
        bbox = { west, east, text: prettyLonRange(west, east), source: "nominatim_boundingbox" };

        // 某些跨日期变更线国家会返回 [-180, 180] 这种“全球 bbox”，给个提示
        if (west === -180 && east === 180) bboxNote = "bbox 为 [-180,180]，可能过宽；建议使用 geojson_min_cover（若可取到）";
      }

      // 覆盖分区（若有 bbox）
      let coveredZones = [];
      if (bbox) {
        coveredZones = zonesCoveredByLonRange(
          bbox.west,
          bbox.east,
          LANGFANG_LON_EAST_BOUNDARY,
          ZONE_WIDTH_DEG
        ).map((z) => ({
          zone: z.zone,
          west: z.west,
          east: z.east,
          intervalText: prettyRange(z.west, z.east),
        }));
      }

      // ----- 输出结构：贴近 Python CLI 的 print_place_result -----
      const zones_list = coveredZones.map((z) => z.zone);
      const zones_lines = coveredZones.map(
        (z) => `zone ${z.zone}: ${prettyRange(z.west, z.east)}`
      );

      // 生成一段可直接显示的“CLI 风格文本”
      const text_lines = [];
      text_lines.push(`place: ${q}`);
      text_lines.push(`matched: ${geo.display_name}`);
      text_lines.push(`center_lon: ${normalizeLonPoint(centerLon)}`);
      text_lines.push(
        `center_zone: ${centerZone.zone}  ${prettyRange(centerZone.west, centerZone.east)}`
      );
      if (bbox) {
        text_lines.push(`lon_range: ${prettyLonRange(bbox.west, bbox.east)} (${bbox.source})`);
      } else {
        text_lines.push(`lon_range: (none)`);
      }
      if (bboxNote) text_lines.push(`note: ${bboxNote}`);
      text_lines.push(`zones_list: [${zones_list.join(", ")}]`);
      if (zones_lines.length) {
        text_lines.push(`zones:`);
        for (const line of zones_lines) text_lines.push(`  ${line}`);
      } else {
        text_lines.push(`zones: (none)`);
      }
      text_lines.push(`sample_lon_count: ${lons.length}`);

      return json(
        {
          // ---- CLI 对齐字段（命名尽量贴近） ----
          place: q,
          matched: geo.display_name,
          center_lon: normalizeLonPoint(centerLon),
          center_zone: {
            zone: centerZone.zone,
            interval: { west: centerZone.west, east: centerZone.east },
            interval_text: prettyRange(centerZone.west, centerZone.east),
          },
          lon_range: bbox
            ? {
                west: bbox.west,
                east: bbox.east,
                range_text: prettyLonRange(bbox.west, bbox.east),
                source: bbox.source,
              }
            : null,
          note: bboxNote,

          // 覆盖区输出：zones_list + 每区一行
          zones_list,
          zones: coveredZones.map((z) => ({
            zone: z.zone,
            interval: { west: z.west, east: z.east },
            interval_text: prettyRange(z.west, z.east),
          })),
          zones_text: zones_lines,

          // 取样信息
          sample_lon_count: lons.length,

          // 额外：提供一段可直接展示的 CLI 风格文本
          text: text_lines.join("\n"),
        },
        200,
        corsHeaders
      );
    }

    // lon（第二优先）
    const lonParam = url.searchParams.get("lon");
    if (lonParam) {
      const parsed = parseLonFromText(String(lonParam));
      if (parsed == null) {
        return json({ error: "invalid lon, use '116.7' or '116.7,39.9'" }, 400, corsHeaders);
      }
      const z = lonToZoneInterval(parsed, LANGFANG_LON_EAST_BOUNDARY, ZONE_WIDTH_DEG);
      const text = [
        `lon_input: ${String(lonParam)}`,
        `lon: ${z.lon}`,
        `zone: ${z.zone}`,
        `interval: ${prettyRange(z.west, z.east)}`,
      ].join("\n");

      return json(
        {
          lon_input: String(lonParam),
          lon: z.lon,
          zone: z.zone,
          interval: { west: z.west, east: z.east },
          interval_text: prettyRange(z.west, z.east),
          text,
        },
        200,
        corsHeaders
      );
    }

    return json(
      { error: "missing q or lon", usage: ["?q=Russia", "?q=俄罗斯", "?lon=116.7"] },
      400,
      corsHeaders
    );
  },
};

function json(obj, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...extraHeaders },
  });
}

/* =========================
 * Nominatim (带 geojson)
 * ========================= */
async function nominatimGeocodeWithGeoJSON(query) {
  const u = new URL("https://nominatim.openstreetmap.org/search");
  u.searchParams.set("format", "jsonv2");
  u.searchParams.set("q", query);
  u.searchParams.set("limit", "1");
  u.searchParams.set("polygon_geojson", "1"); // 关键：拿 geojson
  u.searchParams.set("accept-language", "zh-CN,en");

  const r = await fetch(u.toString(), {
    headers: {
      // Nominatim 期望明确 UA；这里写个可识别字符串
      "User-Agent": "earthzones-worker/1.2",
      "Accept": "application/json",
    },
    // 简单缓存，减少被限流风险
    cf: { cacheTtl: 3600, cacheEverything: true },
  });
  if (!r.ok) return null;
  const arr = await r.json();
  return Array.isArray(arr) && arr.length ? arr[0] : null;
}

/* =========================
 * 你的 Python 逻辑移植
 * ========================= */

// normalize（边界保留 +180）
function normalizeLonEdge(lon) {
  let x = ((lon + 180.0) % 360.0 + 360.0) % 360.0 - 180.0;
  if (x === -180.0 && lon > 0) return 180.0;
  return x;
}

// 点经度 [-180,180)
function normalizeLonPoint(lon) {
  const x = normalizeLonEdge(lon);
  return x === 180.0 ? -180.0 : x;
}

function parseLonFromText(s) {
  const m = String(s).trim().match(/^([-+]?\d+(?:\.\d+)?)(?:[,\s]\s*([-+]?\d+(?:\.\d+)?))?$/);
  if (!m) return null;
  let lon = Number(m[1]);
  if (!Number.isFinite(lon)) return null;
  if (lon < -180 || lon > 180) lon = normalizeLonPoint(lon);
  return lon;
}

// lon -> 0..360
function lonTo360(lon) {
  return ((lon % 360) + 360) % 360;
}

// 0..360 -> 边界 [-180,180]（允许 +180）
function lon360ToEdge(lon360) {
  let x = lon360;
  if (x > 180.0) x = x - 360.0;
  if (x === -180.0) return -180.0;
  return x;
}

// geojson 提取经度点
function extractLonsFromGeoJSON(geom) {
  const lons = [];
  if (!geom || typeof geom !== "object") return lons;

  function walk(obj) {
    if (Array.isArray(obj)) {
      if (obj.length === 2 && isNum(obj[0]) && isNum(obj[1])) {
        lons.push(Number(obj[0])); // [lon, lat]
      } else {
        for (const it of obj) walk(it);
      }
    }
  }
  walk(geom.coordinates);
  return lons;
}

function isNum(x) {
  return typeof x === "number" && Number.isFinite(x);
}

// 圆周最短覆盖区间（找最大 gap 的补集）
function circularMinCoverInterval(lons) {
  if (!lons || !lons.length) return null;
  const pts = lons.map(lonTo360).sort((a, b) => a - b);

  if (pts.length === 1) {
    const w = lon360ToEdge(pts[0]);
    return [w, w];
  }

  let maxGap = -1.0;
  let maxI = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % pts.length];
    const gap = ((b - a) % 360 + 360) % 360;
    if (gap > maxGap) {
      maxGap = gap;
      maxI = i;
    }
  }

  const eastStart = pts[(maxI + 1) % pts.length];
  const westStart = pts[maxI];

  // 覆盖区间取补集：从 eastStart 到 westStart（沿正向）
  const west360 = eastStart;
  const east360 = westStart;

  const west = lon360ToEdge(west360);
  const east = lon360ToEdge(east360);
  return [west, east];
}

// 分区构造
function buildZoneIntervals(eastBoundaryZone9, zoneWidth) {
  const eastB = normalizeLonPoint(eastBoundaryZone9);
  const origin = normalizeLonPoint(eastB - zoneWidth); // 9区西边界

  const intervals = [];
  for (let stepsEast = 0; stepsEast < 10; stepsEast++) {
    const zone = (9 - stepsEast + 10) % 10;
    const west = normalizeLonPoint(origin + stepsEast * zoneWidth);
    const east = normalizeLonPoint(west + zoneWidth);
    intervals.push({ zone, west, east });
  }
  return intervals;
}

function lonToZoneInterval(lon, eastBoundaryZone9, zoneWidth) {
  const lonN = normalizeLonPoint(lon);
  const eastB = normalizeLonPoint(eastBoundaryZone9);
  const origin = normalizeLonPoint(eastB - zoneWidth);

  const diff = ((lonN - origin) % 360 + 360) % 360;
  const stepsEast = Math.floor(diff / zoneWidth);
  const zone = (9 - stepsEast + 10) % 10;

  const west = normalizeLonPoint(origin + stepsEast * zoneWidth);
  const east = normalizeLonPoint(west + zoneWidth);
  return { lon: lonN, zone, west, east };
}

function splitRange(west, east) {
  const w = normalizeLonEdge(west);
  const e = normalizeLonEdge(east);
  if (w <= e) return [[w, e]];
  return [[w, 180.0], [-180.0, e]];
}

function rangesIntersect(a, b) {
  const [a0, a1] = a;
  const [b0, b1] = b;
  return a0 < b1 && b0 < a1;
}

// bbox 覆盖到的所有分区
function zonesCoveredByLonRange(bboxWest, bboxEast, eastBoundaryZone9, zoneWidth) {
  const parts = splitRange(bboxWest, bboxEast);
  const intervals = buildZoneIntervals(eastBoundaryZone9, zoneWidth);

  const covered = [];
  for (const zi of intervals) {
    const ziParts = splitRange(zi.west, zi.east);
    let hit = false;
    for (const p of parts) {
      for (const zp of ziParts) {
        if (rangesIntersect(p, zp)) {
          hit = true;
          break;
        }
      }
      if (hit) break;
    }
    if (hit) covered.push(zi);
  }

  // 稳定输出：按区号从小到大
  covered.sort((a, b) => a.zone - b.zone);
  return covered;
}

function prettyRange(west, east, digits = 4) {
  const parts = splitRange(west, east);
  if (parts.length === 1) {
    const [w, e] = parts[0];
    return `[${w.toFixed(digits)}°, ${e.toFixed(digits)}°)`;
  }
  const [p1, p2] = parts;
  return `跨越±180°：[${p1[0].toFixed(digits)}°, 180.0000°) ∪ [-180.0000°, ${p2[1].toFixed(digits)}°)`;
}

function prettyLonRange(west, east) {
  if (west === east) return `[${west.toFixed(6)}°, ${east.toFixed(6)}°]（bbox 端点相同）`;
  const parts = splitRange(west, east);
  if (parts.length === 1) return `[${parts[0][0].toFixed(6)}°, ${parts[0][1].toFixed(6)}°]`;
  return `跨越±180°：[${parts[0][0].toFixed(6)}°, 180.000000°] ∪ [-180.000000°, ${parts[1][1].toFixed(6)}°]`;
}