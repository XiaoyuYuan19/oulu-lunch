"""抓 Juvenes 学生餐厅菜单（Jamix Cloud JSON API）+ Gemini 翻译 → public/data.json。"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
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


def upcoming_workdays(n: int = 5) -> list[int]:
    """从今天起接下来 n 个工作日（YYYYMMDD int）。周末跳过。"""
    out: list[int] = []
    cur = datetime.now(TZ_HELSINKI).date()
    while len(out) < n:
        if cur.weekday() < 5:  # Mon=0 ... Fri=4
            out.append(cur.year * 10000 + cur.month * 100 + cur.day)
        cur = cur + timedelta(days=1)
    return out


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


def fetch_items_by_date(customer_id: int, kitchen_id: int, dates: set[int]) -> dict[int, list[dict]]:
    """按日期返回每天的所有菜品。{date: [{fi, tags, source_meal, is_premium}]}"""
    url = JAMIX_API.format(cust=customer_id, kitchen=kitchen_id)
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()

    by_date: dict[int, list[dict]] = {d: [] for d in dates}
    seen_per_date: dict[int, set[str]] = {d: set() for d in dates}

    for kitchen in data:
        for mt in kitchen.get("menuTypes", []):
            for menu in mt.get("menus", []):
                for day in menu.get("days", []):
                    d = day.get("date")
                    if d not in dates:
                        continue
                    for opt in day.get("mealoptions", []):
                        source = (opt.get("name") or "").strip()
                        premium = bool(PREMIUM_RE.search(source))
                        for mi in opt.get("menuItems", []):
                            name = clean_name((mi.get("name") or "").strip())
                            if not name or name in seen_per_date[d]:
                                continue
                            seen_per_date[d].add(name)
                            by_date[d].append({
                                "fi": name,
                                "tags": parse_diets(mi.get("diets")),
                                "source_meal": source,
                                "is_premium": premium,
                            })
    return by_date


GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]


VALID_ROLES = {"main", "staple", "sauce", "salad", "dessert", "side"}


BATCH_SIZE = 25

try:
    from pydantic import BaseModel as _BaseModel

    class _Translation(_BaseModel):
        zh: str
        en_search: str
        role: str

    _PYDANTIC_OK = True
except Exception:
    _PYDANTIC_OK = False
    _Translation = None


def translate_batch(fi_names: list[str]) -> dict[str, dict[str, str]]:
    """大批量分成 BATCH_SIZE 块跑，避开 Gemini 输出 token 上限。"""
    if not fi_names:
        return {}
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("warn: GEMINI_API_KEY 未设置，跳过翻译", file=sys.stderr)
        return {}
    if not _PYDANTIC_OK:
        print("warn: pydantic 未导入，跳过翻译", file=sys.stderr)
        return {}

    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        print(f"warn: google-genai import 失败: {e!r}", file=sys.stderr)
        return {}

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=list[_Translation],
        temperature=0.2,
        max_output_tokens=8192,
    )

    def call_one(batch: list[str], model: str) -> list:
        numbered = "\n".join(f"{i+1}. {n}" for i, n in enumerate(batch))
        prompt = (
            "下面是带编号的芬兰餐厅菜名。芬兰学生餐含 主菜 + 主食 + 自助沙拉吧 + 面包 + 饮料。"
            "对每一条输出对象：\n"
            '  zh = 简短自然的中文菜名（不带编号/原文/解释/标点）\n'
            '  en_search = 2-3 个英文 Pexels 检索关键词，描述菜式外观，不音译\n'
            '  role ∈ {"main","staple","sauce","salad","dessert","side"}\n'
            '    main: 蛋白质主角（鸡/鱼/肉丸/炖菜/falafel/扁豆饼/披萨/汤/烩菜）；'
            '"X-kasvis-kastike"(肉/禽+蔬菜+酱) 算 main\n'
            '    staple: 米饭/土豆/面条/薯泥\n'
            '    sauce: 单独的浇汁/酱（番茄酱/胡椒奶油酱/罗勒酱）\n'
            '    dessert: 甜点/果酱/打发奶油/煎饼\n'
            '    side: 烤蔬菜/煮蔬菜等非碳水非主菜配菜\n'
            "返回 JSON 数组，长度与编号一致。\n\n"
            f"{numbered}"
        )
        resp = client.models.generate_content(
            model=model, contents=prompt, config=config,
        )
        text = (resp.text or "").strip()
        if not text:
            print(f"    [{model}] empty response", file=sys.stderr)
            return []
        print(f"    [{model}] {len(text)} 字符返回", file=sys.stderr)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"    JSON parse 失败 {e!r}; 尾 150: {text[-150:]!r}", file=sys.stderr)
            return []

    batches = [fi_names[i:i + BATCH_SIZE] for i in range(0, len(fi_names), BATCH_SIZE)]
    print(f"  分 {len(batches)} 批 × ≤{BATCH_SIZE}", file=sys.stderr)

    all_arr: list = []
    chosen_model: str | None = None
    for bi, batch in enumerate(batches):
        arr: list = []
        models_to_try = GEMINI_MODELS if chosen_model is None else [chosen_model] + [m for m in GEMINI_MODELS if m != chosen_model]
        for model in models_to_try:
            try:
                arr = call_one(batch, model)
                if arr:
                    chosen_model = model
                    break
            except Exception as e:
                print(f"  批 {bi+1}/{len(batches)} 模型 {model} 异常: {e!r}", file=sys.stderr)
        if not arr:
            print(f"  ⚠ 批 {bi+1} 完全没结果", file=sys.stderr)
            arr = [{} for _ in batch]
        if len(arr) < len(batch):
            print(f"  ⚠ 批 {bi+1} 返回 {len(arr)}/{len(batch)}, 末尾补空", file=sys.stderr)
            arr = list(arr) + [{}] * (len(batch) - len(arr))
        all_arr.extend(arr)

    print(f"  模型: {chosen_model}, 合计 {len(all_arr)}/{len(fi_names)} 条", file=sys.stderr)

    out: dict[str, dict[str, str]] = {}
    for i, fi in enumerate(fi_names):
        if i < len(all_arr) and isinstance(all_arr[i], dict) and all_arr[i]:
            item = all_arr[i]
            zh = (item.get("zh") or fi).strip()
            en = (item.get("en_search") or "").strip()
            role = (item.get("role") or "").strip().lower()
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


def bucket_items(items: list[dict], tr_map: dict, fi_to_image: dict) -> dict[str, list[dict]]:
    buckets = {k: [] for k in ("main", "staple", "sauce", "salad", "dessert", "side")}
    for it in items:
        tr = tr_map.get(it["fi"], {})
        role = tr.get("role") or "side"
        entry = {
            "fi": it["fi"],
            "zh": tr.get("zh") or it["fi"],
            "emoji": emoji_for(it["fi"]),
            "tags": it["tags"],
            "is_premium": it["is_premium"],
            "source": it["source_meal"],
        }
        if role == "main":
            entry["image_url"] = fi_to_image.get(it["fi"], "")
        buckets.setdefault(role, []).append(entry)
    return buckets


def main() -> int:
    with open(CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    customer_id = cfg["customer_id"]
    dates = upcoming_workdays(5)
    date_set = set(dates)
    print(f"dates={dates}", file=sys.stderr)

    # {restaurant_idx: {date: [items]}}
    per_r: list[tuple[dict, dict[int, list[dict]]]] = []
    for r in cfg["restaurants"]:
        print(f"→ {r['name']} (k={r['kitchen_id']})", file=sys.stderr)
        try:
            by_date = fetch_items_by_date(customer_id, r["kitchen_id"], date_set)
            total = sum(len(v) for v in by_date.values())
            print(f"   {total} 个菜品 / {sum(1 for v in by_date.values() if v)} 天有菜", file=sys.stderr)
        except Exception as e:
            print(f"   fetch failed: {e}", file=sys.stderr)
            by_date = {d: [] for d in dates}
        per_r.append((r, by_date))

    # 收集所有日所有餐厅的菜名（去重）
    all_names: list[str] = []
    seen: set[str] = set()
    for _, by_date in per_r:
        for items in by_date.values():
            for it in items:
                if it["fi"] not in seen:
                    seen.add(it["fi"])
                    all_names.append(it["fi"])

    print(f"翻译+分类 {len(all_names)} 个唯一菜名…", file=sys.stderr)
    tr_map = translate_batch(all_names)

    # 主菜抓图（5 天合一起，去重共享）
    main_fi_order: list[str] = []
    main_queries: list[str] = []
    seen_fi: set[str] = set()
    for _, by_date in per_r:
        for items in by_date.values():
            for it in items:
                if tr_map.get(it["fi"], {}).get("role") != "main":
                    continue
                if it["fi"] not in seen_fi:
                    seen_fi.add(it["fi"])
                    main_fi_order.append(it["fi"])
                en = tr_map[it["fi"]].get("en_search", "")
                if en and en not in main_queries:
                    main_queries.append(en)

    print(f"抓图 {len(main_queries)} 个查询 ({len(main_fi_order)} 道主菜)…", file=sys.stderr)
    candidates = fetch_images(main_queries)

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
        "dates": dates,
        "today": today_yyyymmdd(),
        "restaurants": [],
    }
    for r, by_date in per_r:
        days_out: dict[str, dict] = {}
        for d in dates:
            days_out[str(d)] = bucket_items(by_date.get(d, []), tr_map, fi_to_image)
        output["restaurants"].append({
            "name": r["name"],
            "hours": r.get("hours", ""),
            "location": r.get("location", ""),
            "price_basic": r.get("price_basic"),
            "price_fusion": r.get("price_fusion"),
            "closed": r.get("closed"),
            "kitchen_id": r["kitchen_id"],
            "by_date": days_out,
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✓ wrote {OUTPUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
