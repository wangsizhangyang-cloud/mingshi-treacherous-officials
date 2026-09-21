#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract the names of all treacherous officials recorded in the Mingshi (明史).

The Mingshi (History of the Ming, compiled 1739 under 张廷玉) devotes its
juan 308 (列传第一百九十六, 明史卷三百八) to the Biographies of Treacherous
Officials (奸臣传). This script downloads that juan from Chinese Wikisource,
parses the biographical entries -- the seven main biographies plus every
attached biography (附传) -- and writes the roster to
``treacherous_officials.txt`` and ``treacherous_officials.html``.

Usage:
    python extract_treacherous_officials.py
    python extract_treacherous_officials.py --source cached_wikitext.txt
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://zh.wikisource.org/w/api.php"
PAGE_TITLE = "明史/卷308"
PAGE_URL = "https://zh.wikisource.org/wiki/明史/卷308"
USER_AGENT = (
    "MingshiJianchenExtractor/1.0 "
    "(text-mining study; https://github.com/wangsizhangyang-cloud)"
)

OUT_DIR = Path(__file__).resolve().parent
TXT_PATH = OUT_DIR / "treacherous_officials.txt"
HTML_PATH = OUT_DIR / "treacherous_officials.html"

CJK = r"\u4e00-\u9fff"
KINSHIP_PREFIXES = set("子孫弟兄弟侄婿奴僕客父")

NAME_COMMA = re.compile(rf"^([{CJK}]{{1,4}})，")
NAME_SHORT = re.compile(rf"^([{CJK}])(?=[{CJK}])")
FALLBACK_STOPCHARS = set("時初先已會尋久俄頃至")

PERIODS = {
    "胡惟庸": "洪武", "陳寧": "洪武",
    "陳瑛": "永樂", "馬麟": "永樂", "丁玨": "永樂",
    "秦政學": "永樂", "趙緯": "永樂", "李芳": "永樂",
    "嚴嵩": "嘉靖", "嚴世蕃": "嘉靖", "趙文華": "嘉靖", "鄢懋卿": "嘉靖",
    "周延儒": "崇禎", "溫體仁": "崇禎",
    "馬士英": "弘光（南明）", "阮大鋮": "弘光（南明）",
}

SIMPLIFIED = {
    "陳寧": "陈宁", "陳瑛": "陈瑛", "馬麟": "马麟", "丁玨": "丁珏",
    "秦政學": "秦政学", "趙緯": "赵纬", "嚴嵩": "严嵩", "嚴世蕃": "严世蕃",
    "趙文華": "赵文华", "溫體仁": "温体仁", "馬士英": "马士英", "阮大鋮": "阮大铖",
}


@dataclass
class Official:
    name: str
    kind: str
    attached_to: str | None
    opening: str
    order: float

    @property
    def kind_label(self) -> str:
        return "主传" if self.kind == "main" else "附传"


def fetch_wikitext(title: str) -> str:
    params = {
        "action": "parse",
        "page": title,
        "prop": "wikitext",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    if "error" in data:
        raise RuntimeError(f"API error: {data['error'].get('info', data['error'])}")
    return data["parse"]["wikitext"]


def parse_roster(raw: str) -> tuple[str, list[str], list[str]]:
    m = re.search(r"奸臣：(.+?)'''", raw, re.S)
    if not m:
        return "", [], []
    line = re.sub(r"\{\{\*\|([^{}]*)\}\}", r"\1", m.group(1))
    line = re.sub(r"<[^<>]+>", "", line)
    mains, attached = [], []
    for token in line.split():
        am = re.match(r"(.+?)附[:：](.+?)等?$", token)
        if am:
            mains.append(am.group(1))
            attached.append(am.group(2))
            continue
        pm = re.match(r"(.+?)[（(]([^（）()]+)[）)]$", token)
        if pm:
            mains.append(pm.group(1))
            attached.append(pm.group(2))
            continue
        mains.append(token)
    return line.strip(), mains, attached


_TEMPLATE = re.compile(r"\{\{([^{}]*)\}\}")
_WIKILINK = re.compile(r"\[\[(?:[^\[\]|]*\|)?([^\[\]|]*)\]\]")


def _expand_template(m: re.Match) -> str:
    inner = m.group(1)
    return inner.split("|")[-1] if "|" in inner else ""


def clean_wikitext(raw: str) -> str:
    text = raw.replace("\r\n", "\n")
    while True:
        new = _TEMPLATE.sub(_expand_template, text)
        if new == text:
            break
        text = new
    text = _WIKILINK.sub(r"\1", text)
    text = re.sub(r"<[^<>\n]+>", "", text)
    return text.replace("'''", "").replace("''", "")


_HEADING = re.compile(r"(?m)^(={2,3})([^=\n]+?)\1[ \t]*$")


def split_sections(text: str) -> list[dict]:
    heads = list(_HEADING.finditer(text))
    sections = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        sections.append({
            "level": len(m.group(1)),
            "title": m.group(2).strip(),
            "start": m.start(),
            "body_start": m.end(),
            "body": text[m.end():end],
        })
    return sections


def split_heading_title(title: str) -> tuple[str, list[str]]:
    title = title.strip()
    attached: list[str] = []
    m = re.search(r"[（(]([^（）()]+)[）)]", title)
    if m:
        attached = [a for a in re.split(r"[、，,\s]+", m.group(1)) if a]
        title = (title[:m.start()] + title[m.end():]).strip()
    title = re.sub(r"等\s*$", "", title).strip()
    return title, attached


def iter_paragraphs(body: str, base: int):
    for m in re.finditer(r"(?:[^\n]+\n?)+", body):
        block = m.group(0)
        stripped = block.strip(" 　\t\r")
        if not stripped:
            continue
        lead = len(block) - len(block.lstrip(" 　\t\r"))
        yield stripped, base + m.start() + lead


def resolve_name(short: str, scope: str, surname: str | None = None) -> str | None:
    counts: dict[str, int] = {}
    for m in re.finditer(rf"([{CJK}])(?={re.escape(short)})", scope):
        pfx = m.group(1)
        if pfx in KINSHIP_PREFIXES:
            continue
        full = pfx + short
        counts[full] = counts.get(full, 0) + 1
    if not counts:
        return None
    if surname:
        for cand in sorted(counts, key=lambda c: (-counts[c], len(c))):
            if cand.startswith(surname):
                return cand
    return max(counts, key=lambda c: (counts[c], -len(c)))


def enumerated_in(name: str, scope: str) -> bool:
    return bool(re.search(rf"(?:[、如：]|^){re.escape(name)}", scope))


def first_sentence(para: str, limit: int = 60) -> str:
    sentence = para.split("。", 1)[0]
    if len(sentence) > limit:
        sentence = sentence[:limit] + "……"
    return sentence


def criteria_quote(preamble: str, limit: int = 160) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=。)", preamble) if s.strip()]
    for key in ("始加以惡名", "奸臣傳》", "奸臣传》"):
        for s in sentences:
            if key in s:
                return s[:limit] + ("……" if len(s) > limit else "")
    return first_sentence(preamble, limit)


def bio_opening(name: str, paras: list[tuple[str, int]]) -> str:
    for p, _ in paras:
        if p.startswith(name) or p.startswith(name[1:]):
            return first_sentence(p)
    return ""


def parse_juan(raw: str):
    roster_line, roster_mains, roster_attached = parse_roster(raw)
    text = clean_wikitext(raw)
    sections = split_sections(text)
    if not sections:
        raise RuntimeError("no section headings found; the page layout may have changed")

    l2_idx = [i for i, s in enumerate(sections) if s["level"] == 2]
    scopes: dict[int, str] = {}
    for n, i in enumerate(l2_idx):
        end = sections[l2_idx[n + 1]]["start"] if n + 1 < len(l2_idx) else len(text)
        scopes[i] = text[sections[i]["start"]:end]

    records: list[tuple[float, Official]] = []
    pre_paras = [p for p, _ in iter_paragraphs(text[:sections[0]["start"]], 0)
                 if not re.match(r"^__\w+__$", p) and "奸臣：" not in p and "notes" not in p]
    preamble = pre_paras[-1] if pre_paras else ""

    current_l2 = None
    for i, sec in enumerate(sections):
        title, paren_attached = split_heading_title(sec["title"])
        paras = list(iter_paragraphs(sec["body"], sec["body_start"]))

        if sec["level"] == 2:
            current_l2 = i
            records.append((sec["start"],
                            Official(title, "main", None, bio_opening(title, paras), sec["start"])))
            for a in paren_attached:
                records.append((sec["start"] + 0.5,
                                Official(a, "attached", title, bio_opening(a, paras), sec["start"] + 0.5)))
            named = [(p, o, NAME_COMMA.match(p).group(1))
                     for p, o in paras if NAME_COMMA.match(p)]
            if named and len(named) > 1 and named[-1][1] == paras[-1][1]:
                tok = named[-1][2]
                if len(tok) <= 3 and tok != title:
                    full = resolve_name(tok, scopes[current_l2], surname=title[:1])
                    if full and enumerated_in(full, scopes[current_l2]):
                        records.append((named[-1][1],
                                        Official(full, "attached", title,
                                                 first_sentence(named[-1][0]), named[-1][1])))
        else:
            scope = scopes.get(current_l2, "")
            produced: set[str] = set()
            for p, o in paras:
                cm = NAME_COMMA.match(p)
                if cm:
                    tok = cm.group(1)
                    if len(tok) >= 3:
                        name = tok
                    elif tok == title:
                        name = title
                    else:
                        name = resolve_name(tok, scope, surname=title[:1])
                        if name and not enumerated_in(name, scope):
                            name = None
                else:
                    sm = NAME_SHORT.match(p)
                    name = None
                    if sm and sm.group(1) not in FALLBACK_STOPCHARS:
                        cand = resolve_name(sm.group(1), scope, surname=title[:1])
                        if cand and enumerated_in(cand, scope):
                            name = cand
                if not name:
                    continue
                produced.add(name)
                records.append((o, Official(name, "attached",
                                            sections[current_l2]["title"] if current_l2 is not None else None,
                                            first_sentence(p), o)))
            if title and title not in produced:
                records.append((sec["start"], Official(title, "attached",
                                                       sections[current_l2]["title"] if current_l2 is not None else None,
                                                       bio_opening(title, paras), sec["start"])))

    records.sort(key=lambda r: r[0])
    officials = [off for _, off in records]
    return officials, roster_line, roster_mains, roster_attached, preamble


def validate(officials, roster_mains, roster_attached) -> list[str]:
    notes = []
    names = {o.name for o in officials}
    main_names = {o.name for o in officials if o.kind == "main"}
    missing_mains = [n for n in roster_mains if n not in main_names]
    missing_attached = [n for n in roster_attached if n not in names]
    if missing_mains:
        notes.append(f"警告：卷首传目中的主传人物未被解析到：{'、'.join(missing_mains)}")
    if missing_attached:
        notes.append(f"警告：卷首传目中的附传人物未被解析到：{'、'.join(missing_attached)}")
    if not notes:
        notes.append("校验通过：解析结果完全覆盖卷首传目所列人物。")
    unknown = [o.name for o in officials if o.name not in PERIODS]
    if unknown:
        notes.append(f"警告：以下人物不在已知奸臣传名单中，可能为解析误报：{'、'.join(unknown)}")
    return notes


def build_txt(officials, roster_line, notes, preamble, fetched_at) -> str:
    mains = [o for o in officials if o.kind == "main"]
    attached = [o for o in officials if o.kind == "attached"]
    lines = [
        "=" * 64,
        "《明史·奸臣传》人物名录",
        "Treacherous Officials Recorded in the Mingshi (History of the Ming)",
        "=" * 64,
        "",
        f"资料来源：维基文库《明史》卷三百八·列传第一百九十六《奸臣传》",
        f"          {PAGE_URL}",
        f"抓取时间：{fetched_at}",
        f"传主统计：共 {len(officials)} 人（独立立传 {len(mains)} 人，附传 {len(attached)} 人）",
        "",
        "卷首传目（原书目录）：",
        f"    {roster_line}",
        "",
        "收传标准（据本卷序言）：",
        f"    「{criteria_quote(preamble)}」",
        "",
        "一、独立立传（主传）",
        "-" * 64,
    ]
    for n, o in enumerate(mains, 1):
        era = PERIODS.get(o.name, "")
        lines.append(f" {n:>2}. {o.name}{'（' + SIMPLIFIED[o.name] + '）' if o.name in SIMPLIFIED else ''}〔{era}〕")
        lines.append(f"     小传：{o.opening or '（见本卷正文）'}")
    lines += ["", "二、附传（附于他人传后）", "-" * 64]
    for n, o in enumerate(attached, len(mains) + 1):
        era = PERIODS.get(o.name, "")
        simp = f"（{SIMPLIFIED[o.name]}）" if o.name in SIMPLIFIED else ""
        lines.append(f" {n:>2}. {o.name}{simp}〔{era}〕 — 附于《{o.attached_to}传》")
        lines.append(f"     小传：{o.opening or '（见本卷正文）'}")
    lines += ["", "三、校验结果", "-" * 64]
    lines += [f"    {n}" for n in notes]
    lines += [
        "",
        "说明：",
        "    1. 名单取自《明史》奸臣传（卷三百八）中立有传文的人物，含附传；",
        "       传文起首句保留原文（繁体）。附传人物依史家惯例缀于主传之后，",
        "       亦属「奸臣传」所录人物。",
        "    2. 名单由 extract_treacherous_officials.py 自动解析维基文库原文生成。",
        "",
    ]
    return "\n".join(lines)


def build_html(officials, roster_line, notes, preamble, fetched_at) -> str:
    mains = [o for o in officials if o.kind == "main"]
    attached = [o for o in officials if o.kind == "attached"]
    css = """
    :root { --ink:#2b2b2b; --accent:#8c2f2f; --line:#d9d2c5; --paper:#faf7f0; }
    * { box-sizing: border-box; }
    body { margin:0; padding:2rem 1rem; background:var(--paper); color:var(--ink);
           font-family:"Noto Serif SC","Source Han Serif SC","SimSun",serif; line-height:1.7; }
    .wrap { max-width:920px; margin:0 auto; }
    header { border-bottom:3px double var(--accent); padding-bottom:1rem; margin-bottom:1.4rem; }
    h1 { margin:0 0 .3rem; font-size:1.9rem; letter-spacing:.08em; }
    .sub { margin:0; color:#6b6257; font-size:.95rem; }
    .chips { margin-top:.8rem; }
    .chip { display:inline-block; background:var(--accent); color:#fff; border-radius:999px;
            padding:.15rem .8rem; font-size:.85rem; margin-right:.5rem; }
    blockquote { margin:0 0 1.5rem; padding:.8rem 1.1rem; border-left:4px solid var(--accent);
                 background:#fff; font-size:.95rem; color:#555; }
    table { width:100%; border-collapse:collapse; background:#fff; font-size:.95rem; }
    th, td { border:1px solid var(--line); padding:.55rem .7rem; text-align:left; vertical-align:top; }
    th { background:#efe9dc; font-weight:600; white-space:nowrap; }
    tr:nth-child(even) td { background:#fbf9f4; }
    .badge { display:inline-block; border-radius:4px; padding:.05rem .45rem; font-size:.8rem; color:#fff; }
    .main  { background:var(--accent); }
    .att   { background:#7a7264; }
    .simp  { color:#8a8071; font-size:.85em; }
    .open  { color:#5a5348; }
    footer { margin-top:1.6rem; padding-top:1rem; border-top:1px solid var(--line);
             color:#6b6257; font-size:.85rem; }
    footer a { color:var(--accent); }
    """
    rows = []
    for n, o in enumerate(officials, 1):
        simp = f'<span class="simp">（{SIMPLIFIED[o.name]}）</span>' if o.name in SIMPLIFIED else ""
        badge = (f'<span class="badge main">主传</span>' if o.kind == "main"
                 else f'<span class="badge att">附传·附于{o.attached_to}传</span>')
        rows.append(
            f"<tr><td>{n}</td>"
            f"<td><strong>{html.escape(o.name)}</strong>{simp}<br>"
            f"<span class='open'>{html.escape(PERIODS.get(o.name, ''))}</span></td>"
            f"<td>{badge}</td>"
            f"<td class='open'>{html.escape(o.opening or '—')}</td></tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>《明史·奸臣传》人物名录</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>《明史·奸臣传》人物名录</h1>
  <p class="sub">Treacherous Officials Recorded in the Mingshi (History of the Ming) · 卷三百八 · 列传第一百九十六</p>
  <div class="chips">
    <span class="chip">共 {len(officials)} 人</span>
    <span class="chip">主传 {len(mains)} 人</span>
    <span class="chip">附传 {len(attached)} 人</span>
  </div>
</header>
<blockquote>「{html.escape(criteria_quote(preamble))}」<br>—— 《明史·奸臣传》序</blockquote>
<table>
<thead><tr><th>#</th><th>姓名</th><th>类别</th><th>小传起首</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
<footer>
  <p>校验：{"；".join(html.escape(n) for n in notes)}</p>
  <p>卷首传目：{html.escape(roster_line)}</p>
  <p>数据来源：<a href="{PAGE_URL}">维基文库《明史/卷308》</a> · 抓取时间 {fetched_at} ·
     由 <code>extract_treacherous_officials.py</code> 自动解析生成</p>
</footer>
</div>
</body>
</html>
"""


def main(argv=None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Extract the treacherous officials of the Mingshi (juan 308 奸臣传).")
    ap.add_argument("--source", help="use a cached wikitext file instead of downloading from Wikisource")
    args = ap.parse_args(argv)

    if args.source:
        raw = Path(args.source).read_text(encoding="utf-8")
    else:
        print(f"Fetching {PAGE_TITLE} from Chinese Wikisource ...")
        raw = fetch_wikitext(PAGE_TITLE)

    fetched_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    officials, roster_line, roster_mains, roster_attached, preamble = parse_juan(raw)
    notes = validate(officials, roster_mains, roster_attached)

    TXT_PATH.write_text(build_txt(officials, roster_line, notes, preamble, fetched_at), encoding="utf-8")
    HTML_PATH.write_text(build_html(officials, roster_line, notes, preamble, fetched_at), encoding="utf-8")

    mains = [o.name for o in officials if o.kind == "main"]
    attached = [o.name for o in officials if o.kind == "attached"]
    print(f"Parsed {len(officials)} officials: {len(mains)} main + {len(attached)} attached")
    print("  主传: " + "、".join(mains))
    print("  附传: " + "、".join(attached))
    for n in notes:
        print(f"  {n}")
    print(f"Wrote {TXT_PATH}")
    print(f"Wrote {HTML_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
