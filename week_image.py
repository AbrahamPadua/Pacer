"""Draws the week-at-a-glance image the bot sends for the "week" command."""
import datetime as dt
import io
import math
import re
import textwrap

from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle

from scheduler_tools import TZ, get_upcoming_assignments, list_events

COURSE = re.compile(r"^([A-Z]{2,6} ?\d+[A-Z0-9]*)\s*(.*)$")
BG, COLUMN, TODAY, PAST, GRID, DIVIDER = "#0F1117", "#151822", "#1B2132", "#0A0C10", "#222736", "#262C3D"
TEXT, MUTED, NOW = "#F8FAFC", "#8E96AA", "#FF4757"
DUE_BG, DUE_EDGE, DUE_TEXT, DUE_TITLE = "#2D1522", "#FF5C77", "#FF8FA3", "#FFF0F3"
# Per course: accent stripe, card fill, Pacer block fill, time text
PALETTE = [("#38BDF8", "#142B3B", "#0E5A8A", "#BAE6FD"), ("#10B981", "#132E28", "#0E6B50", "#A7F3D0"),
           ("#F97316", "#332015", "#9A4210", "#FED7AA"), ("#F59E0B", "#332714", "#8F560A", "#FDE68A"),
           ("#EC4899", "#331A2C", "#8C2258", "#FBCFE8"), ("#4F8CFF", "#192745", "#2552B8", "#C2D9FF"),
           ("#A855F7", "#291D40", "#5F28A3", "#E9D5FF"), ("#84CC16", "#233015", "#487510", "#D9F99D")]
NO_COURSE = ("#64748B", "#1E2430", "#334155", "#CBD5E1")
FIG_W, FIG_H, DPI = 14.0, 9.8, 180
GRID_BOX, HEAD_BOX = (0.058, 0.032, 0.922, 0.802), (0.058, 0.838, 0.922, 0.145)


def week_png() -> bytes:
    """Today and the next 6 days, read from midnight so earlier classes still show."""
    now = dt.datetime.now(TZ)
    start = dt.datetime.combine(now.date(), dt.time(0), TZ)
    try:
        assignments = get_upcoming_assignments()["assignments"]
    except Exception:
        assignments = None  # still worth sending the calendar; the image says deadlines are missing
    return render_week(list_events(start, start + dt.timedelta(days=7)), assignments, now)


def _local(s):
    t = dt.datetime.fromisoformat(s)
    return t.astimezone(TZ) if t.tzinfo else t.replace(tzinfo=TZ)


def _hour(t):
    return t.hour + t.minute / 60


def _clock(t, compact=False):
    s = t.strftime("%I:%M %p").lstrip("0").replace(":00", "")
    return s.replace(" AM", "a").replace(" PM", "p") if compact else s


def _span(s, e, compact=False):
    """'9:30-10:50 AM', keeping both AM/PM marks only when they differ ('11 AM-12:20 PM')."""
    start, end = _clock(s, compact), _clock(e, compact)
    mark = end[-1:] if compact else end[-2:]
    return f"{start.removesuffix(mark).rstrip() if start.endswith(mark) else start}-{end}"


def layout(events, days):
    """Timed events as card pieces: split at midnight, and overlapping ones side by side (lane out of lanes)."""
    blocks = []
    for e in events:
        if "T" not in e["start"]:
            continue  # shortcut: all-day events are skipped; draw them as a band above the grid if you start using them
        s, end = _local(e["start"]), _local(e["end"])
        title = e["summary"].removeprefix("[Flex] ").strip()
        m = COURSE.match(title)
        while s < end:
            piece_end = min(end, dt.datetime.combine(s.date() + dt.timedelta(1), dt.time(0), TZ))
            if s.date() in days:
                blocks.append({"day": days.index(s.date()), "s": _hour(s), "e": _hour(piece_end) or 24.0,
                               "title": title, "course": m.group(1) if m else "", "rest": m.group(2) if m else "",
                               "flex": e["flexibility"] == "movable", "start": s, "end": piece_end})
            s = piece_end
    for d in range(len(days)):
        cluster, cluster_end, lanes = [], -1.0, []  # lanes holds each lane's last end time
        for b in sorted((b for b in blocks if b["day"] == d), key=lambda b: (b["s"], b["e"])) + [None]:
            if b is None or b["s"] >= cluster_end:
                for c in cluster:
                    c["lanes"] = len(lanes)
                if b is None:
                    break
                cluster, cluster_end, lanes = [], -1.0, []
            b["lane"] = next((i for i, last in enumerate(lanes) if last <= b["s"]), len(lanes))
            lanes[b["lane"]:b["lane"] + 1] = [b["e"]]
            cluster.append(b)
            cluster_end = max(cluster_end, b["e"])
    return blocks


def render_week(events, assignments, now) -> bytes:
    """The 7 days from `now` as a PNG. `assignments` is None when the deadlines couldn't be read."""
    days = [now.date() + dt.timedelta(d) for d in range(7)]
    blocks = layout(events, days)
    dues = {}
    for a in assignments or []:
        t = _local(a["due"])
        if t.date() in days:
            dues.setdefault((days.index(t.date()), _hour(t), _clock(t)), []).append((a["course"], a["title"]))
    courses = sorted({b["course"] for b in blocks if b["course"]} | {c for items in dues.values() for c, _ in items})
    colors = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(courses)}

    top = math.floor(min([8.0] + [b["s"] for b in blocks]))
    bottom = 24 if any(h > 22 for _, h, _ in dues) else math.ceil(max([22.0] + [b["e"] for b in blocks]))
    fig = Figure(figsize=(FIG_W, FIG_H), dpi=DPI, facecolor=BG)
    ax, head = fig.add_axes(GRID_BOX, facecolor=COLUMN), fig.add_axes(HEAD_BOX)
    ax.set_xlim(0, 7)
    ax.set_ylim(bottom, top)
    head.set_xlim(0, 7)
    head.set_ylim(0, 1)
    head.axis("off")
    # Sizes below are in points; px and py turn one point into data units across and down
    col_pt = FIG_W * GRID_BOX[2] * 72 / 7
    hour_pt = FIG_H * GRID_BOX[3] * 72 / (bottom - top)
    px, py, aspect = 1 / col_pt, 1 / hour_pt, col_pt / hour_pt
    head_aspect = col_pt / (FIG_H * HEAD_BOX[3] * 72)

    # Header: title, date range, legend, one card per day
    head.text(0, 0.8, "Week at a Glance", fontsize=18, fontweight="bold", color=TEXT, va="center")
    head.text(1.72, 0.79, f"{days[0]:%b} {days[0].day} - {days[-1]:%b} {days[-1].day}", fontsize=11.5,
              fontweight="bold", color="#94A3B8", va="center",
              bbox=dict(boxstyle="round,pad=0.35", facecolor="#1A1E2B", edgecolor="#2A3147"))
    if assignments is None:
        head.text(3.1, 0.79, "Deadlines unavailable", fontsize=9.5, fontweight="bold", color=DUE_TEXT, va="center")
    for x, label, face, edge in [(4.07, "Class / Meeting", "#192745", "#4F8CFF"),
                                 (5.02, "Flex Block (Movable)", "#2552B8", "#93BBFF"),
                                 (6.18, "Assignment Due", DUE_BG, DUE_EDGE)]:
        head.add_patch(Rectangle((x, 0.755), 16 * px, 0.07, facecolor=face, edgecolor=edge, lw=1.2))
        head.text(x + 22 * px, 0.79, label, fontsize=9.2, fontweight="bold", color="#CBD5E1", va="center")
    for d, day in enumerate(days):
        today = d == 0
        head.add_patch(FancyBboxPatch((d + 0.035, 0.04), 0.93, 0.48, boxstyle=f"round,pad=0,rounding_size={6 * px}",
                                      mutation_aspect=head_aspect, facecolor="#1E2942" if today else COLUMN,
                                      edgecolor="#3B82F6" if today else "#24293A", lw=1.6 if today else 0.9))
        head.text(d + 0.5, 0.36, f"{day:%a}".upper(), fontsize=8.8, fontweight="bold",
                  color="#60A5FA" if today else MUTED, ha="center", va="center")
        head.text(d + 0.5, 0.17, f"{day:%b} {day.day}", fontsize=10.8, fontweight="bold", color=TEXT,
                  ha="center", va="center")

    # Grid: today's column lighter, its past hours dimmed, hour lines, day dividers
    now_h = _hour(now)
    ax.add_patch(Rectangle((0, top), 1, bottom - top, facecolor=TODAY, zorder=0))
    if now_h > top:
        ax.add_patch(Rectangle((0, top), 1, min(now_h, bottom) - top, facecolor=PAST, alpha=0.48, zorder=0.5))
    for h in range(top, bottom + 1):
        ax.axhline(h, color=GRID, lw=0.75, zorder=1)
    for d in range(8):
        ax.axvline(d, color=DIVIDER, lw=1, zorder=1)
    ax.add_patch(Rectangle((0, top), 7, bottom - top, facecolor="none", edgecolor="#2E364B", lw=1.2, zorder=7))
    ax.set_yticks(range(top, bottom + 1))
    ax.set_yticklabels([dt.time(h % 24).strftime("%I %p").lstrip("0") for h in range(top, bottom + 1)],
                       fontsize=9.5, fontweight="bold", color=MUTED)
    ax.set_xticks([])
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    for b in blocks:
        _draw_card(ax, b, colors.get(b["course"], NO_COURSE), px, py, aspect, col_pt, hour_pt)

    # Deadlines as pills near their due time, stacked upward when a day has several
    pill_top = {}
    chars = int((0.93 * col_pt - 16) / (8 * 0.62))
    for (d, h, when), items in sorted(dues.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        height = (25 + 10.5 * (len(items) - 1)) * py
        y = min(max(top + 2 * py, h - height / 2), min(bottom - 2.2 * py, pill_top.get(d, bottom) - 2.5 * py) - height)
        pill_top[d] = y
        pill = FancyBboxPatch((d + 0.035, y), 0.93, height, boxstyle=f"round,pad=0,rounding_size={9.5 * px}",
                              mutation_aspect=aspect, facecolor=DUE_BG, edgecolor=DUE_EDGE, lw=1.4, zorder=4.5)
        ax.add_patch(pill)
        stripe = Rectangle((d + 0.035, y), 4.2 * px, height, facecolor=colors.get(items[0][0], NO_COURSE)[0],
                           zorder=4.6)
        stripe.set_clip_path(pill)
        ax.add_patch(stripe)
        single = len(items) == 1  # one deadline: course in the header so the title gets the whole line
        lines = [title if single else f"{course}: {title}" for course, title in items]
        clip = Rectangle((d + 0.035, y), 0.93, height, transform=ax.transData)
        for text, dy, size, color in [(f"DUE {when}  {items[0][0]}" if single else f"DUE {when}", 3.2, 7.3, DUE_TEXT),
                                      ("\n".join(textwrap.shorten(l, chars, placeholder="...") for l in lines),
                                       12.8, 8, DUE_TITLE)]:
            ax.text(d + 0.035 + 9 * px, y + dy * py, text, fontsize=size, fontweight="bold", color=color, va="top",
                    zorder=5, clip_on=True, clip_path=clip, linespacing=1.05)

    if top <= now_h <= bottom:
        ax.plot([0, 1], [now_h] * 2, color=NOW, lw=5, alpha=0.22, zorder=5.8, solid_capstyle="butt")
        ax.plot([0, 1], [now_h] * 2, color=NOW, lw=2, zorder=6)
        ax.plot([0.025], [now_h], "o", color=NOW, ms=6.5, zorder=6.2)
        ax.plot([0.025], [now_h], "o", color="#FFFFFF", ms=2.4, zorder=6.3)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    return buf.getvalue()


def _draw_card(ax, b, color, px, py, aspect, col_pt, hour_pt):
    accent, fill, flex_fill, time_color = color
    width = (0.93 - 0.022 * (b["lanes"] - 1)) / b["lanes"]
    x = b["day"] + 0.035 + b["lane"] * (width + 0.022)
    y, height = b["s"] + 1.2 * py, max(6 * py, b["e"] - b["s"] - 2.4 * py)
    w_pt, h_pt, narrow = width * col_pt, height * hour_pt, b["lanes"] > 1

    card = FancyBboxPatch((x, y), width, height, boxstyle=f"round,pad=0,rounding_size={5.5 * px}",
                          mutation_aspect=aspect, facecolor=flex_fill if b["flex"] else fill,
                          edgecolor=accent if b["flex"] else (accent, 0.35), lw=1.7 if b["flex"] else 1.0,
                          zorder=2.5 if b["flex"] else 2)
    ax.add_patch(card)
    stripe = Rectangle((x, y), (4.2 if b["flex"] else 3.4) * px, height, facecolor=accent, zorder=2.7)
    stripe.set_clip_path(card)
    ax.add_patch(stripe)
    badge = b["flex"] and w_pt >= 75 and h_pt >= 24
    if badge:
        bx, by = x + width - 32.5 * px, y + 3.8 * py
        ax.add_patch(FancyBboxPatch((bx, by), 28 * px, 11 * py, boxstyle=f"round,pad=0,rounding_size={3 * px}",
                                    mutation_aspect=aspect, facecolor=(1, 1, 1, 0.2), edgecolor=accent, lw=0.8,
                                    zorder=3.2))
        ax.text(bx + 14 * px, by + 5.7 * py, "FLEX", fontsize=6.8, fontweight="bold", color="#FFFFFF",
                ha="center", va="center", zorder=3.5)

    # Lines that fit: a bold title (wrapped), then smaller lines. A short Pacer block leads with the
    # assignment, since the card's color already says the course.
    title_fs, sub_fs = (8.6, 7.3) if narrow else (9.8, 8.2)
    when = _span(b["start"], b["end"], compact=narrow)
    if b["flex"] and b["rest"]:
        title, subs = (b["course"], [b["rest"], when]) if h_pt >= 42 else (b["rest"], [when])
    else:
        title, subs = b["title"], [when]
    room = w_pt - (8 if narrow else 13) - (33 if badge else 0)
    title_lines = textwrap.wrap(title, max(5, int(room / (title_fs * 0.62))))
    space = h_pt - 4.5
    while subs and title_fs * 1.2 + len(subs) * sub_fs * 1.17 > space:
        subs = subs[1:]
    shown = 1
    while shown < len(title_lines) and (shown + 1) * title_fs * 1.2 + len(subs) * sub_fs * 1.17 <= space:
        shown += 1

    clip = Rectangle((x, y), width, height, transform=ax.transData)  # text ignores the rounded card as a clip
    tx = x + (8 if narrow else 9.5) * px
    ax.text(tx, y + 3.5 * py, "\n".join(title_lines[:shown]), fontsize=title_fs, fontweight="bold",
            color="#FFFFFF" if b["flex"] else TEXT, va="top", zorder=3.5, linespacing=1.05, clip_on=True,
            clip_path=clip)
    if subs:
        ax.text(tx, y + (3.5 + shown * title_fs * 1.2) * py, "\n".join(subs), fontsize=sub_fs,
                fontweight="bold" if b["flex"] else "normal", color="#E2E8F0" if b["flex"] else time_color,
                va="top", zorder=3.5, linespacing=1.05, clip_on=True, clip_path=clip)
