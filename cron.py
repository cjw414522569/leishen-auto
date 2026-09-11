"""极简 cron 解析与下一次触发时间计算（只用标准库）。

标准 5 段：``分 时 日 月 星期``

- 每段支持 ``*``、``N``、``N-M``、``N,M``、``*/N``、``N-M/S``、``N/S``
- 月与星期也认 ``JAN``-``DEC`` / ``SUN``-``SAT``（大小写不敏感）
- 星期里 ``0`` 和 ``7`` 都表示周日

日与星期的组合遵循 cron 惯例：**两者都限定时取「或」**（任一个匹配就算触发），
只有一个限定时用那个。所以 ``0 0 1 * 1`` 表示「每月 1 号**或**每周一」。

时区用调用方传入的 ``datetime`` 的时区——本工具的定时模式跑在本地，跟随本机时区。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

# 一次最多向后找多少天。表达式合法但永远匹配不到（如 0 0 30 2 *）时用它兜底
SEARCH_DAYS = 366 * 5

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
WEEKDAY_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}


@dataclass(frozen=True)
class CronExpr:
    """解析好的 cron 表达式。"""

    raw: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_restricted: bool
    weekday_restricted: bool

    def matches_day(self, day: date) -> bool:
        if day.month not in self.months:
            return False

        day_ok = day.day in self.days
        # date.weekday(): 周一=0；cron: 周日=0
        weekday_ok = (day.weekday() + 1) % 7 in self.weekdays

        if self.day_restricted and self.weekday_restricted:
            return day_ok or weekday_ok
        if self.day_restricted:
            return day_ok
        if self.weekday_restricted:
            return weekday_ok
        return True


def _resolve(text: str, names: dict[str, int] | None) -> int:
    token = text.strip().lower()
    if names and token in names:
        return names[token]
    try:
        return int(token)
    except ValueError:
        raise ValueError(f"认不出 {text.strip()!r}") from None


def _parse_part(part: str, low: int, high: int, names: dict[str, int] | None) -> set[int]:
    step = 1
    base = part
    if "/" in part:
        base, _, step_text = part.partition("/")
        step = _resolve(step_text, None)
        if step < 1:
            raise ValueError(f"步长必须大于 0，当前是 {part!r}")

    if base.strip() == "*":
        start, end = low, high
    elif "-" in base:
        left, _, right = base.partition("-")
        start, end = _resolve(left, names), _resolve(right, names)
        if start > end:
            raise ValueError(f"区间起止反了：{part!r}")
    else:
        start = _resolve(base, names)
        # 单个值带步长（如 5/10）表示从该值一直到该段最大值
        end = high if step > 1 else start

    if start < low or end > high:
        raise ValueError(f"{part!r} 超出取值范围 {low}-{high}")

    return set(range(start, end + 1, step))


def _parse_field(
    text: str, low: int, high: int, label: str, names: dict[str, int] | None = None
) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part.strip():
            raise ValueError(f"{label}段里有空项：{text!r}")
        values |= _parse_part(part, low, high, names)
    return values


def parse_cron(expression: str) -> CronExpr:
    """解析 5 段 cron 表达式，出错抛 ``ValueError``。"""
    text = str(expression or "").strip()
    fields = text.split()
    if len(fields) != 5:
        raise ValueError(
            f"需要 5 段（分 时 日 月 星期），当前 {len(fields)} 段：{text!r}"
        )

    minutes = _parse_field(fields[0], 0, 59, "分钟")
    hours = _parse_field(fields[1], 0, 23, "小时")
    days = _parse_field(fields[2], 1, 31, "日")
    months = _parse_field(fields[3], 1, 12, "月", MONTH_NAMES)
    raw_weekdays = _parse_field(fields[4], 0, 7, "星期", WEEKDAY_NAMES)
    weekdays = {0 if value == 7 else value for value in raw_weekdays}

    return CronExpr(
        raw=text,
        minutes=frozenset(minutes),
        hours=frozenset(hours),
        days=frozenset(days),
        months=frozenset(months),
        weekdays=frozenset(weekdays),
        day_restricted=fields[2].strip() != "*",
        weekday_restricted=fields[4].strip() != "*",
    )


def next_run(expr: CronExpr, after: datetime) -> datetime:
    """严格晚于 ``after`` 的下一次触发时间。

    找不到就抛 ``ValueError``（例如 ``0 0 30 2 *``——2 月没有 30 号）。
    """
    start = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    hours = sorted(expr.hours)
    minutes = sorted(expr.minutes)

    day = start.date()
    for _ in range(SEARCH_DAYS):
        if expr.matches_day(day):
            for hour in hours:
                for minute in minutes:
                    candidate = start.replace(
                        year=day.year, month=day.month, day=day.day,
                        hour=hour, minute=minute,
                    )
                    if candidate >= start:
                        return candidate
        day += timedelta(days=1)

    raise ValueError(f"未来 {SEARCH_DAYS // 366} 年内都不会触发：{expr.raw!r}")
