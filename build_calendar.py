#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将校历与课程表合并成一个可订阅的 calendar.ics。

数据源:
    校历: 校历/basic.ics          (Google 日历导出, 只保留 >= CUTOFF 的事件)
    课程: 2026秋季课程/2026.md    (制表符分隔的课程表)

输出:
    calendar.ics   合并后的日历 (校历 + 课程)
    index.html     订阅说明页 (GitHub Pages 首页)

用法:
    python build_calendar.py
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 校历截取线: 只保留 2025-09-01 及之后的事件
CUTOFF = date(2025, 9, 1)
# 课程所属学年: 用于从校历中定位 "秋季学期第一周"
SEMESTER_YEAR = 2026
COURSE_SOURCE = ROOT / "2026秋季课程" / "2026.md"

CALENDAR_NAME = "中国科学技术大学校历与课程"
CALENDAR_DESC = "中国科学技术大学本科教学日历 + 个人课程表（合并订阅日历）"

# 中国科学技术大学上课节次时间表 (2026 年官方版)
PERIOD_TIMES: dict[int, tuple[time, time]] = {
    1: (time(7, 50), time(8, 35)),
    2: (time(8, 40), time(9, 25)),
    3: (time(9, 45), time(10, 30)),
    4: (time(10, 35), time(11, 20)),
    5: (time(11, 25), time(12, 10)),
    6: (time(14, 0), time(14, 45)),
    7: (time(14, 50), time(15, 35)),
    8: (time(15, 55), time(16, 40)),
    9: (time(16, 45), time(17, 30)),
    10: (time(17, 35), time(18, 20)),
    11: (time(19, 30), time(20, 15)),
    12: (time(20, 20), time(21, 5)),
    13: (time(21, 10), time(21, 55)),
}

WEEKDAY_NAMES = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def ics_escape(text: str) -> str:
    """转义 ICS 文本中的特殊字符。"""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def utf8_split(data: bytes, n: int) -> bytes:
    """在不超过 n 字节的 UTF-8 字符边界处截断。"""
    if len(data) <= n:
        return data
    cut = n
    while cut > 0 and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return data[:cut]


def fold_ics(text: str) -> str:
    """按 RFC 5545 将每行折叠到 75 字节, 使用 CRLF 行尾。"""
    out = []
    for line in text.split("\n"):
        data = line.encode("utf-8")
        while len(data) > 75:
            chunk = utf8_split(data, 75)
            out.append(chunk.decode("utf-8"))
            data = b" " + data[len(chunk):]
        out.append(data.decode("utf-8"))
    return "\r\n".join(out) + "\r\n"


def parse_date(value: str) -> date:
    """从 YYYYMMDD 开头的字符串解析日期。"""
    return datetime.strptime(value[:8], "%Y%m%d").date()


def parse_ics_events(text: str) -> list[dict]:
    """解析 ICS 文件, 返回每个 VEVENT 的 {属性: 值} 与原始行。"""
    events = []
    for block in re.findall(r"BEGIN:VEVENT\r?\n(.*?)\r?\nEND:VEVENT", text, re.S):
        lines: list[str] = ["BEGIN:VEVENT"]
        for raw in block.splitlines():
            raw = raw.rstrip("\r")
            if raw[:1] in (" ", "\t") and lines:
                lines[-1] += raw[1:]
            else:
                lines.append(raw)
        lines.append("END:VEVENT")
        props: dict[str, str] = {}
        for line in lines:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            props[key.split(";")[0]] = value
        events.append({"props": props, "lines": lines})
    return events


def parse_weeks(spec: str) -> list[int]:
    """解析起止周, 如 '2~18;18' / '1-8,10' -> [2..18] 去重。"""
    weeks: set[int] = set()
    for part in re.split(r"[;；,，、]+", spec):
        part = part.strip().rstrip("周")
        m = re.match(r"^(\d{1,2})\s*[~\-—–]\s*(\d{1,2})$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b:
                weeks.update(range(a, b + 1))
        elif re.match(r"^\d{1,2}$", part):
            weeks.add(int(part))
    return sorted(weeks)


SLOT_RE = re.compile(r"([^:：;\s][^:：;]*?)\s*[:：]\s*([1-7])\s*\(([^)]+)\)")


def parse_slots(spec: str) -> list[tuple[str, int, list[int]]]:
    """解析上课时间地点, 如 '2408: 4(3,4,5);2408: 5(11,12,13)'。"""
    slots = []
    for m in SLOT_RE.finditer(spec):
        room = m.group(1).strip()
        weekday = int(m.group(2))
        periods: set[int] = set()
        for tok in re.split(r"[，,、\s]+", m.group(3)):
            tok = tok.strip()
            if not tok:
                continue
            pm = re.match(r"^(\d{1,2})\s*[-~—]\s*(\d{1,2})$", tok)
            if pm:
                a, b = int(pm.group(1)), int(pm.group(2))
                periods.update(range(min(a, b), max(a, b) + 1))
            elif tok.isdigit():
                periods.add(int(tok))
        if periods:
            slots.append((room, weekday, sorted(periods)))
    return slots


def find_fall_week1_start(events: list[dict]) -> date:
    """从校历中定位目标学年 '秋季学期第一周' 的起始日 (周日)。"""
    for ev in events:
        props = ev["props"]
        if props.get("SUMMARY", "").strip() == "秋季学期第一周" and props["DTSTART"][:4] == str(SEMESTER_YEAR):
            return parse_date(props["DTSTART"])
    raise RuntimeError(f"校历中未找到 {SEMESTER_YEAR} 年 '秋季学期第一周' 事件")


def collect_holidays(events: list[dict]) -> set[date]:
    """收集校历中 '休（...）' 事件覆盖的日期, 用于跳过法定假日里的课程。"""
    days: set[date] = set()
    for ev in events:
        props = ev["props"]
        if props.get("SUMMARY", "").strip().startswith("休（"):
            d0 = parse_date(props["DTSTART"])
            d1 = parse_date(props["DTEND"])
            d = d0
            while d < d1:
                days.add(d)
                d += timedelta(days=1)
    return days


def course_event_lines(
    class_id: str,
    name: str,
    teacher: str,
    week: int,
    room: str,
    weekday: int,
    periods: list[int],
    day: date,
) -> list[str]:
    """生成单次课程事件的 ICS 行。"""
    start = datetime.combine(day, PERIOD_TIMES[periods[0]][0])
    end = datetime.combine(day, PERIOD_TIMES[periods[-1]][1])
    seed = f"{class_id}|{name}|{week}|{day.isoformat()}|{room}|{weekday}|{periods}"
    uid = hashlib.md5(seed.encode("utf-8")).hexdigest()[:16]
    pdesc = f"{periods[0]}-{periods[-1]}节" if len(periods) > 1 else f"{periods[0]}节"
    return [
        "BEGIN:VEVENT",
        f"UID:course-{uid}@ustc-calendar",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART;TZID=Asia/Shanghai:{start.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND;TZID=Asia/Shanghai:{end.strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:{ics_escape(name)}",
        f"LOCATION:{ics_escape(room)}",
        f"DESCRIPTION:{ics_escape(f'{class_id} {teacher} 第{week}周 {WEEKDAY_NAMES[weekday]} {pdesc}')}",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        "END:VEVENT",
    ]


def build_course_events(events: list[dict], holidays: set[date], week1_start: date) -> tuple[list[str], list[str]]:
    """读取课程表并生成课程事件行, 返回 (事件行, 警告)。"""
    lines: list[str] = []
    warnings: list[str] = []
    if not COURSE_SOURCE.exists():
        return lines, [f"未找到课程文件: {COURSE_SOURCE}"]

    for row_no, raw in enumerate(COURSE_SOURCE.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("课堂号"):
            continue
        cols = line.split("\t")
        if len(cols) < 5:
            cols = re.split(r"\s+", line)
        if len(cols) < 5:
            warnings.append(f"课程表第 {row_no} 行格式无法识别, 已跳过: {line[:60]}")
            continue
        class_id, name, week_spec, teacher, slot_spec = cols[:5]
        weeks = parse_weeks(week_spec)
        slots = parse_slots(slot_spec)
        if not weeks:
            warnings.append(f"课程 '{name}' 起止周解析失败: {week_spec}")
            continue
        if not slots:
            warnings.append(f"课程 '{name}' 时间地点解析失败: {slot_spec}")
            continue
        for week in weeks:
            for room, weekday, periods in slots:
                day = week1_start + timedelta(days=(week - 1) * 7 + weekday)
                if day in holidays:
                    continue
                lines.extend(course_event_lines(class_id, name, teacher, week, room, weekday, periods, day))
    return lines, warnings


def detect_calendar_url() -> str:
    """从 git remote 推断 Pages 订阅地址, 失败则给出占位符。"""
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=ROOT, check=False,
        ).stdout.strip()
        m = re.search(r"(?:github\.com[:/])([^/]+)/([^/.]+?)(?:\.git)?$", out)
        if m:
            return f"https://{m.group(1)}.github.io/{m.group(2)}/calendar.ics"
    except OSError:
        pass
    return "https://<你的GitHub用户名>.github.io/Calendar/calendar.ics"


def render_index(calendar_url: str) -> str:
    webcal_url = calendar_url.replace("https://", "webcal://", 1)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>校历与课程日历</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       max-width: 720px; margin: 40px auto; padding: 0 16px; color: #222; line-height: 1.7; }}
h1 {{ font-size: 1.6em; }}
.url {{ background: #f4f4f5; border: 1px solid #ddd; border-radius: 8px;
        padding: 12px 14px; font-family: ui-monospace, Consolas, monospace;
        word-break: break-all; font-size: 0.95em; }}
a {{ color: #0969da; }}
h2 {{ font-size: 1.2em; margin-top: 32px; }}
ol {{ padding-left: 22px; }}
</style>
</head>
<body>
<h1>校历与课程日历</h1>
<p>中国科学技术大学本科教学日历 + 个人课程表，已合并为一个可订阅的 iCal 日历，约每天自动同步。</p>
<p class="url">{html_escape(calendar_url)}</p>

<h2>Google 日历</h2>
<ol>
<li>打开 Google 日历网页版</li>
<li>左侧“其他日历”旁点 <b>+</b>，选择“从网址添加”</li>
<li>粘贴上面的订阅地址，点击“添加日历”</li>
</ol>

<h2>Apple 日历 (iPhone / Mac)</h2>
<ol>
<li>点击 <a href="{html_escape(webcal_url)}">此处订阅</a>（或手动添加：文件 → 新建日历订阅）</li>
<li>粘贴地址：<span class="url" style="padding:2px 6px;">{html_escape(webcal_url)}</span></li>
<li>按需调整同步频率，点击“订阅”</li>
</ol>

<h2>Outlook</h2>
<ol>
<li>Outlook 中：文件 → 账户设置 → 账户设置</li>
<li>选择“Internet 日历”选项卡 → 新建</li>
<li>粘贴订阅地址 → 添加</li>
</ol>

<h2>更新日历</h2>
<ol>
<li>编辑 <code>2026秋季课程/2026.md</code> 增加或修改课程</li>
<li>本地运行 <code>python build_calendar.py</code> 重新生成</li>
<li>提交并推送到 GitHub，Pages 会自动重新部署（也可手动触发 Actions）</li>
</ol>
</body>
</html>
"""


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def main() -> int:
    source_path = ROOT / "校历" / "basic.ics"
    raw = source_path.read_text(encoding="utf-8")
    all_events = parse_ics_events(raw)

    cutoff_str = CUTOFF.strftime("%Y%m%d")
    kept = [ev for ev in all_events if ev["props"].get("DTSTART", "")[:8] >= cutoff_str]
    kept.sort(key=lambda ev: ev["props"].get("DTSTART", ""))

    week1_start = find_fall_week1_start(kept)
    holidays = collect_holidays(kept)
    course_lines, warnings = build_course_events(kept, holidays, week1_start)

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    body = [f"PRODID:-//USTC Calendar//校历与课程//CN", "VERSION:2.0", "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH", f"X-WR-CALNAME:{CALENDAR_NAME}",
            "X-WR-TIMEZONE:Asia/Shanghai", f"X-WR-CALDESC:{CALENDAR_DESC}"]
    body.extend(
        [
            "BEGIN:VTIMEZONE",
            "TZID:Asia/Shanghai",
            "BEGIN:STANDARD",
            "DTSTART:19700101T000000",
            "TZOFFSETFROM:+0800",
            "TZOFFSETTO:+0800",
            "TZNAME:CST",
            "END:STANDARD",
            "END:VTIMEZONE",
        ]
    )
    for ev in kept:
        body.extend(ev["lines"])
    body.extend(course_lines)
    ics_text = "BEGIN:VCALENDAR\r\n" + fold_ics("\n".join(body)) + "END:VCALENDAR\r\n"

    out_ics = ROOT / "calendar.ics"
    out_ics.write_bytes(ics_text.encode("utf-8"))

    calendar_url = detect_calendar_url()
    (ROOT / "index.html").write_text(render_index(calendar_url), encoding="utf-8")

    course_count = sum(1 for line in course_lines if line == "BEGIN:VEVENT")
    first_course = min((parse_date(ev["props"]["DTSTART"]) for ev in kept), default=None)
    last_course = max((parse_date(ev["props"]["DTSTART"]) for ev in kept), default=None)
    print(f"校历事件: {len(kept)} 个 (原 {len(all_events)} 个, 截取 {CUTOFF} 之后)")
    print(f"校历日期范围: {first_course} ~ {last_course}")
    print(f"课程事件: {course_count} 个")
    print(f"已生成: {out_ics}")
    print(f"已生成: {ROOT / 'index.html'}")
    if calendar_url.startswith("https://<"):
        print("提示: 尚未检测到 git remote, index.html 中的订阅地址为占位符, 推送后可重新运行以更新。")
    for w in warnings:
        print(f"警告: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
