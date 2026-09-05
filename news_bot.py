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
SEEN_FILE = os.path.join(BASE, "seen.json")
LAST_RUN_FILE = os.path.join(BASE, "last_run.json")
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
    """并发抓取所有源，把新条目加入 collect（统一放 'general'）。返回总新条目数。"""
    total_new = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_one, f): f for f in feeds}
        for fut in as_completed(futures):
            name, cat, cn, entries = fut.result()
            for e in entries:
                guid = entry_guid(e)
                if not guid or guid in seen:
                    continue
                seen[guid] = datetime.datetime.now().isoformat(timespec="seconds")
                title = clean_text(e.get("title", ""))
                link = e.get("link", "")
                summary = clean_text(e.get("summary", ""))
                if len(summary) > 300:
                    summary = summary[:300].rstrip() + "…"
                dt = entry_date(e)
                date_str = dt.strftime("%m-%d") if dt else ""
                # 封面：从 enclosure / media_content / 正文里提取第一个图
                image = ""
                for key in ("enclosures", "media_content"):
                    arr = e.get(key) or []
                    if arr:
                        cand = arr[0].get("href") or arr[0].get("url") or ""
                        if cand and cand.startswith("http"):
                            image = cand
                            break
                if not image:
                    m = re.search(r'https?://[^\s\"<>]+\.(?:jpg|jpeg|png|webp)', e.get("summary", "") + " " + e.get("content", [{}])[0].get("value", ""), re.I)
                    if m:
                        image = m.group(0)
                collect.setdefault("general", []).append({
                    "source": name, "cn": cn, "title": title, "link": link,
                    "summary": summary, "date": date_str, "ts": dt or datetime.datetime.min,
                    "image": image
                })
                total_new += 1
    return total_new

def build_message(cfg, item):
    """为单条热点新闻构建消息体。"""
    if not item:
        return "", 0
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    src = html_esc(item.get("cn") or item["source"])
    title = html_esc(item["title"])
    date_part = f" · {item['date']}" if item["date"] else ""
    summary = item.get("summary", "")
    lines = [
        "📰 <b>全球热点速递</b>",
        f"🕗 {now}（北京时间）",
        "",
        f"• <b>{title}</b>",
        f'  <a href="{item["link"]}">来源: {src}{date_part}</a>',
    ]
    if summary:
        lines.append(f"  {html_esc(summary)}")
    lines.append("")
    if cfg.get("show_stats", True):
        lines.append("—" * 12)
        lines.append("📊 1 条热点 · 随机推送")
    msg = "\n".join(lines).strip()
    return msg, 1

def send_telegram(cfg, text, image=None):
    token = cfg.get("telegram_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        log("[DRY-RUN] 未配置 token/chat_id，仅打印消息：")
        print("─" * 40)
        print(text)
        print("─" * 40)
        return True
    try:
        if image:
            # 带封面：sendPhoto + caption
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            payload = {
                "chat_id": chat_id,
                "photo": image,
                "caption": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        else:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        r = requests.post(url, json=payload, timeout=30)
        data = r.json()
        if data.get("ok"):
            log(f"[OK] 已推送，message_id={data.get('result', {}).get('message_id')}")
            return True
        else:
            log(f"[FAIL] Telegram 返回错误: {data}")
            # 带图失败时回退到纯文本
            if image and "photo" in (data.get("description") or "").lower():
                log("[WARN] sendPhoto 失败，回退 sendMessage")
                return send_telegram(cfg, text, image=None)
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

    # 防抖：距上次推送不足 55 分钟则跳过（避免 keepalive 在不足 1 小时内反复触发）
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if os.path.exists(LAST_RUN_FILE) and not args.dry_run:
        try:
            last = json.load(open(LAST_RUN_FILE, encoding="utf-8")).get("last_run", "")
            if last:
                last_ts = datetime.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                delta_min = (datetime.datetime.now(datetime.timezone.utc) - last_ts).total_seconds() / 60
                if delta_min < 55:
                    log(f"距上次推送仅 {delta_min:.1f} 分钟（< 55 分钟），跳过本次。")
                    return
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

    # 随机选 1 条热点（偏好有封面的）
    all_items = collect.get("general", [])
    if not all_items:
        if send_when_empty:
            msg, _ = build_message(cfg, {})
            msg += "\n\n" + html_esc(cfg.get("empty_message", "📭 本时段暂无新增资讯。"))
            send_telegram(cfg, msg) if not args.dry_run else (log("[DRY-RUN] empty"), print(msg))
        else:
            log("无新条目，按配置不推送。")
        save_seen(prune_seen(seen))
        return

    # 优先中文源，次选英文源，避免整晚都是翻译后的英语媒体内容
    cn_items = [it for it in all_items if any('\u4e00' <= c <= '\u9fff' for c in (it.get("title") or ""))]
    pool = cn_items if cn_items else all_items
    pick = pool[int(time.time()) % len(pool)]

    # 翻译：只翻选中的 1 条
    trans_enabled = cfg.get("translate", True)
    if trans_enabled:
        log(f"翻译热点条目（标题/摘要 -> 中文）…")
        from translate import translate_batch
        zt = translate_batch([pick["title"]]) or [pick["title"]]
        zs = translate_batch([pick.get("summary", "")]) or [pick.get("summary", "")]
        pick["title"] = zt[0] or pick["title"]
        if zs[0]:
            pick["summary"] = zs[0][:300]

    msg, total = build_message(cfg, pick)
    if args.dry_run:
        send_telegram(cfg, msg, image=pick.get("image"))
    else:
        send_telegram(cfg, msg, image=pick.get("image"))
    save_seen(prune_seen(seen))

if __name__ == "__main__":
    main()
