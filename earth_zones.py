#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地球经度分区（10区，每区 36°）

规则（按你的设定实现）：
- 每个分区宽度 = 36°，共 10 个分区（0~9）。
- 以“廊坊经线”为第9区的东边界（右开），第9区为其西侧 36°：
    第9区 = [廊坊经度-36, 廊坊经度)
- 往东（右侧）分区号递减：第8区、第7区…
- 往西（左侧）分区号递增并取模回绕：…第0区紧邻第9区西侧。

输入：
- 城市/省份/国家等名称（联网：OpenStreetMap Nominatim via geopy）
- 或直接输入经度：如 116.7 或 -74.006
- 或输入 "经度,纬度"：如 116.7,39.9（只用经度）

输出：
- 若能拿到地点的 bbox（常见于国家/省州）：输出该地点“经度区间”以及其覆盖到的所有分区（并分别列出每个分区的区间）。
- 若只有点坐标（常见于城市/POI）：输出点经度及其所属分区（含区间）。

依赖（用于输入地点名自动查经度）：
    pip install geopy
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


# ===== 可按需要调整的参数 =====
ZONE_WIDTH_DEG = 36.0
# 用“廊坊经线”作为第9区的东边界（不含）。默认取 116.7°E
LANGFANG_LON_EAST_BOUNDARY = 116.7


def normalize_lon_edge(lon: float) -> float:
    """把经度归一化到 [-180, 180]，并保留 +180（不折叠成 -180）。

    说明：
    - 点经度我们常用 [-180, 180)；
    - 但 bbox 边界若用 [-180, 180) 会把 +180 折叠到 -180，导致区间端点“看起来相同”。
    """
    x = (lon + 180.0) % 360.0 - 180.0
    # 这里的 x 可能是 -180（对应原始 lon 为 180 或 -180 或其他等价值）
    # 如果原始 lon 是正向的 180（或等价 540 等），我们把它当作 +180 保留
    if x == -180.0 and lon > 0:
        return 180.0
    return x


def normalize_lon_point(lon: float) -> float:
    """点经度归一化到 [-180, 180)；把 +180 视为 -180。"""
    x = normalize_lon_edge(lon)
    if x == 180.0:
        return -180.0
    return x


def parse_lon_from_text(s: str) -> Optional[float]:
    """解析经度输入：支持 '116.7'、'116.7,39.9'、'116.7 39.9'。"""
    s = s.strip()
    m = re.match(r"^\s*([-+]?\d+(?:\.\d+)?)\s*(?:[,\s]\s*([-+]?\d+(?:\.\d+)?))?\s*$", s)
    if not m:
        return None
    try:
        lon = float(m.group(1))
    except ValueError:
        return None
    # 兼容 0~360
    if lon < -180 or lon > 180:
        lon = normalize_lon_point(lon)
    return lon


# ==== 辅助函数：经度区间最小覆盖、GeoJSON 提取等 ====

def lon_to_360(lon: float) -> float:
    """经度映射到 [0, 360)。"""
    return lon % 360.0


def lon360_to_edge(lon360: float) -> float:
    """把 [0,360) 的经度转换到 [-180,180]（边界用，保留 +180）。"""
    x = lon360
    if x > 180.0:
        x = x - 360.0
    # 允许返回 180.0
    if x == -180.0:
        return -180.0
    return x


def circular_min_cover_interval(lons: List[float]) -> Optional[Tuple[float, float]]:
    """在圆周上计算覆盖所有点的最短经度区间。

    输入 lons：任意经度（度）。
    返回 (west, east) 作为 bbox 边界经度（允许跨越±180°），使用 [-180,180] 边界表示。

    算法：把经度映射到 [0,360)，排序，找最大间隙；最短覆盖区间是其补集。
    """
    if not lons:
        return None

    pts = sorted(lon_to_360(l) for l in lons)
    if len(pts) == 1:
        w = lon360_to_edge(pts[0])
        return (w, w)

    # 找最大 gap
    max_gap = -1.0
    max_i = 0
    for i in range(len(pts)):
        a = pts[i]
        b = pts[(i + 1) % len(pts)]
        gap = (b - a) % 360.0
        if gap > max_gap:
            max_gap = gap
            max_i = i

    # 最大 gap 从 pts[max_i] 到 pts[max_i+1]，覆盖区间取其补集：
    east_start = pts[(max_i + 1) % len(pts)]
    west_start = pts[max_i]

    # 覆盖区间从 east_start 向前到 west_start（沿正向增加）
    west_360 = east_start
    east_360 = west_start

    west = lon360_to_edge(west_360)
    east = lon360_to_edge(east_360)

    # 注意：这里 west/east 可能 west<=east（不跨线）或 west>east（跨线），交给 split_range 处理
    return (west, east)


def extract_lons_from_geojson(geom) -> List[float]:
    """从 Nominatim 返回的 GeoJSON geometry 提取所有经度点。"""
    lons: List[float] = []
    if not geom or not isinstance(geom, dict):
        return lons

    def walk_coords(obj):
        if isinstance(obj, (list, tuple)):
            if len(obj) == 2 and all(isinstance(x, (int, float)) for x in obj):
                # [lon, lat]
                lons.append(float(obj[0]))
            else:
                for it in obj:
                    walk_coords(it)

    coords = geom.get("coordinates")
    walk_coords(coords)
    return lons


@dataclass(frozen=True)
class ZoneInterval:
    zone: int
    west: float
    east: float


@dataclass(frozen=True)
class PlaceResult:
    query: str
    center_lon: float
    center_zone: ZoneInterval
    # 地点经度范围（若可得）
    bbox_west: Optional[float] = None
    bbox_east: Optional[float] = None
    covered_zones: Tuple[ZoneInterval, ...] = ()
    note: Optional[str] = None


def build_zone_intervals(east_boundary_zone9: float = LANGFANG_LON_EAST_BOUNDARY) -> List[ZoneInterval]:
    """构造 10 个分区的经度区间（每区 36°，左闭右开）。"""
    east_b = normalize_lon_point(east_boundary_zone9)
    origin = normalize_lon_point(east_b - ZONE_WIDTH_DEG)  # 第9区西边界

    intervals: List[ZoneInterval] = []
    for steps_east in range(10):
        zone = (9 - steps_east) % 10
        west = normalize_lon_point(origin + steps_east * ZONE_WIDTH_DEG)
        east = normalize_lon_point(west + ZONE_WIDTH_DEG)
        intervals.append(ZoneInterval(zone=zone, west=west, east=east))
    return intervals


def lon_to_zone_interval(lon: float, east_boundary_zone9: float = LANGFANG_LON_EAST_BOUNDARY) -> ZoneInterval:
    """将点经度映射到一个分区（返回该分区的区间）。"""
    lon_n = normalize_lon_point(lon)
    east_b = normalize_lon_point(east_boundary_zone9)
    origin = normalize_lon_point(east_b - ZONE_WIDTH_DEG)  # 第9区西边界

    diff = (lon_n - origin) % 360.0
    steps_east = int(math.floor(diff / ZONE_WIDTH_DEG))  # 0..9
    zone = (9 - steps_east) % 10

    west = normalize_lon_point(origin + steps_east * ZONE_WIDTH_DEG)
    east = normalize_lon_point(west + ZONE_WIDTH_DEG)
    return ZoneInterval(zone=zone, west=west, east=east)


def split_range(west: float, east: float) -> List[Tuple[float, float]]:
    """把可能跨越日期变更线的区间拆成 1~2 段（每段满足 a < b）。"""
    w = normalize_lon_edge(west)
    e = normalize_lon_edge(east)
    if w <= e:
        return [(w, e)]
    # 跨越 -180/180： [w, 180) U [-180, e)
    return [(w, 180.0), (-180.0, e)]


def ranges_intersect(a: Tuple[float, float], b: Tuple[float, float]) -> bool:
    a0, a1 = a
    b0, b1 = b
    return (a0 < b1) and (b0 < a1)


def zones_covered_by_lon_range(bbox_west: float, bbox_east: float, east_boundary_zone9: float = LANGFANG_LON_EAST_BOUNDARY) -> Tuple[ZoneInterval, ...]:
    """给定地点经度范围（west/east），返回覆盖到的所有分区。"""
    parts = split_range(bbox_west, bbox_east)
    intervals = build_zone_intervals(east_boundary_zone9)

    covered: List[ZoneInterval] = []
    for zi in intervals:
        zi_parts = split_range(zi.west, zi.east)
        hit = False
        for p in parts:
            for zp in zi_parts:
                if ranges_intersect(p, zp):
                    hit = True
                    break
            if hit:
                break
        if hit:
            covered.append(zi)

    # 显示稳定：按区号从小到大
    covered.sort(key=lambda x: x.zone)
    return tuple(covered)


def pretty_range(west: float, east: float, digits: int = 4) -> str:
    """分区区间展示（左闭右开）。若跨越±180°，用并集形式显示。"""
    parts = split_range(west, east)
    if len(parts) == 1:
        w, e = parts[0]
        return f"[{w:.{digits}f}°, {e:.{digits}f}°)"
    p1, p2 = parts
    return (
        f"跨越±180°：[{p1[0]:.{digits}f}°, 180.0000°) ∪ [-180.0000°, {p2[1]:.{digits}f}°)"
    )


def pretty_lon_range(west: float, east: float) -> str:
    """地点经度区间的友好展示（若跨越±180°会显示拆分）。

    注意：若 west==east，可能表示：
    - bbox 退化为一条经线（非常窄）；
    - 或服务返回了特殊/不完整的 bbox。
    """
    # 这里 west/east 已是边界归一化后的值（允许 +180）
    if west == east:
        return f"[{west:.6f}°, {east:.6f}°]（bbox 端点相同，可能为退化/不完整范围）"

    parts = split_range(west, east)
    if len(parts) == 1:
        w, e = parts[0]
        return f"[{w:.6f}°, {e:.6f}°]"
    p1, p2 = parts
    return (
        f"跨越±180°：[{p1[0]:.6f}°, 180.000000°] ∪ [-180.000000°, {p2[1]:.6f}°]"
    )


def geocode_place(query: str) -> Tuple[Optional[float], Optional[Tuple[float, float]], Optional[str]]:
    """在线地理编码：返回 (中心点经度, (bbox_west,bbox_east) 或 None, 说明/错误信息)"""
    try:
        from geopy.geocoders import Nominatim
    except Exception:
        return None, None, "未安装 geopy：请先运行 pip install geopy，或直接输入经度。"

    try:
        geolocator = Nominatim(user_agent="earth_zone_mapper/1.1")
        loc = geolocator.geocode(query, language="zh", geometry="geojson")
        if not loc:
            loc = geolocator.geocode(query, language="en", geometry="geojson")
        if not loc:
            return None, None, "未找到该地点，请尝试更完整的写法（如 'Bangkok, Thailand'）。"

        bbox = None
        try:
            raw = getattr(loc, "raw", None) or {}
            bb = raw.get("boundingbox")
            # Nominatim boundingbox 通常是 [south_lat, north_lat, west_lon, east_lon]（字符串）
            if isinstance(bb, (list, tuple)) and len(bb) == 4:
                west = normalize_lon_edge(float(bb[2]))
                east = normalize_lon_edge(float(bb[3]))
                bbox = (west, east)

            # 修正：有些跨越日期变更线的国家会返回 [-180,180] 这种“全球 bbox”
            if bbox is not None:
                west, east = bbox
                if west == -180.0 and east == 180.0:
                    geom = raw.get("geojson") or raw.get("geometry")
                    lons = extract_lons_from_geojson(geom)
                    tight = circular_min_cover_interval(lons)
                    if tight is not None:
                        bbox = (normalize_lon_edge(tight[0]), normalize_lon_edge(tight[1]))
        except Exception:
            bbox = None

        return float(loc.longitude), bbox, f"匹配到：{loc.address}"
    except Exception as e:
        return None, None, f"地理编码失败（可能是网络/服务限制）：{e}"


def resolve_query_to_place(query: str) -> Optional[PlaceResult]:
    """把用户输入解析为 PlaceResult。"""
    # 1) 当作经度（或经度,纬度）
    lon = parse_lon_from_text(query)
    if lon is not None:
        center_zone = lon_to_zone_interval(lon)
        return PlaceResult(query=query, center_lon=normalize_lon_point(lon), center_zone=center_zone)

    # 2) 当作地点名（在线）
    lon2, bbox, note = geocode_place(query)
    if lon2 is None:
        print(f"❌ {note}")
        return None

    center_zone = lon_to_zone_interval(lon2)

    covered: Tuple[ZoneInterval, ...] = ()
    bbox_w, bbox_e = None, None
    if bbox is not None:
        bbox_w, bbox_e = bbox
        covered = zones_covered_by_lon_range(bbox_w, bbox_e)

    return PlaceResult(
        query=query,
        center_lon=normalize_lon_point(lon2),
        center_zone=center_zone,
        bbox_west=bbox_w,
        bbox_east=bbox_e,
        covered_zones=covered,
        note=note,
    )


def print_place_result(res: PlaceResult) -> None:
    if res.note:
        print(f"ℹ️  {res.note}")

    print(f"✅ 输入：{res.query}")

    # 地点经度区间（若有 bbox）
    if res.bbox_west is not None and res.bbox_east is not None:
        print("   地点经度区间：" + pretty_lon_range(res.bbox_west, res.bbox_east))
    else:
        print(f"   点经度：{res.center_lon:.6f}°")

    # 所属分区输出
    if res.covered_zones:
        zones_list = ", ".join(str(z.zone) for z in res.covered_zones)
        print(f"   覆盖分区：{zones_list}")
        for z in res.covered_zones:
            print(f"     - 第 {z.zone} 区区间：{pretty_range(z.west, z.east)}  （左闭右开）")
    else:
        z = res.center_zone
        print(f"   所属分区：第 {z.zone} 区")
        print(f"   分区区间：{pretty_range(z.west, z.east)}  （左闭右开）")

    print("")


def main() -> int:
    print("\n地球经度分区（10区，每区 36°）")
    print(f"默认：第9区 = [{LANGFANG_LON_EAST_BOUNDARY:.4f}°-36°, {LANGFANG_LON_EAST_BOUNDARY:.4f}°) （以廊坊经线为第9区东边界）")
    print("输入地点名或经度；输入 q 退出。\n")

    while True:
        s = input("请输入地点/经度：").strip()
        if not s:
            continue
        if s.lower() in {"q", "quit", "exit"}:
            return 0

        res = resolve_query_to_place(s)
        if res is None:
            continue
        print_place_result(res)


if __name__ == "__main__":
    raise SystemExit(main())