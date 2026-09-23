#!/usr/bin/env python3
"""Count daily LLM token usage from local coding-agent logs.

Reads the session logs that Claude Code (~/.claude/projects) and Codex
(~/.codex/sessions) keep on this machine, merges the per-day totals into
src/data/token-usage.json, and renders the heatmap SVGs that the GitHub profile
README embeds.

The agents prune old logs (Claude Code keeps 30 days by default), so the JSON
file is the long-term record: a stored day is only replaced by a larger count,
unless --rebuild is given.

Usage: python3 scripts/token_usage.py [--rebuild] [--tz Area/City]
"""
import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'src' / 'data' / 'token-usage.json'
SVGS = {'light': ROOT / 'src' / 'images' / 'token-usage.svg',
        'dark': ROOT / 'src' / 'images' / 'token-usage-dark.svg'}

SOURCES = {'claude': 'Claude Code', 'codex': 'Codex'}
FIELDS = ['input', 'output', 'cache_read', 'cache_write']
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def num(value):
    return value if isinstance(value, int) else 0


class Tally:
    """Token counts per local day and source, in FIELDS order."""

    def __init__(self, tz):
        self.tz = tz
        self.days = {}

    def add(self, timestamp, source, counts):
        try:
            when = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        except (AttributeError, ValueError):
            return
        day = when.astimezone(self.tz).date().isoformat()
        row = self.days.setdefault(day, {}).setdefault(source, [0] * len(FIELDS))
        for i, n in enumerate(counts):
            row[i] += max(0, n)


def read_jsonl(path, *needles):
    """Yield the JSON objects on lines containing any needle (a cheap pre-filter)."""
    try:
        with open(path, encoding='utf-8', errors='replace') as lines:
            for line in lines:
                if any(needle in line for needle in needles):
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(entry, dict):
                        yield entry
    except OSError:
        return


# --- Claude Code -------------------------------------------------------------

CLAUDE_KEYS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')


def claude_project_dirs():
    roots = [Path(p) for p in os.environ.get('CLAUDE_CONFIG_DIR', '').split(',') if p]
    roots += [Path.home() / '.config' / 'claude', Path.home() / '.claude']
    return [root / 'projects' for root in dict.fromkeys(roots) if (root / 'projects').is_dir()]


def collect_claude(tally):
    # A response is logged as one line per content block, all sharing the message
    # and request ids (resumed and forked sessions copy them too). Streaming can
    # leave partial output counts on the earlier lines, so keep the maximum.
    responses = {}
    for projects in claude_project_dirs():
        for path in projects.rglob('*.jsonl'):
            for entry in read_jsonl(path, '"usage"'):
                message = entry.get('message')
                if entry.get('type') != 'assistant' or not isinstance(message, dict):
                    continue
                usage = message.get('usage')
                if not isinstance(usage, dict) or message.get('model') == '<synthetic>':
                    continue
                key = (message.get('id'), entry.get('requestId')) if message.get('id') else entry.get('uuid')
                counts = [num(usage.get(k)) for k in CLAUDE_KEYS]
                if key in responses:
                    timestamp, seen = responses[key]
                    counts = [max(a, b) for a, b in zip(seen, counts)]
                else:
                    timestamp = entry.get('timestamp')
                responses[key] = (timestamp, counts)
    for timestamp, counts in responses.values():
        tally.add(timestamp, 'claude', counts)


# --- Codex -------------------------------------------------------------------

CODEX_KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens')


def codex_usage(usage):
    return tuple(num((usage if isinstance(usage, dict) else {}).get(k)) for k in CODEX_KEYS)


def codex_counts(usage):
    # Codex's input_tokens includes the cached ones.
    total_input, cache_read, cache_write, output = usage
    return [total_input - cache_read - cache_write, output, cache_read, cache_write]


def read_codex_session(path):
    thread = parent = None
    records, events = [], []
    for entry in read_jsonl(path, '"session_meta"', '"token_count"', '"token_usage_record"'):
        kind, payload = entry.get('type'), entry.get('payload')
        if not isinstance(payload, dict):
            continue
        if kind == 'session_meta' and thread is None:
            thread, parent = payload.get('id'), payload.get('forked_from_id')
        elif kind == 'token_usage_record':
            records.append((entry.get('timestamp'), payload.get('thread_id'),
                            payload.get('response_id'), codex_usage(payload.get('usage'))))
        elif kind == 'event_msg' and payload.get('type') == 'token_count' and isinstance(payload.get('info'), dict):
            info = payload['info']
            events.append((entry.get('timestamp'), codex_usage(info.get('total_token_usage')),
                           codex_usage(info.get('last_token_usage'))))
    return thread, parent, records, events


def collect_codex(tally):
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    paths = list((home / 'sessions').rglob('rollout-*.jsonl')) + list((home / 'archived_sessions').glob('rollout-*.jsonl'))
    # Rollout file names start with the session's start time, so parents come before forks.
    sessions = [read_codex_session(path) for path in sorted(paths, key=lambda p: p.name)]
    events_by_thread = {}
    for thread, _, _, events in sessions:
        events_by_thread.setdefault(thread, set()).update((total, last) for _, total, last in events)

    responses = set()
    for thread, parent, records, events in sessions:
        if records:
            # Codex 0.153+ writes one record per model response.
            for timestamp, owner, response, usage in records:
                if (owner and thread and owner != thread) or (response and response in responses):
                    continue
                responses.add(response)
                tally.add(timestamp, 'codex', codex_counts(usage))
            continue
        # Older sessions only have token_count events carrying a running total.
        # Diffing that total skips repeated events and still counts responses
        # that got no event of their own. A fork starts by replaying its parent's
        # events, which are counted from the parent's own file.
        replayed = events_by_thread.get(parent, set()) if parent else set()
        seen, previous = set(), None
        for timestamp, total, last in events:
            if (total, last) in seen:
                continue
            seen.add((total, last))
            if (total, last) not in replayed:
                grew = previous is not None and all(a >= b for a, b in zip(total, previous))
                tally.add(timestamp, 'codex', codex_counts(tuple(a - b for a, b in zip(total, previous)) if grew else last))
            previous = total


# --- Storage -----------------------------------------------------------------

def local_timezone():
    if os.environ.get('TZ'):
        return os.environ['TZ']
    try:
        return os.readlink('/etc/localtime').split('zoneinfo/', 1)[1]
    except (OSError, IndexError):
        return 'UTC'


def load_data():
    try:
        return json.loads(DATA.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def merge(stored, scanned, rebuild):
    days = {}
    for day, sources in stored.items():
        kept = {source: counts for source, counts in sources.items() if source in SOURCES}
        if kept:
            days[day] = kept
    for day, sources in scanned.items():
        for source, counts in sources.items():
            old = days.get(day, {}).get(source)
            if rebuild or old is None or sum(counts) > sum(old):
                days.setdefault(day, {})[source] = counts
    return days


def dump_data(timezone, updated, days):
    # One day per line keeps the daily diffs small.
    rows = [f'    {json.dumps(day)}: {json.dumps(dict(sorted(days[day].items())))}' for day in sorted(days)]
    return '\n'.join([
        '{',
        f'  "timezone": {json.dumps(timezone)},',
        f'  "updated": {json.dumps(updated)},',
        f'  "sources": {json.dumps(SOURCES)},',
        f'  "fields": {json.dumps(FIELDS)},',
        '  "days": {',
        ',\n'.join(rows),
        '  }',
        '}',
    ]) + '\n'


def write_if_changed(path, text):
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return True


# --- Heatmap -----------------------------------------------------------------
# Same layout and levels as src/js/token-heatmap.js: one calendar per source,
# 53 Sunday-first weeks ending on the update day, with levels split at the
# quartiles of that source's active days.

WEEKS, CELL, GAP = 53, 10, 3
PITCH = CELL + GAP
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
# One-hue ramps from GitHub's Primer scales (light: steps 3/4/6/8, dark: 7/5/3/1),
# checked for steadily changing lightness, visibly distinct steps, and a first
# level that stands out from the page (>= 2:1 contrast).
RAMPS = {
    'light': {'claude': ('#ff8182', '#fa4549', '#a40e26', '#660018'),
              'codex': ('#54aeff', '#218bff', '#0550ae', '#0a3069')},
    'dark': {'claude': ('#8e1519', '#da3633', '#ff7b72', '#ffc1ba'),
             'codex': ('#0d419d', '#1f6feb', '#58a6ff', '#a5d6ff')},
}
THEMES = {
    'light': {'empty': '#eff2f5', 'text': '#1f2328', 'muted': '#59636e', 'border': '#d1d9e0', 'outline': '#1f2328'},
    'dark': {'empty': '#151b23', 'text': '#f0f6fc', 'muted': '#9198a1', 'border': '#3d444d', 'outline': '#f0f6fc'},
}


def compact(n):
    for size, unit in ((1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        value = float(f'{n / size:.3g}')
        if value >= 1:
            return f'{value:g}{unit}'
    return str(n)


def quartiles(totals):
    active = sorted(t for t in totals if t > 0)
    return [active[int(q * (len(active) - 1))] for q in (0.25, 0.5, 0.75)] if active else []


def level(total, cuts):
    return 0 if total <= 0 else 1 + sum(total > cut for cut in cuts)


def month_labels(start):
    labels = []
    for week in range(WEEKS):
        first = start + timedelta(weeks=week)
        if week == 0 or first.month != (first - timedelta(weeks=1)).month:
            labels.append((week, MONTHS[first.month - 1]))
    # Drop labels that would collide: a partial first month, or one in the last column.
    if len(labels) > 1 and labels[1][0] - labels[0][0] < 2:
        labels.pop(0)
    return [(week, name) for week, name in labels if week < WEEKS - 1]


def cell(x, y, fill):
    return f'<rect class="day" x="{x + 0.5}" y="{y + 0.5}" width="{CELL - 1}" height="{CELL - 1}" rx="2" fill="{fill}"/>'


def render_svg(days, end, theme):
    colors = THEMES[theme]
    start = end - timedelta(days=(end.weekday() + 1) % 7 + (WEEKS - 1) * 7)
    window = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]

    # Each panel: a headline above a box holding month labels, the grid and a legend.
    pad, left, top, head, gap = 16, 32, 20, 30, 20
    width = 2 * pad + left + WEEKS * PITCH - GAP
    panel = head + 2 * pad + top + 7 * PITCH - GAP + 24
    height = len(SOURCES) * (panel + gap) - gap
    titles, body = [], []
    for n, (source, name) in enumerate(SOURCES.items()):
        totals = [sum(days.get(day, {}).get(source, [])) for day in window]
        cuts, fills = quartiles(totals), (colors['empty'],) + RAMPS[theme][source]
        summary = f'{compact(sum(totals))} tokens in the last year' if sum(totals) else 'No tokens in the last year'
        titles.append(f'{name}: {summary}')

        box_y = n * (panel + gap) + head
        grid_x, grid_y = pad + left, box_y + pad + top
        foot_y = grid_y + 7 * PITCH - GAP + 24
        body.append(f'<text class="head" x="0" y="{box_y - 12}">'
                    f'<tspan font-weight="600">{escape(name)}</tspan> · {summary}</text>')
        body.append(f'<rect x="0.5" y="{box_y + 0.5}" width="{width - 1}" height="{panel - head - 1}" '
                    f'rx="6" fill="none" stroke="{colors["border"]}"/>')
        for week, label in month_labels(start):
            body.append(f'<text x="{grid_x + week * PITCH}" y="{grid_y - 8}">{label}</text>')
        for row, label in ((1, 'Mon'), (3, 'Wed'), (5, 'Fri')):
            body.append(f'<text x="{pad}" y="{grid_y + row * PITCH + 9}">{label}</text>')
        body += [cell(grid_x + i // 7 * PITCH, grid_y + i % 7 * PITCH, fills[level(total, cuts)])
                 for i, total in enumerate(totals)]

        if n == len(SOURCES) - 1:
            body.append(f'<text x="{pad}" y="{foot_y}">Updated {MONTHS[end.month - 1]} {end.day}, {end.year}</text>')
        more_x = width - pad
        legend_x = more_x - 36 - (5 * PITCH - GAP)
        body.append(f'<text x="{legend_x - 6}" y="{foot_y}" text-anchor="end">Less</text>')
        body += [cell(legend_x + i * PITCH, foot_y - 10, fill) for i, fill in enumerate(fills)]
        body.append(f'<text x="{more_x}" y="{foot_y}" text-anchor="end">More</text>')

    title = escape('; '.join(titles))
    return '\n'.join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
        f'<title>{title}</title>',
        f'<style>text {{ font: 12px {FONT}; fill: {colors["muted"]}; }} '
        f'.head {{ font-size: 16px; fill: {colors["text"]}; }} '
        f'rect.day {{ stroke: {colors["outline"]}; stroke-opacity: 0.05; }}</style>',
        *body,
        '</svg>',
    ]) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--rebuild', action='store_true',
                        help='replace stored days with the fresh scan even when it is smaller')
    parser.add_argument('--tz', help='IANA time zone for day boundaries (default: the stored or system zone)')
    args = parser.parse_args()

    stored = load_data()
    timezone = args.tz or stored.get('timezone') or local_timezone()
    tally = Tally(ZoneInfo(timezone))
    collect_claude(tally)
    collect_codex(tally)

    days = merge(stored.get('days', {}), tally.days, args.rebuild)
    today = datetime.now(ZoneInfo(timezone)).date()
    changed = write_if_changed(DATA, dump_data(timezone, today.isoformat(), days))
    for theme, path in SVGS.items():
        changed |= write_if_changed(path, render_svg(days, today, theme))

    scanned = {s: sum(sum(c.get(s, [])) for c in tally.days.values()) for s in SOURCES}
    print('Scanned ' + ', '.join(f'{SOURCES[s]} {compact(n)}' for s, n in scanned.items())
          + f' tokens; {len(days)} days stored ({timezone}); ' + ('files updated.' if changed else 'no changes.'))


if __name__ == '__main__':
    main()
