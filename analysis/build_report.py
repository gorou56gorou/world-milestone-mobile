#!/usr/bin/env python3
"""しばたの郷土館 第二収蔵庫 温湿度モニタリングレポート生成スクリプト。

analysis/data/ のロガーデータ5点と外気データ(Open-Meteo ERA5)を読み込み、
管理基準(温度25±5℃・湿度55±5%)との比較・逸脱エピソード抽出を行って
analysis/report.html を生成する。
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUT_HTML = os.path.join(BASE_DIR, "report.html")

TEMP_LO, TEMP_HI = 20.0, 30.0   # 25℃ ± 5℃
RH_LO, RH_HI = 50.0, 60.0       # 55% ± 5%
SAMPLE_INTERVAL = timedelta(hours=2)  # ロガーの記録間隔
BASE_EPOCH = datetime(2026, 7, 15, tzinfo=timezone.utc)  # 分単位圧縮の基準時刻

ROOMS = [
    ("01_hall.txt", "hall", "①ホール"),
    ("02_room1_west.txt", "room1", "②第1教室（西）"),
    ("03_room2_center.txt", "room2", "③第2教室（中央）"),
    ("04_room3_east.txt", "room3", "④第3教室（東）"),
    ("05_north_storage.txt", "north", "⑤北側保管室"),
]


def to_minutes(dt: datetime) -> int:
    return int((dt.replace(tzinfo=timezone.utc) - BASE_EPOCH).total_seconds() // 60)


def parse_logger(path):
    """ロガーTXT (DATE TIME Tair RH WBGT) を [(datetime, temp, rh)] に変換する。"""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(
                r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2}\s+([\d.]+)\s+([\d.]+)\s+[\d.]+",
                line.strip(),
            )
            if m:
                dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
                rows.append((dt, float(m.group(3)), float(m.group(4))))
    return rows


def parse_outdoor(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    h = d["hourly"]
    rows = []
    for t, temp, rh in zip(h["time"], h["temperature_2m"], h["relative_humidity_2m"]):
        if temp is None or rh is None:
            continue
        rows.append((datetime.strptime(t, "%Y-%m-%dT%H:%M"), float(temp), float(rh)))
    meta = {"lat": d["latitude"], "lon": d["longitude"]}
    return rows, meta


def stats_of(values):
    return {
        "min": min(values),
        "max": max(values),
        "avg": round(sum(values) / len(values), 1),
    }


def deviation_rate(values, lo, hi):
    out = sum(1 for v in values if v < lo or v > hi)
    return round(100.0 * out / len(values), 1)


def episodes_of(rows, idx, lo, hi, unit):
    """連続する同種別の範囲外サンプルを1エピソードに統合する。

    終了時刻は最終範囲外サンプル + 記録間隔(2時間)。ピークは逸脱方向の極値。
    """
    episodes = []
    cur = None  # (dir, start_dt, last_dt, peak)
    for dt, *vals in rows:
        v = vals[idx]
        direction = "high" if v > hi else ("low" if v < lo else None)
        if direction is None:
            if cur:
                episodes.append(cur)
                cur = None
            continue
        if cur and cur["dir"] == direction:
            cur["last"] = dt
            cur["peak"] = max(cur["peak"], v) if direction == "high" else min(cur["peak"], v)
        else:
            if cur:
                episodes.append(cur)
            cur = {"dir": direction, "start": dt, "last": dt, "peak": v, "unit": unit}
    if cur:
        episodes.append(cur)
    for e in episodes:
        e["end"] = e["last"] + SAMPLE_INTERVAL
        e["hours"] = int((e["end"] - e["start"]).total_seconds() // 3600)
    return episodes


def fmt_dt(dt):
    return f"{dt.month}/{dt.day} {dt:%H:%M}"


KIND_LABEL = {
    ("temp", "high"): ("温度", "▲ 上限超過"),
    ("temp", "low"): ("温度", "▼ 下限未満"),
    ("rh", "high"): ("湿度", "▲ 上限超過"),
    ("rh", "low"): ("湿度", "▼ 下限未満"),
}


def episode_rows_html(temp_eps, rh_eps):
    merged = [("temp", e) for e in temp_eps] + [("rh", e) for e in rh_eps]
    merged.sort(key=lambda ke: ke[1]["start"])
    rows = []
    for kind, e in merged:
        metric, label = KIND_LABEL[(kind, e["dir"])]
        unit = "℃" if kind == "temp" else "%"
        rows.append(
            f'<tr><td><span class="dev {e["dir"]}">{metric} {label}</span></td>'
            f'<td>{fmt_dt(e["start"])}</td><td>{fmt_dt(e["end"])}</td>'
            f'<td class="num">約{e["hours"]}時間</td>'
            f'<td class="num">{e["peak"]}{unit}</td></tr>'
        )
    return "\n".join(rows), len(merged)


def severity_class(rate):
    if rate >= 50:
        return "sev-high"
    if rate >= 10:
        return "sev-mid"
    if rate > 0:
        return "sev-low"
    return "sev-ok"


def build():
    rooms = []
    for fname, rid, name in ROOMS:
        rows = parse_logger(os.path.join(DATA_DIR, fname))
        temps = [r[1] for r in rows]
        rhs = [r[2] for r in rows]
        rooms.append(
            {
                "id": rid,
                "name": name,
                "rows": rows,
                "t_stats": stats_of(temps),
                "h_stats": stats_of(rhs),
                "t_rate": deviation_rate(temps, TEMP_LO, TEMP_HI),
                "h_rate": deviation_rate(rhs, RH_LO, RH_HI),
                "t_eps": episodes_of(rows, 0, TEMP_LO, TEMP_HI, "℃"),
                "h_eps": episodes_of(rows, 1, RH_LO, RH_HI, "%"),
            }
        )

    outdoor, ometa = parse_outdoor(os.path.join(DATA_DIR, "outdoor_shibata.json"))
    o_temps = [r[1] for r in outdoor]
    o_rhs = [r[2] for r in outdoor]

    period_start = min(r["rows"][0][0] for r in rooms)
    period_end = max(r["rows"][-1][0] for r in rooms)

    # チャート用データ(JSON)。時刻は 2026-07-15 00:00 からの分数で圧縮する。
    payload = {
        "rooms": [
            {
                "id": r["id"],
                "name": r["name"],
                "data": [[to_minutes(dt), t, h] for dt, t, h in r["rows"]],
            }
            for r in rooms
        ],
        "outdoor": [[to_minutes(dt), t, h] for dt, t, h in outdoor],
        "limits": {"tempLo": TEMP_LO, "tempHi": TEMP_HI, "rhLo": RH_LO, "rhHi": RH_HI},
    }

    # ---- サマリー表 ----
    summary_rows = []
    for r in rooms:
        summary_rows.append(
            f'<tr><th scope="row"><a href="#{r["id"]}">{r["name"]}</a></th>'
            f'<td class="num">{r["t_stats"]["min"]:.1f} 〜 {r["t_stats"]["max"]:.1f}</td>'
            f'<td class="num">{r["t_stats"]["avg"]:.1f}</td>'
            f'<td class="num"><span class="pill {severity_class(r["t_rate"])}">{r["t_rate"]}%</span></td>'
            f'<td class="num">{r["h_stats"]["min"]:.1f} 〜 {r["h_stats"]["max"]:.1f}</td>'
            f'<td class="num">{r["h_stats"]["avg"]:.1f}</td>'
            f'<td class="num"><span class="pill {severity_class(r["h_rate"])}">{r["h_rate"]}%</span></td></tr>'
        )
    summary_table = "\n".join(summary_rows)

    # ---- 所見(データから機械的に導出) ----
    worst_h = max(rooms, key=lambda r: r["h_rate"])
    worst_h_peak = max(worst_h["rows"], key=lambda x: x[2])
    worst_t = max(rooms, key=lambda r: r["t_rate"])
    worst_t_peak = max(worst_t["rows"], key=lambda x: x[1])
    dry_rooms = [
        (r, [e for e in r["h_eps"] if e["dir"] == "low"]) for r in rooms
    ]
    dry_rooms = [(r, eps) for r, eps in dry_rooms if eps]
    dry_note = ""
    if dry_rooms:
        worst_dry_room, worst_dry_eps = max(
            dry_rooms, key=lambda re_: sum(e["hours"] for e in re_[1])
        )
        dry_note = (
            f"<li><strong>乾燥側の逸脱</strong>も発生しています。特に{worst_dry_room['name']}では"
            f"湿度50%未満の時間帯が計{sum(e['hours'] for e in worst_dry_eps)}時間"
            f"({len(worst_dry_eps)}回)あり、加湿・除湿双方の調整が必要です。</li>"
        )

    findings = f"""
      <li><strong>湿度の上限超過が最大の課題</strong>です。{worst_h['name']}は測定時間の {worst_h['h_rate']}% で湿度が管理範囲(50〜60%)を外れ、最高 {worst_h['h_stats']['max']}%({fmt_dt(worst_h_peak[0])})に達しました。カビ・虫害リスクが高い状態です。</li>
      <li><strong>温度は概ね管理範囲内</strong>です。逸脱率が最も高い{worst_t['name']}でも {worst_t['t_rate']}% で、最高 {worst_t['t_stats']['max']}℃({fmt_dt(worst_t_peak[0])})でした。夏季日中の上限(30℃)超過が中心です。</li>
      {dry_note}
      <li><strong>外気の影響</strong>: 期間中の外気は平均湿度 {round(sum(o_rhs)/len(o_rhs))}%(最高 {max(o_rhs):.0f}%)と多湿で、外気温 {min(o_temps):.1f}〜{max(o_temps):.1f}℃ の日変動が室内の温湿度変動と連動する傾向が見られます。開口部の管理と除湿運転の強化が有効と考えられます。</li>
    """

    # ---- 部屋別セクション ----
    sections = []
    for r in rooms:
        ep_html, ep_count = episode_rows_html(r["t_eps"], r["h_eps"])
        t_out_hours = sum(e["hours"] for e in r["t_eps"])
        h_out_hours = sum(e["hours"] for e in r["h_eps"])
        sections.append(f"""
    <section class="room" id="{r['id']}">
      <h2>{r['name']}</h2>
      <div class="room-meta">
        <span>温度 {r['t_stats']['min']:.1f}〜{r['t_stats']['max']:.1f}℃(平均 {r['t_stats']['avg']:.1f}℃)・逸脱 <span class="pill {severity_class(r['t_rate'])}">{r['t_rate']}%</span></span>
        <span>湿度 {r['h_stats']['min']:.1f}〜{r['h_stats']['max']:.1f}%(平均 {r['h_stats']['avg']:.1f}%)・逸脱 <span class="pill {severity_class(r['h_rate'])}">{r['h_rate']}%</span></span>
      </div>
      <div class="legend" aria-hidden="true">
        <span class="key"><i class="swatch line indoor"></i>室内</span>
        <span class="key"><i class="swatch line outdoor"></i>外気(参考)</span>
        <span class="key"><i class="swatch dot dev-dot"></i>基準逸脱</span>
        <span class="key"><i class="swatch band-chip"></i>目標範囲</span>
      </div>
      <h3>温度(℃)<small>目標 25℃±5℃</small></h3>
      <div class="chart-box"><canvas id="c-{r['id']}-t" role="img" aria-label="{r['name']}の温度推移グラフ"></canvas></div>
      <h3>湿度(%)<small>目標 55%±5%</small></h3>
      <div class="chart-box"><canvas id="c-{r['id']}-h" role="img" aria-label="{r['name']}の湿度推移グラフ"></canvas></div>
      <details class="episodes">
        <summary>基準逸脱の時間帯一覧 <span class="count">{ep_count}件</span>(温度 計約{t_out_hours}時間 / 湿度 計約{h_out_hours}時間)</summary>
        <div class="table-scroll"><table>
          <thead><tr><th>種別</th><th>開始</th><th>終了</th><th>継続</th><th>ピーク値</th></tr></thead>
          <tbody>{ep_html}</tbody>
        </table></div>
        <p class="note">連続する同種別の範囲外記録を1件に統合し、最終記録の約2時間後までを継続時間としています。</p>
      </details>
    </section>""")

    room_sections = "\n".join(sections)

    nav_links = "".join(
        f'<a href="#{r["id"]}">{r["name"]}</a>' for r in rooms
    ) + '<a href="#outdoor">外気</a>'

    generated = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

    html = HTML_TEMPLATE
    for k, v in {
        "@@NAV@@": nav_links,
        "@@SUMMARY_ROWS@@": summary_table,
        "@@FINDINGS@@": findings,
        "@@ROOM_SECTIONS@@": room_sections,
        "@@PAYLOAD@@": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "@@PERIOD@@": f"{period_start.year}年{period_start.month}月{period_start.day}日 〜 {period_end.month}月{period_end.day}日",
        "@@O_LAT@@": str(ometa["lat"]),
        "@@O_LON@@": str(ometa["lon"]),
        "@@O_TMIN@@": f"{min(o_temps):.1f}",
        "@@O_TMAX@@": f"{max(o_temps):.1f}",
        "@@O_HMIN@@": f"{min(o_rhs):.0f}",
        "@@O_HMAX@@": f"{max(o_rhs):.0f}",
        "@@GENERATED@@": generated,
    }.items():
        html = html.replace(k, v)

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    # 検証用サマリーを標準出力へ
    for r in rooms:
        print(
            f"{r['name']}: 温度 {r['t_stats']['min']}-{r['t_stats']['max']} 平均{r['t_stats']['avg']} "
            f"逸脱{r['t_rate']}% / 湿度 {r['h_stats']['min']}-{r['h_stats']['max']} 平均{r['h_stats']['avg']} "
            f"逸脱{r['h_rate']}% / エピソード {len(r['t_eps'])+len(r['h_eps'])}件"
        )
    print(f"外気: 温度 {min(o_temps)}-{max(o_temps)} / 湿度 {min(o_rhs)}-{max(o_rhs)} ({len(outdoor)}点)")
    print(f"HTML: {OUT_HTML} ({os.path.getsize(OUT_HTML)} bytes)")


HTML_TEMPLATE = r"""<title>第二収蔵庫 温湿度モニタリング</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    color-scheme: light;
    --bg: #f6f5f1;
    --surface: #fcfcfb;
    --ink: #1c1b18;
    --ink-2: #52514e;
    --muted: #898781;
    --accent: #2b4a70;
    --rule: #e1e0d9;
    --grid: #e7e6df;
    --axis: #c3c2b7;
    --series: #2a78d6;
    --outdoor: #898781;
    --bad: #d03b3b;
    --band: rgba(12, 163, 12, 0.07);
    --band-edge: rgba(12, 131, 12, 0.35);
    --pill-high-bg: rgba(208, 59, 59, 0.12);
    --pill-high-ink: #a32424;
    --pill-mid-bg: rgba(201, 133, 0, 0.14);
    --pill-mid-ink: #8a5b00;
    --pill-low-bg: rgba(137, 135, 129, 0.15);
    --pill-low-ink: #52514e;
    --pill-ok-bg: rgba(12, 163, 12, 0.12);
    --pill-ok-ink: #006300;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --bg: #111110;
      --surface: #1a1a19;
      --ink: #f4f3ef;
      --ink-2: #c3c2b7;
      --muted: #898781;
      --accent: #9db8d8;
      --rule: #2c2c2a;
      --grid: #262624;
      --axis: #383835;
      --series: #3987e5;
      --outdoor: #898781;
      --bad: #e05252;
      --band: rgba(12, 163, 12, 0.10);
      --band-edge: rgba(70, 180, 70, 0.40);
      --pill-high-bg: rgba(224, 82, 82, 0.18);
      --pill-high-ink: #f2a0a0;
      --pill-mid-bg: rgba(250, 178, 25, 0.16);
      --pill-mid-ink: #f0c46a;
      --pill-low-bg: rgba(137, 135, 129, 0.22);
      --pill-low-ink: #c3c2b7;
      --pill-ok-bg: rgba(12, 163, 12, 0.18);
      --pill-ok-ink: #7fd67f;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --bg: #111110;
    --surface: #1a1a19;
    --ink: #f4f3ef;
    --ink-2: #c3c2b7;
    --muted: #898781;
    --accent: #9db8d8;
    --rule: #2c2c2a;
    --grid: #262624;
    --axis: #383835;
    --series: #3987e5;
    --outdoor: #898781;
    --bad: #e05252;
    --band: rgba(12, 163, 12, 0.10);
    --band-edge: rgba(70, 180, 70, 0.40);
    --pill-high-bg: rgba(224, 82, 82, 0.18);
    --pill-high-ink: #f2a0a0;
    --pill-mid-bg: rgba(250, 178, 25, 0.16);
    --pill-mid-ink: #f0c46a;
    --pill-low-bg: rgba(137, 135, 129, 0.22);
    --pill-low-ink: #c3c2b7;
    --pill-ok-bg: rgba(12, 163, 12, 0.18);
    --pill-ok-ink: #7fd67f;
  }

  body {
    background: var(--bg);
    color: var(--ink);
    font-family: "Noto Sans JP", "Hiragino Kaku Gothic ProN", "Yu Gothic", Meiryo, system-ui, sans-serif;
    font-size: 15px;
    line-height: 1.75;
    padding: 0 20px;
    padding-block: 0 48px;
  }
  .wrap { max-width: 1020px; margin: 0 auto; }

  header.masthead { padding-block: 40px 20px; border-bottom: 2px solid var(--accent); }
  .eyebrow {
    font-size: 12px; letter-spacing: 0.18em; color: var(--ink-2);
    text-transform: none; margin: 0 0 6px;
  }
  h1 {
    font-family: "Shippori Mincho", "Hiragino Mincho ProN", "Yu Mincho", serif;
    font-weight: 700; font-size: clamp(24px, 4.5vw, 34px);
    margin: 0 0 10px; letter-spacing: 0.04em; text-wrap: balance; color: var(--ink);
  }
  .meta-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .chip {
    font-size: 13px; padding: 3px 12px; border: 1px solid var(--rule);
    border-radius: 999px; background: var(--surface); color: var(--ink-2);
  }
  .chip strong { color: var(--ink); font-weight: 600; }

  nav.jump { display: flex; flex-wrap: wrap; gap: 6px; padding-block: 14px; }
  nav.jump a {
    font-size: 13px; color: var(--accent); text-decoration: none;
    border: 1px solid var(--rule); border-radius: 999px; padding: 2px 12px;
    background: var(--surface);
  }
  nav.jump a:hover { border-color: var(--accent); }

  h2 {
    font-family: "Shippori Mincho", "Hiragino Mincho ProN", "Yu Mincho", serif;
    font-size: 21px; font-weight: 700; letter-spacing: 0.03em;
    margin: 40px 0 4px; padding-top: 8px; color: var(--ink);
  }
  h3 { font-size: 14px; font-weight: 600; margin: 20px 0 8px; color: var(--ink-2); }
  h3 small { font-weight: 400; color: var(--muted); margin-left: 10px; font-size: 12px; }

  .lead-note {
    background: var(--surface); border: 1px solid var(--rule); border-radius: 8px;
    padding: 14px 20px; margin-top: 20px; font-size: 14px;
  }
  .lead-note ul { margin: 6px 0 2px; padding-left: 20px; }
  .lead-note li { margin-block: 6px; }

  .table-scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 13.5px; background: var(--surface); }
  th, td { border: 1px solid var(--rule); padding: 6px 10px; text-align: left; }
  thead th { background: color-mix(in srgb, var(--accent) 7%, var(--surface)); font-weight: 600; white-space: nowrap; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  tbody th[scope="row"] { font-weight: 600; white-space: nowrap; }
  tbody th a { color: var(--accent); text-decoration: none; }
  tbody th a:hover { text-decoration: underline; }

  .pill {
    display: inline-block; min-width: 52px; text-align: center;
    border-radius: 999px; padding: 0 8px; font-size: 12.5px; font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .sev-high { background: var(--pill-high-bg); color: var(--pill-high-ink); }
  .sev-mid  { background: var(--pill-mid-bg);  color: var(--pill-mid-ink); }
  .sev-low  { background: var(--pill-low-bg);  color: var(--pill-low-ink); }
  .sev-ok   { background: var(--pill-ok-bg);   color: var(--pill-ok-ink); }

  section.room { border-top: 1px solid var(--rule); margin-top: 8px; }
  .room-meta { display: flex; flex-wrap: wrap; gap: 6px 24px; font-size: 13.5px; color: var(--ink-2); }

  .legend { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px; font-size: 12.5px; color: var(--ink-2); }
  .key { display: inline-flex; align-items: center; gap: 6px; }
  .swatch.line { width: 18px; height: 0; border-top: 2.5px solid var(--series); display: inline-block; }
  .swatch.line.outdoor { border-top: 2px solid var(--outdoor); }
  .swatch.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--bad); display: inline-block; }
  .swatch.band-chip { width: 18px; height: 11px; background: var(--band); border: 1px dashed var(--band-edge); display: inline-block; }

  .chart-box {
    position: relative; height: 250px; background: var(--surface);
    border: 1px solid var(--rule); border-radius: 6px; padding: 10px 12px 6px;
  }
  @media (max-width: 560px) { .chart-box { height: 215px; } }

  details.episodes { margin-top: 16px; }
  details.episodes summary {
    cursor: pointer; font-size: 14px; font-weight: 600; color: var(--accent);
    padding: 6px 0;
  }
  details.episodes .count {
    background: var(--pill-high-bg); color: var(--pill-high-ink);
    border-radius: 999px; padding: 0 10px; font-size: 12.5px; margin-inline: 4px;
  }
  .dev { white-space: nowrap; font-weight: 600; }
  .dev.high { color: var(--pill-high-ink); }
  .dev.low  { color: var(--accent); }
  .note { font-size: 12px; color: var(--muted); margin-top: 8px; }

  footer.src {
    margin-top: 48px; border-top: 1px solid var(--rule); padding-top: 14px;
    font-size: 12.5px; color: var(--muted);
  }
  footer.src p { margin: 4px 0; }
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Shippori+Mincho:wght@600;700&display=swap">

<div class="wrap">
  <header class="masthead">
    <p class="eyebrow">しばたの郷土館</p>
    <h1>第二収蔵庫 温湿度モニタリング</h1>
    <div class="meta-chips">
      <span class="chip">測定期間 <strong>@@PERIOD@@</strong></span>
      <span class="chip">記録間隔 <strong>2時間</strong></span>
      <span class="chip">管理基準 温度 <strong>25℃±5℃</strong>(20〜30℃)</span>
      <span class="chip">管理基準 湿度 <strong>55%±5%</strong>(50〜60%)</span>
    </div>
  </header>

  <nav class="jump" aria-label="部屋へ移動">@@NAV@@</nav>

  <section id="summary">
    <h2>部屋別サマリー</h2>
    <div class="table-scroll"><table>
      <thead>
        <tr>
          <th rowspan="2">測定場所</th>
          <th colspan="3">温度(℃)</th>
          <th colspan="3">湿度(%)</th>
        </tr>
        <tr>
          <th>最低〜最高</th><th>平均</th><th>逸脱率</th>
          <th>最低〜最高</th><th>平均</th><th>逸脱率</th>
        </tr>
      </thead>
      <tbody>@@SUMMARY_ROWS@@</tbody>
    </table></div>
    <div class="lead-note">
      <strong>所見</strong>
      <ul>@@FINDINGS@@</ul>
    </div>
  </section>

  @@ROOM_SECTIONS@@

  <section class="room" id="outdoor">
    <h2>柴田町の外気(比較用)</h2>
    <div class="room-meta">
      <span>外気温 @@O_TMIN@@〜@@O_TMAX@@℃ / 外気湿度 @@O_HMIN@@〜@@O_HMAX@@%</span>
      <span>Open-Meteo ERA5 再解析・1時間値(北緯@@O_LAT@@ / 東経@@O_LON@@ 付近)</span>
    </div>
    <div class="legend" aria-hidden="true">
      <span class="key"><i class="swatch line outdoor"></i>外気</span>
      <span class="key"><i class="swatch band-chip"></i>収蔵庫の目標範囲(参考)</span>
    </div>
    <h3>外気温(℃)</h3>
    <div class="chart-box"><canvas id="c-out-t" role="img" aria-label="柴田町の外気温推移グラフ"></canvas></div>
    <h3>外気湿度(%)</h3>
    <div class="chart-box"><canvas id="c-out-h" role="img" aria-label="柴田町の外気湿度推移グラフ"></canvas></div>
  </section>

  <footer class="src">
    <p>屋内データ: 温湿度ロガー5台(2時間間隔)。しばたの郷土館 第二収蔵庫にて測定。</p>
    <p>外気データ: Open-Meteo Historical Weather API(ERA5 再解析、宮城県柴田町 北緯@@O_LAT@@・東経@@O_LON@@ 付近の推定値)。アメダス実測値ではありません。</p>
    <p>基準逸脱の判定: 温度 20〜30℃・湿度 50〜60% の範囲外。レポート生成日: @@GENERATED@@</p>
  </footer>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const DATA = @@PAYLOAD@@;
const BASE_MS = Date.UTC(2026, 6, 15);          // 2026-07-15 00:00 (現地時刻をUTC扱いで統一)
const DAY = 86400000;
const X_MIN = BASE_MS;
const X_MAX = Date.UTC(2026, 7, 9);             // 2026-08-09 00:00

const charts = [];

function tokens() {
  const cs = getComputedStyle(document.body);
  const g = (n) => cs.getPropertyValue(n).trim();
  return {
    series: g('--series'), outdoor: g('--outdoor'), bad: g('--bad'),
    band: g('--band'), bandEdge: g('--band-edge'),
    grid: g('--grid'), axis: g('--axis'), muted: g('--muted'), ink2: g('--ink-2'),
    surface: g('--surface'),
  };
}

function fmtTick(ms) {
  const d = new Date(ms);
  return (d.getUTCMonth() + 1) + '/' + d.getUTCDate();
}
function fmtFull(ms) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return (d.getUTCMonth() + 1) + '/' + d.getUTCDate() + ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
}

// 目標範囲の帯と上下限の破線を描画するプラグイン
const bandPlugin = {
  id: 'band',
  beforeDatasetsDraw(chart, _args, opts) {
    if (!opts || opts.lo == null) return;
    const { ctx, chartArea: a, scales: { y } } = chart;
    const yLo = y.getPixelForValue(opts.lo);
    const yHi = y.getPixelForValue(opts.hi);
    ctx.save();
    ctx.fillStyle = opts.fill;
    ctx.fillRect(a.left, yHi, a.right - a.left, yLo - yHi);
    ctx.strokeStyle = opts.edge;
    ctx.setLineDash([5, 4]);
    ctx.lineWidth = 1;
    [yLo, yHi].forEach((yy) => {
      ctx.beginPath(); ctx.moveTo(a.left, yy); ctx.lineTo(a.right, yy); ctx.stroke();
    });
    ctx.restore();
  },
};
Chart.register(bandPlugin);

function makeChart(canvasId, cfg) {
  const el = document.getElementById(canvasId);
  if (!el) return;
  const tok = tokens();
  const { indoor, outdoor, lo, hi, yMin, yMax, unit, indoorLabel } = cfg;
  const out = (v) => v < lo || v > hi;

  const datasets = [];
  if (outdoor) {
    datasets.push({
      label: '外気', data: outdoor, parsing: false,
      borderColor: tok.outdoor, borderWidth: 1.4, pointRadius: 0,
      pointHoverRadius: 4, pointHoverBackgroundColor: tok.outdoor,
      tension: 0, spanGaps: true, order: 2,
    });
  }
  if (indoor) {
    datasets.push({
      label: indoorLabel || '室内', data: indoor, parsing: false,
      borderColor: tok.series, borderWidth: 2,
      segment: {
        borderColor: (c) => (out(c.p0.parsed.y) || out(c.p1.parsed.y)) ? tok.bad : tok.series,
      },
      pointRadius: (c) => out(c.parsed.y) ? 2.3 : 0,
      pointBackgroundColor: tok.bad, pointBorderWidth: 0,
      pointHoverRadius: 4.5,
      tension: 0, order: 1,
    });
  }

  const chart = new Chart(el, {
    type: 'line',
    data: { datasets },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      normalized: true,
      interaction: { mode: 'nearest', axis: 'x', intersect: false },
      plugins: {
        legend: { display: false },
        band: { lo, hi, fill: tok.band, edge: tok.bandEdge },
        tooltip: {
          displayColors: false,
          callbacks: {
            title: (items) => items.length ? fmtFull(items[0].parsed.x) : '',
            label: (item) => {
              const v = item.parsed.y;
              let s = item.dataset.label + ': ' + v.toFixed(1) + unit;
              if (item.datasetIndex === datasets.length - 1 && indoor && out(v)) {
                s += v > hi ? ' (上限超過)' : ' (下限未満)';
              }
              return s;
            },
          },
        },
      },
      scales: {
        x: {
          type: 'linear', min: X_MIN, max: X_MAX,
          ticks: {
            stepSize: 3 * DAY, callback: fmtTick,
            color: tok.muted, font: { size: 11 }, maxRotation: 0,
          },
          grid: { color: tok.grid }, border: { color: tok.axis },
        },
        y: {
          min: yMin, max: yMax,
          ticks: { color: tok.muted, font: { size: 11 } },
          grid: { color: tok.grid }, border: { color: tok.axis },
          title: { display: false },
        },
      },
    },
  });
  charts.push({ chart, cfg, canvasId });
}

function toXY(rows, idx) {
  return rows.map((r) => ({ x: BASE_MS + r[0] * 60000, y: r[idx] }));
}

function buildAll() {
  charts.forEach((c) => c.chart.destroy());
  charts.length = 0;
  Chart.defaults.font.family = "'Noto Sans JP', 'Hiragino Kaku Gothic ProN', Meiryo, system-ui, sans-serif";
  const L = DATA.limits;
  const outdoorT = toXY(DATA.outdoor, 1);
  const outdoorH = toXY(DATA.outdoor, 2);
  DATA.rooms.forEach((room) => {
    makeChart('c-' + room.id + '-t', {
      indoor: toXY(room.data, 1), outdoor: outdoorT,
      lo: L.tempLo, hi: L.tempHi, yMin: 15, yMax: 35, unit: '℃',
    });
    makeChart('c-' + room.id + '-h', {
      indoor: toXY(room.data, 2), outdoor: outdoorH,
      lo: L.rhLo, hi: L.rhHi, yMin: 30, yMax: 100, unit: '%',
    });
  });
  makeChart('c-out-t', {
    indoor: null, outdoor: outdoorT,
    lo: L.tempLo, hi: L.tempHi, yMin: 15, yMax: 35, unit: '℃',
  });
  makeChart('c-out-h', {
    indoor: null, outdoor: outdoorH,
    lo: L.rhLo, hi: L.rhHi, yMin: 30, yMax: 100, unit: '%',
  });
}

buildAll();

// テーマ変更(OS設定・ビューア側トグルの両方)に追従して再描画する
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', buildAll);
new MutationObserver(buildAll).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
</script>
"""


if __name__ == "__main__":
    build()
