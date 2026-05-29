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


def translate_batch(fi_names: list[str]) -> dict[str, dict[str, str]]:
    """对每个菜名返回 {'zh': 中文, 'en_search': 英文图片关键词}"""
    if not fi_names:
        return {}
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("warn: GEMINI_API_KEY 未设置，跳过翻译", file=sys.stderr)
        return {}

    try:
        from google import genai
        from google.genai import types
        from pydantic import BaseModel
    except Exception as e:
        print(f"warn: google-genai import 失败: {e!r}", file=sys.stderr)
        return {}

    class Translation(BaseModel):
        zh: str
        en_search: str

    client = genai.Client(api_key=api_key)
    numbered = "\n".join(f"{i+1}. {n}" for i, n in enumerate(fi_names))
    prompt = (
        "下面是带编号的芬兰菜名。对每一条输出一个对象：\n"
        '  zh = 简短自然的中文菜名（不带编号/原文/解释）\n'
        '  en_search = 2-3 个英文检索关键词，能在 Pexels 图库搜到一张代表该菜的图，'
        '用通用菜式描述（如 "pea soup", "meatballs in cream sauce", "pancake jam"），'
        "不要音译。\n"
        "返回 JSON 数组，长度与编号数完全一致，顺序对齐。\n\n"
        f"{numbered}"
    )

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=list[Translation],
        temperature=0.2,
    )

    text = ""
    last_err: Exception | None = None
    for model in GEMINI_MODELS:
        try:
            resp = client.models.generate_content(
                model=model, contents=prompt, config=config,
            )
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

    try:
        arr = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"warn: JSON parse 失败 ({e!r}), 头200字: {text[:200]!r}", file=sys.stderr)
        return {}

    print(f"  返回 {len(arr)} 条 / 输入 {len(fi_names)} 条", file=sys.stderr)
    if len(arr) != len(fi_names):
        print(f"  ⚠ 长度不一致，会按位置对齐, 头200字: {text[:200]!r}", file=sys.stderr)

    out: dict[str, dict[str, str]] = {}
    for i, fi in enumerate(fi_names):
        if i < len(arr) and isinstance(arr[i], dict):
            zh = (arr[i].get("zh") or fi).strip()
            en = (arr[i].get("en_search") or "").strip()
            out[fi] = {"zh": zh, "en_search": en}
        else:
            out[fi] = {"zh": fi, "en_search": ""}
    return out


# --- 图片：Pexels ----------------------------------------------------------

def fetch_images(en_queries: list[str]) -> dict[str, str]:
    """对每个英文查询返回一张 Pexels 图片 URL。缺 key 或失败返回空映射。"""
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        print("warn: PEXELS_API_KEY 未设置，跳过图片抓取", file=sys.stderr)
        return {}

    sess = requests.Session()
    sess.headers["Authorization"] = api_key
    sess.headers["User-Agent"] = HEADERS["User-Agent"]

    out: dict[str, str] = {}
    for q in en_queries:
        if not q or q in out:
            continue
        try:
            r = sess.get(
                "https://api.pexels.com/v1/search",
                params={"query": q, "per_page": 1, "orientation": "square"},
                timeout=15,
            )
            if r.status_code != 200:
                print(f"  pexels {q!r} -> {r.status_code}", file=sys.stderr)
                continue
            photos = r.json().get("photos", [])
            if photos:
                src = photos[0].get("src", {})
                out[q] = src.get("medium") or src.get("small") or src.get("original", "")
        except Exception as e:
            print(f"  pexels {q!r} 失败: {e!r}", file=sys.stderr)
    return out


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
    tr_map = translate_batch(all_names)

    en_queries = [tr_map.get(n, {}).get("en_search", "") for n in all_names]
    print(f"抓图 {sum(1 for q in en_queries if q)} 个查询…", file=sys.stderr)
    img_map = fetch_images(en_queries)

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today,
        "restaurants": [],
    }
    for r, dishes in per_restaurant:
        items = []
        for d in dishes:
            tr = tr_map.get(d["fi"], {})
            zh = tr.get("zh") or d["fi"]
            en_q = tr.get("en_search") or ""
            items.append({
                "fi": d["fi"],
                "zh": zh,
                "emoji": emoji_for(d["fi"]),
                "category": d["category"],
                "tags": d["tags"],
                "image_url": img_map.get(en_q, ""),
            })
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
