"""每天抓 lounaat.info 上 Oulu 学生餐厅菜单 + Claude Haiku 翻译 → 写 public/data.json。"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "scraper" / "restaurants.yaml"
OUTPUT = ROOT / "public" / "data.json"

HEADERS = {
    "User-Agent": "oulu-lunch-pwa/1.0 (+https://github.com/) python-requests",
    "Accept-Language": "fi,en;q=0.8",
}

EMOJI_RULES: list[tuple[str, str]] = [
    (r"\b(lohi|kala|silakka|seiti|tonnikala|kuha|made|muikku)", "🐟"),
    (r"\b(kana|broileri|kalkkuna)", "🍗"),
    (r"\b(nauta|härkä|jauheliha|naudan|paahtopaisti)", "🥩"),
    (r"\b(sika|possu|kassler|pekoni|porsaan)", "🥓"),
    (r"\b(makkara)", "🌭"),
    (r"\b(keitto|sose)", "🍲"),
    (r"\b(pasta|spagetti|lasagne|makaroni)", "🍝"),
    (r"\b(pizza)", "🍕"),
    (r"\b(riisi)", "🍚"),
    (r"\b(peruna|perunamuusi|perunoita)", "🥔"),
    (r"\b(pyörykä|pyörykät|lihapulla)", "🍡"),
    (r"\b(curry)", "🍛"),
    (r"\b(wok|nuudeli|nuudelit|noodle)", "🍜"),
    (r"\b(salaatti)", "🥗"),
    (r"\b(kasvis|vegaani|vegetaarinen|vege|kasvi)", "🌱"),
    (r"\b(juusto)", "🧀"),
    (r"\b(muna|munakas|kananmuna)", "🥚"),
    (r"\b(leipä|sämpylä)", "🍞"),
    (r"\b(jälkiruoka|kakku|piirakka)", "🍰"),
]

DIET_TAGS = {
    "L": "无乳糖", "VL": "极低乳糖", "G": "无麸质", "M": "无奶",
    "VEG": "素", "VE": "全素", "K": "本地", "*": "心脏标志",
}

DIET_CODE_RE = re.compile(r"\(([A-Z, *\.]+)\)\s*$")
PRICE_LEAD_RE = re.compile(r"^\s*\d+[,.]\d{2}\s*€?\s*")


def emoji_for(fi: str) -> str:
    s = fi.lower()
    for pattern, e in EMOJI_RULES:
        if re.search(pattern, s):
            return e
    return "🍽️"


def parse_dish_line(raw: str) -> tuple[str, list[str]]:
    """从一行原文里抠出菜名 + 饮食标签。"""
    text = re.sub(r"\s+", " ", raw).strip()
    text = PRICE_LEAD_RE.sub("", text)
    tags: list[str] = []
    m = DIET_CODE_RE.search(text)
    if m:
        for code in re.split(r"[, .]+", m.group(1)):
            code = code.strip()
            if code in DIET_TAGS:
                tags.append(DIET_TAGS[code])
        text = DIET_CODE_RE.sub("", text).strip()
    return text, tags


def fetch_menu(url: str) -> list[tuple[str, list[str]]]:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # 找今天的菜单块
    today_block = None
    for sel in [".menu.today", ".today .menu", "article .today", ".day.today", ".menu"]:
        today_block = soup.select_one(sel)
        if today_block:
            break
    if not today_block:
        return []

    items: list[tuple[str, list[str]]] = []
    candidates = today_block.select("li.dish") or today_block.select("li") or today_block.select(".meal, .menu-item")
    for it in candidates:
        if it.find("ul"):
            continue
        text = it.get_text(" ", strip=True)
        if not text:
            continue
        name, tags = parse_dish_line(text)
        if len(name) < 4 or name[0].isdigit():
            continue
        if any(name == n for n, _ in items):
            continue
        items.append((name, tags))
        if len(items) >= 12:  # 防御性截断
            break
    return items


def translate_batch(fi_names: list[str]) -> dict[str, str]:
    if not fi_names:
        return {}
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("warn: GEMINI_API_KEY 未设置，跳过翻译", file=sys.stderr)
        return {}

    from google import genai
    client = genai.Client(api_key=api_key)

    numbered = "\n".join(f"{i+1}. {n}" for i, n in enumerate(fi_names))
    prompt = (
        "把下列芬兰菜名翻译成简短自然的中文。要求：\n"
        "- 每行一个，顺序与编号一致\n"
        "- 只输出中文，不要编号、不要解释、不要原文\n"
        "- 看不懂的词直接音译或描述\n\n"
        f"{numbered}"
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    text = (resp.text or "").strip()
    zh_lines = [l.strip() for l in text.splitlines() if l.strip()]
    # 容错：行数对不上时尽量按位置对齐
    return {fi: zh_lines[i] if i < len(zh_lines) else fi for i, fi in enumerate(fi_names)}


def main() -> int:
    with open(CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    per_restaurant: list[tuple[dict, list[tuple[str, list[str]]]]] = []
    for r in cfg["restaurants"]:
        print(f"→ {r['name']} ({r['url']})", file=sys.stderr)
        try:
            dishes = fetch_menu(r["url"])
            print(f"   {len(dishes)} 道菜", file=sys.stderr)
        except Exception as e:
            print(f"   fetch failed: {e}", file=sys.stderr)
            dishes = []
        per_restaurant.append((r, dishes))

    all_names: list[str] = []
    seen: set[str] = set()
    for _, dishes in per_restaurant:
        for name, _ in dishes:
            if name not in seen:
                seen.add(name)
                all_names.append(name)

    print(f"翻译 {len(all_names)} 道菜…", file=sys.stderr)
    zh_map = translate_batch(all_names)

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "restaurants": [],
    }
    for r, dishes in per_restaurant:
        items = [
            {
                "fi": name,
                "zh": zh_map.get(name, name),
                "emoji": emoji_for(name),
                "tags": tags,
            }
            for name, tags in dishes
        ]
        output["restaurants"].append({
            "name": r["name"],
            "hours": r.get("hours", ""),
            "price": r.get("price", ""),
            "location": r.get("location", ""),
            "url": r.get("url", ""),
            "dishes": items,
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✓ wrote {OUTPUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
