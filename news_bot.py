#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
全球军事 / AI / Cloudflare 资讯定时推送
- 抓取 RSS 源 -> 解析 -> 中文排版 -> 跨运行去重 -> 推送 Telegram 群
- 用法:
    python news_bot.py            # 正常跑（有 token 就推送，无 token 自动 dry-run）
    python news_bot.py --dry-run  # 强制只打印，不推送
    python news_bot.py --bootstrap # 仅把当前条目写入 seen（不推送），用于冷启动
"""
import argparse, json, os, sys, html, time, datetime, re
from concurrent.futures import ThreadPoolExecutor, as_completed
import feedparser, requests

BASE = os.path.dirname(os.path.abspath(__file__))
FEEDS_FILE = os.path.join(BASE, "feeds.json")
CFG_FILE   = os.path.join(BASE, "bot_config.json")
SEEN_FILE  = os.path.join(BASE, "seen.json")
STATE_DIR  = BASE
UA = "Mozilla/5.0 (compatible; NewsDigestBot/1.0)"

def log(*a):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}]", *a, flush=True)

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def load_seen():
    d = load_json(SEEN_FILE, {})
    # d 结构: { guid: first_seen_iso }
    return d if isinstance(d, dict) else {}

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=0)

def html_esc(s):
    return html.escape(s or "", quote=True)

def clean_text(s):
    if not s:
        return ""
    s = re_sub_tags(s)
    # 源里常有双重/多重转义: &amp;#x27; -> &#x27; -> '，html.unescape 完整支持数字实体
    prev = None
    for _ in range(5):
        if s == prev:
            break
        prev = s
        s = html.unescape(s)
    s = " ".join(s.split())
    return s

_TAG_RE = None
def re_sub_tags(s):
    import re
    return re.sub(r"<[^>]+>", "", s)

def entry_guid(e):
    return e.get("guid") or e.get("id") or e.get("link") or ""

def entry_date(e):
    for k in ("published_parsed", "updated_parsed", "created_parsed"):
        v = e.get(k)
        if v:
            try:
                return datetime.datetime(*v[:6])
            except Exception:
                pass
    return None

def _fetch_one(feed):
    """抓取单个源，返回 (name, cat, cn, parsed_entries)。失败返回空列表。"""
    name, cat, url = feed["name"], feed["cat"], feed["url"]
    cn = feed.get("cn", name)
    last_err = ""
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20, verify=False)
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            if (parsed.bozo and not parsed.entries):
                last_err = f"解析异常 {parsed.get('bozo_exception')}"
                continue
            entries = parsed.entries[: feed.get("max", 5)]
            return (name, cat, cn, entries)
        except Exception as ex:
            last_err = str(ex)
            time.sleep(1 + attempt)
    log(f"  [WARN] 抓取失败 {name}: {last_err}")
    return (name, cat, cn, [])

def fetch_all(feeds, seen, collect):
    """并发抓取所有源，把新条目加入 collect（按分类）。返回总新条目数。"""
    total_new = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_one, f): f for f in feeds}
        for fut in as_completed(futures):
            name, cat, cn, entries = fut.result()
            for e in entries:
                guid = entry_guid(e)
                if not guid:
                    continue
                if guid in seen:
                    continue
                seen[guid] = datetime.datetime.now().isoformat(timespec="seconds")
                title = clean_text(e.get("title", ""))
                link = e.get("link", "")
                summary = clean_text(e.get("summary", ""))
                if len(summary) > 220:
                    summary = summary[:220].rstrip() + "…"
                dt = entry_date(e)
                date_str = dt.strftime("%m-%d") if dt else ""
                collect.setdefault(cat, []).append({
                    "source": name, "cn": cn, "title": title, "link": link,
                    "summary": summary, "date": date_str, "ts": dt or datetime.datetime.min
                })
                total_new += 1
    return total_new

def build_message(cfg, digest_by_cat, tz, cats=None):
    if cats is None:
        cats = {}
    now = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append("📰 <b>全球资讯速递</b>")
    lines.append(f"🕗 {now}（北京时间）")
    lines.append("")
    cats_local = cats
    order = ["military", "ai", "cloudflare"]
    total = 0
    sources_used = set()
    cat_counts = {}
    for cat in order:
        items = digest_by_cat.get(cat, [])
        if not items:
            continue
        cat_counts[cat] = len(items)
        lines.append(f"{cats_local.get(cat, cat)}")
        for it in items:
            title_html = html_esc(it["title"])
            src = html_esc(it.get("cn") or it["source"])
            sources_used.add(it.get("cn") or it["source"])
            date_part = f" · {it['date']}" if it["date"] else ""
            lines.append(f"• <b>{title_html}</b>")
            link = it["link"]
            if link:
                lines.append(f'  <a href="{link}">来源: {src}{date_part}</a>')
            else:
                lines.append(f"  来源: {src}{date_part}")
            if it["summary"]:
                lines.append(f"  {html_esc(it['summary'])}")
        lines.append("")
        total += len(items)
    # 统计脚注
    if cfg.get("show_stats", True) and total > 0:
        lines.append("—" * 12)
        cc = " / ".join(f"{cats_local.get(c, c)} {cat_counts[c]}" for c in order if cat_counts.get(c))
        lines.append(f"📊 本期共 <b>{total}</b> 条（{cc}）· 来源 {len(sources_used)} 个")
    msg = "\n".join(lines).strip()
    return msg, total

def send_telegram(cfg, text):
    token = cfg.get("telegram_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        log("[DRY-RUN] 未配置 token/chat_id，仅打印消息：")
        print("─" * 40)
        print(text)
        print("─" * 40)
        return True
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=30)
        data = r.json()
        if data.get("ok"):
            log(f"[OK] 已推送，message_id={data.get('result', {}).get('message_id')}")
            return True
        else:
            log(f"[FAIL] Telegram 返回错误: {data}")
            return False
    except Exception as ex:
        log(f"[FAIL] 推送异常: {ex}")
        return False

def prune_seen(seen, days=30):
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    pruned = {}
    removed = 0
    for k, v in seen.items():
        try:
            t = datetime.datetime.fromisoformat(v)
            if t >= cutoff:
                pruned[k] = v
            else:
                removed += 1
        except Exception:
            pruned[k] = v
    if removed:
        log(f"  清理 {removed} 条过期 seen 记录")
    return pruned

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="强制不推送，仅打印")
    ap.add_argument("--bootstrap", action="store_true", help="仅写 seen，不推送")
    args = ap.parse_args()

    feeds_cfg = load_json(FEEDS_FILE, {})
    cfg = load_json(CFG_FILE, {})
    feeds = feeds_cfg.get("feeds", [])
    cats = feeds_cfg.get("categories", {})

    # 环境变量优先（GitHub Actions / 容器化运行时注入 secrets）
    env_token = os.environ.get("TG_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    env_chat = os.environ.get("TG_CHAT") or os.environ.get("CHAT_ID")
    if env_token:
        cfg["telegram_token"] = env_token
    if env_chat:
        cfg["chat_id"] = env_chat

    tz = datetime.timezone.utc
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(cfg.get("timezone", "Asia/Shanghai"))
    except Exception:
        pass

    seen = load_seen()
    collect = {}
    log(f"开始并发抓取 {len(feeds)} 个源…")
    t0 = time.time()
    total_new = fetch_all(feeds, seen, collect)
    log(f"抓取完成，耗时 {time.time()-t0:.1f}s，本次新条目: {total_new}，累计 seen={len(seen)}")

    if args.bootstrap:
        save_seen(seen)
        log("[bootstrap] 已写入 seen.json，退出（不推送）。")
        return

    # 翻译：标题 + 摘要 翻译成中文（并发 + 缓存）
    trans_enabled = cfg.get("translate", True)
    if trans_enabled:
        log("翻译中（标题/摘要 -> 中文）…")
        from translate import translate_batch
        for c in collect:
            items = collect[c]
            titles = [it["title"] for it in items]
            zt = translate_batch(titles)
            sums = [it["summary"] for it in items]
            zs = translate_batch(sums)
            for it, zt_i, zs_i in zip(items, zt, zs):
                it["title"] = zt_i or it["title"]
                if zs_i:
                    it["summary"] = zs_i[:220]
            log(f"  {c}: 已翻译 {len(items)} 条")

    # 排序：每类按时间倒序
    for c in collect:
        collect[c].sort(key=lambda x: x["ts"], reverse=True)

    # Cloudflare 每天只保留最热的 1 条（按 feed 顺序/时间取首条）
    cf_max = cfg.get("cloudflare_max", 1)
    if "cloudflare" in collect:
        collect["cloudflare"] = collect["cloudflare"][:cf_max]

    send_when_empty = cfg.get("send_when_empty", True)
    has_content = any(collect.values())
    if not has_content:
        if send_when_empty:
            msg, _ = build_message(cfg, {}, tz, cats)
            msg += "\n\n" + html_esc(cfg.get("empty_message", "📭 本时段暂无新增资讯。"))
            send_telegram(cfg, msg) if not args.dry_run else (log("[DRY-RUN] empty"), print(msg))
        else:
            log("无新条目，按配置不推送。")
        save_seen(prune_seen(seen))
        return

    # 字符预算分配：每类取等量头条，总量受 3900 字符约束
    per_cat = cfg.get("per_cat_max", 8)
    order = [c for c in ["military", "ai", "cloudflare"] if collect.get(c)]
    # 先按 per_cat 截断每类
    for c in collect:
        collect[c] = collect[c][:per_cat]
    # 若仍超长，逐步降低每类条数直到合规（最少每类1条）
    while True:
        msg, _ = build_message(cfg, collect, tz, cats)
        if len(msg) <= 3900:
            break
        # 找当前最长的类减1
        longest = max(order, key=lambda c: len(collect[c]))
        if len(collect[longest]) <= 1:
            break
        collect[longest] = collect[longest][:-1]

    msg, total = build_message(cfg, collect, tz, cats)
    if len(msg) > 4000:
        msg = msg[:3900].rstrip() + "\n…(已截断)"
    if args.dry_run:
        send_telegram(cfg, msg)  # dry-run 内部会打印不推送（若 token 缺失）
    else:
        send_telegram(cfg, msg)
    save_seen(prune_seen(seen))

if __name__ == "__main__":
    main()
