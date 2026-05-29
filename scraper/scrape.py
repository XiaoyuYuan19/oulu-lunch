"""抓 Juvenes 学生餐厅菜单（Jamix Cloud JSON API）+ Gemini 翻译 → public/data.json。"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "scraper" / "restaurants.yaml"
OUTPUT = ROOT / "docs" / "data.json"

JAMIX_API = "https://fi.jamix.cloud/apps/menuservice/rest/haku/menu/{cust}/{kitchen}?lang=fi"
HEADERS = {"User-Agent": "oulu-lunch-pwa/1.0 python-requests"}
TZ_HELSINKI = ZoneInfo("Europe/Helsinki")

EMOJI_RULES: list[tuple[str, str]] = [
    (r"\b(lohi|kala|silakka|seiti|tonnikala|kuha|made|muikku|katkarapu)", "🐟"),
    (r"\b(kana|broileri|kalkkuna)", "🍗"),
    (r"\b(nauta|härkä|jauheliha|naudan|paahtopaisti|härän)", "🥩"),
    (r"\b(sika|possu|kassler|pekoni|porsaan)", "🥓"),
    (r"\b(makkara|nakki)", "🌭"),
    (r"\b(keitto|sose)", "🍲"),
    (r"\b(pasta|spagetti|lasagne|makaroni|tortellini|gnocchi)", "🍝"),
    (r"\b(pizza)", "🍕"),
    (r"\b(riisi|risotto)", "🍚"),
    (r"\b(peruna|perunamuusi|perunoita|lohko)", "🥔"),
    (r"\b(pyörykä|pyörykät|lihapulla)", "🍡"),
    (r"\b(curry|tikka|masala)", "🍛"),
    (r"\b(wok|nuudeli|nuudelit|noodle|ramen)", "🍜"),
    (r"\b(salaatti|salad)", "🥗"),
    (r"\b(kasvis|vegaani|vegetaarinen|vege|kasvi|tofu|seitan)", "🌱"),
    (r"\b(juusto)", "🧀"),
    (r"\b(muna|munakas|kananmuna)", "🥚"),
    (r"\b(leipä|sämpylä|patonki)", "🍞"),
    (r"\b(pannukakku|lettu)", "🥞"),
    (r"\b(jälkiruoka|kakku|piirakka|pulla|munkki)", "🍰"),
    (r"\b(hilloa|hillo|marmeladi)", "🍓"),
]

DIET_TAGS = {
    "L": "无乳糖", "VL": "极低乳糖", "G": "无麸质", "M": "无奶", "Mu": "无蛋",
    "VEG": "素", "VE": "全素", "K": "本地", "SIS.LUOMUA": "含有机", "*": "心脏",
}

CATEGORY_MAP = {
    "KASVISLOUNAS": "素菜",
    "KEITTOLOUNAS": "汤",
    "KOTIRUOKA": "家常菜",
    "KEVYTLOUNAS": "轻食",
    "PÄIVÄN LOUNAS": "今日特餐",
    "PÄIVÄN ATERIA": "今日套餐",
    "LIHAINEN LOUNAS": "肉菜",
    "LOUNAS": "午餐",
    "KALARUOKA": "鱼",
    "PUUROLOUNAS": "粥",
    "JÄLKIRUOKA": "甜点",
    "SALAATTI": "沙拉",
    "LISÄKE": "配菜",
    "LEIPÄ": "面包",
    "DESSERT": "甜点",
    "FUSION": "融合料理",
    "SALAD AND SOUP": "汤/沙拉",
    "SALAD": "沙拉",
    "SOUP": "汤",
    "MAIN": "主菜",
    "MAIN COURSE": "主菜",
    "BREAKFAST": "早餐",
    "VEGETARIAN": "素菜",
    "VEGAN": "全素",
}


def emoji_for(fi: str) -> str:
    s = fi.lower()
    for pattern, e in EMOJI_RULES:
        if re.search(pattern, s):
            return e
    return "🍽️"


def parse_diets(s: str | None) -> list[str]:
    if not s:
        return []
    codes = [c.strip() for c in re.split(r"[,/]", s) if c.strip()]
    return [DIET_TAGS[c] for c in codes if c in DIET_TAGS]


def map_category(fi: str | None) -> str:
    if not fi:
        return ""
    up = fi.strip().upper()
    return CATEGORY_MAP.get(up, fi.strip().title())


def today_yyyymmdd() -> int:
    now = datetime.now(TZ_HELSINKI)
    return now.year * 10000 + now.month * 100 + now.day


INLINE_DIET_RE = re.compile(
    r"[\s,]*(?:\b(?:G|M|L|VL|VE|VEG|Mu|K)\b[\s,/]*)+\*?\s*$",
    re.IGNORECASE,
)


def clean_name(name: str) -> str:
    name = INLINE_DIET_RE.sub("", name).strip()
    name = re.sub(r"\s+", " ", name).strip(" ,*")
    return name


def fetch_dishes(customer_id: int, kitchen_id: int, today: int) -> list[dict]:
    url = JAMIX_API.format(cust=customer_id, kitchen=kitchen_id)
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()

    items: list[dict] = []
    seen: set[str] = set()
    for kitchen in data:
        for mt in kitchen.get("menuTypes", []):
            for menu in mt.get("menus", []):
                for day in menu.get("days", []):
                    if day.get("date") != today:
                        continue
                    for opt in day.get("mealoptions", []):
                        category = map_category(opt.get("name"))
                        for mi in opt.get("menuItems", []):
                            raw = (mi.get("name") or "").strip()
                            name = clean_name(raw)
                            if not name or name in seen:
                                continue
                            seen.add(name)
                            items.append({
                                "fi": name,
                                "category": category,
                                "tags": parse_diets(mi.get("diets")),
                            })
    return items


GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]


def translate_batch(fi_names: list[str]) -> dict[str, str]:
    if not fi_names:
        return {}
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("warn: GEMINI_API_KEY 未设置，跳过翻译", file=sys.stderr)
        return {}

    try:
        from google import genai
    except Exception as e:
        print(f"warn: google-genai import 失败: {e!r}", file=sys.stderr)
        return {}

    client = genai.Client(api_key=api_key)
    numbered = "\n".join(f"{i+1}. {n}" for i, n in enumerate(fi_names))
    prompt = (
        "把下列芬兰菜名翻译成简短自然的中文。要求：\n"
        "- 每行一个，顺序与编号一致\n"
        "- 只输出中文，不要编号、不要解释、不要原文\n"
        "- 看不懂的词直接音译或描述\n\n"
        f"{numbered}"
    )

    text = ""
    last_err: Exception | None = None
    for model in GEMINI_MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            text = (resp.text or "").strip()
            if text:
                print(f"  翻译用模型: {model}", file=sys.stderr)
                break
        except Exception as e:
            last_err = e
            print(f"  模型 {model} 失败: {e!r}", file=sys.stderr)

    if not text:
        print(f"warn: 所有 Gemini 模型失败，跳过翻译。最后错误: {last_err!r}", file=sys.stderr)
        return {}

    raw_lines = [l.strip() for l in text.splitlines() if l.strip()]
    zh_lines = [re.sub(r"^\s*\d+[.．、)、:：]+\s*", "", l).strip() for l in raw_lines]
    return {fi: (zh_lines[i] if i < len(zh_lines) else fi) for i, fi in enumerate(fi_names)}


def main() -> int:
    with open(CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    customer_id = cfg["customer_id"]
    today = today_yyyymmdd()
    print(f"date={today}", file=sys.stderr)

    per_restaurant: list[tuple[dict, list[dict]]] = []
    for r in cfg["restaurants"]:
        print(f"→ {r['name']} (k={r['kitchen_id']})", file=sys.stderr)
        try:
            dishes = fetch_dishes(customer_id, r["kitchen_id"], today)
            print(f"   {len(dishes)} 道菜", file=sys.stderr)
        except Exception as e:
            print(f"   fetch failed: {e}", file=sys.stderr)
            dishes = []
        per_restaurant.append((r, dishes))

    all_names: list[str] = []
    seen: set[str] = set()
    for _, dishes in per_restaurant:
        for d in dishes:
            if d["fi"] not in seen:
                seen.add(d["fi"])
                all_names.append(d["fi"])

    print(f"翻译 {len(all_names)} 道菜…", file=sys.stderr)
    zh_map = translate_batch(all_names)

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today,
        "restaurants": [],
    }
    for r, dishes in per_restaurant:
        items = [
            {
                "fi": d["fi"],
                "zh": zh_map.get(d["fi"], d["fi"]),
                "emoji": emoji_for(d["fi"]),
                "category": d["category"],
                "tags": d["tags"],
            }
            for d in dishes
        ]
        output["restaurants"].append({
            "name": r["name"],
            "hours": r.get("hours", ""),
            "price": r.get("price", ""),
            "location": r.get("location", ""),
            "kitchen_id": r["kitchen_id"],
            "dishes": items,
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✓ wrote {OUTPUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
