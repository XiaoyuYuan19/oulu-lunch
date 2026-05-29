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


# 配菜/酱/甜点的关键词；匹配则不会被选作"主菜"用来搜图。
SIDE_RE = re.compile(
    r"\b(riisi|risotto|peruna|perun|lohko|leip|sämpyl|patonki|"
    r"hilloa|hillo|marmel|kermavaaht|kastik|"
    r"salaat|tomaatti(kasti)?|piimäkasti|"
    r"jälkiruok|kakku|piirakk|munkki|pulla|"
    r"keitettyj|paahdettuj|höyrytetty)",
    re.IGNORECASE,
)


PREMIUM_RE = re.compile(r"\b(fusion|erikois|special|à la carte)\b", re.IGNORECASE)


def fetch_items(customer_id: int, kitchen_id: int, today: int) -> list[dict]:
    """扁平返回今天的所有菜品：{fi, tags, source_meal, is_premium}。"""
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
                        source = (opt.get("name") or "").strip()
                        premium = bool(PREMIUM_RE.search(source))
                        for mi in opt.get("menuItems", []):
                            name = clean_name((mi.get("name") or "").strip())
                            if not name or name in seen:
                                continue
                            seen.add(name)
                            items.append({
                                "fi": name,
                                "tags": parse_diets(mi.get("diets")),
                                "source_meal": source,
                                "is_premium": premium,
                            })
    return items


GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]


VALID_ROLES = {"main", "staple", "sauce", "salad", "dessert", "side"}


def translate_batch(fi_names: list[str]) -> dict[str, dict[str, str]]:
    """对每个菜名返回 {'zh': 中文, 'en_search': 英文图片关键词, 'role': main/staple/sauce/salad/dessert/side}"""
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
        role: str  # main/staple/sauce/salad/dessert/side

    client = genai.Client(api_key=api_key)
    numbered = "\n".join(f"{i+1}. {n}" for i, n in enumerate(fi_names))
    prompt = (
        "下面是带编号的芬兰餐厅菜名。芬兰学生餐的点法：选一道主菜（main）"
        "+ 配的主食（staple，米饭/土豆/面食）+ 自助沙拉吧 + 面包 + 一杯餐饮。"
        "对每一条输出对象：\n"
        '  zh = 简短自然的中文菜名（不带编号/原文/解释/标点）\n'
        '  en_search = 2-3 个英文 Pexels 检索关键词，描述菜式外观，不要音译\n'
        '  role = 该菜在套餐里的角色，必为以下之一：\n'
        '    "main"    = 主菜（蛋白质/主角，如鸡/鱼/肉丸/falafel/扁豆饼/披萨）\n'
        '    "staple"  = 主食（米饭/土豆/面条/麦饭/薯泥/烤土豆等碳水底盘）\n'
        '    "sauce"   = 浇汁/酱（pippurikastike, kermakastike, tomaattikastike 等）\n'
        '    "salad"   = 沙拉/凉拌\n'
        '    "dessert" = 甜点/糕点/果酱/打发奶油\n'
        '    "side"    = 配蔬菜（烤蔬菜/煮蔬菜，非碳水非主菜）\n'
        '汤类（keitto/keittoa）算 "main"。\n'
        "返回 JSON 数组，长度与编号一致，顺序对齐。\n\n"
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
            role = (arr[i].get("role") or "").strip().lower()
            if role not in VALID_ROLES:
                role = "side"
            out[fi] = {"zh": zh, "en_search": en, "role": role}
        else:
            out[fi] = {"zh": fi, "en_search": "", "role": "side"}
    return out


# --- 图片：Pexels ----------------------------------------------------------

def fetch_images(en_queries: list[str], per_query: int = 5) -> dict[str, list[str]]:
    """每个英文查询拉 Pexels 前 N 张候选 URL。缺 key 或失败返回空映射。"""
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        print("warn: PEXELS_API_KEY 未设置，跳过图片抓取", file=sys.stderr)
        return {}

    sess = requests.Session()
    sess.headers["Authorization"] = api_key
    sess.headers["User-Agent"] = HEADERS["User-Agent"]

    out: dict[str, list[str]] = {}
    for q in en_queries:
        if not q or q in out:
            continue
        try:
            r = sess.get(
                "https://api.pexels.com/v1/search",
                params={"query": q, "per_page": per_query, "orientation": "square"},
                timeout=15,
            )
            if r.status_code != 200:
                print(f"  pexels {q!r} -> {r.status_code}", file=sys.stderr)
                continue
            urls: list[str] = []
            for p in r.json().get("photos", []):
                src = p.get("src", {})
                u = src.get("medium") or src.get("small") or src.get("original", "")
                if u:
                    urls.append(u)
            out[q] = urls
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
            items = fetch_items(customer_id, r["kitchen_id"], today)
            print(f"   {len(items)} 个菜品", file=sys.stderr)
        except Exception as e:
            print(f"   fetch failed: {e}", file=sys.stderr)
            items = []
        per_restaurant.append((r, items))

    all_names: list[str] = []
    seen: set[str] = set()
    for _, items in per_restaurant:
        for it in items:
            if it["fi"] not in seen:
                seen.add(it["fi"])
                all_names.append(it["fi"])

    print(f"翻译+分类 {len(all_names)} 个菜品…", file=sys.stderr)
    tr_map = translate_batch(all_names)

    # 只为主菜抓图，每个查询拉多张候选
    main_fi_order: list[str] = []
    main_queries: list[str] = []
    seen_fi: set[str] = set()
    for _, items in per_restaurant:
        for it in items:
            if tr_map.get(it["fi"], {}).get("role") != "main":
                continue
            if it["fi"] not in seen_fi:
                seen_fi.add(it["fi"])
                main_fi_order.append(it["fi"])
            en = tr_map[it["fi"]].get("en_search", "")
            if en and en not in main_queries:
                main_queries.append(en)

    print(f"抓图 {len(main_queries)} 个查询…", file=sys.stderr)
    candidates = fetch_images(main_queries)

    # 同一道菜（fi 相同）→ 同一张图；不同菜之间避让候选
    used_urls: set[str] = set()
    fi_to_image: dict[str, str] = {}
    for fi in main_fi_order:
        en = tr_map.get(fi, {}).get("en_search", "")
        cands = candidates.get(en, [])
        pick = next((u for u in cands if u not in used_urls), cands[0] if cands else "")
        if pick:
            used_urls.add(pick)
        fi_to_image[fi] = pick

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today,
        "restaurants": [],
    }
    for r, items in per_restaurant:
        buckets: dict[str, list[dict]] = {k: [] for k in ("main", "staple", "sauce", "salad", "dessert", "side")}
        for it in items:
            tr = tr_map.get(it["fi"], {})
            role = tr.get("role") or "side"
            zh = tr.get("zh") or it["fi"]
            entry = {
                "fi": it["fi"],
                "zh": zh,
                "emoji": emoji_for(it["fi"]),
                "tags": it["tags"],
                "is_premium": it["is_premium"],
                "source": it["source_meal"],
            }
            if role == "main":
                entry["image_url"] = fi_to_image.get(it["fi"], "")
            buckets.setdefault(role, []).append(entry)

        output["restaurants"].append({
            "name": r["name"],
            "hours": r.get("hours", ""),
            "location": r.get("location", ""),
            "price_basic": r.get("price_basic"),
            "price_fusion": r.get("price_fusion"),
            "kitchen_id": r["kitchen_id"],
            "mains":    buckets["main"],
            "staples":  buckets["staple"],
            "sauces":   buckets["sauce"],
            "salads":   buckets["salad"],
            "desserts": buckets["dessert"],
            "sides":    buckets["side"],
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✓ wrote {OUTPUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
