#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""轻量翻译模块：Google 免费 gtx 端点，带并发、缓存、重试。"""
import json, os, urllib.parse, threading, time
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE, "translate_cache.json")
_LOCK = threading.Lock()

_cache = {}
def _load_cache():
    global _cache
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    except Exception:
        _cache = {}

def _save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False)
    except Exception:
        pass

_load_cache()

UA = "Mozilla/5.0"

def _translate_one(text, to="zh-CN", src="en"):
    if not text or not text.strip():
        return text
    key = f"{src}|{to}|{text.strip()}"
    with _LOCK:
        if key in _cache:
            return _cache[key]
    try:
        q = urllib.parse.quote(text[:4000])
        url = (f"https://translate.googleapis.com/translate_a/single"
               f"?client=gtx&sl={src}&tl={to}&dt=t&q={q}")
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        r.raise_for_status()
        data = r.json()
        zh = "".join(seg[0] for seg in data[0] if seg and seg[0])
        zh = zh.strip()
        with _LOCK:
            _cache[key] = zh
            _save_cache()
        return zh
    except Exception:
        return text  # 翻译失败回退原文

def translate(text, to="zh-CN", src="en"):
    return _translate_one(text, to, src)

def translate_batch(texts, to="zh-CN", src="en", workers=6):
    """并发翻译一批文本，保持顺序返回。已为中文的输入跳过翻译。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = [None] * len(texts)
    def is_chinese(s):
        return any('一' <= c <= '鿿' for c in (s or ""))
    jobs = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, t in enumerate(texts):
            if not t or is_chinese(t):
                results[i] = t or ""
                continue
            jobs[ex.submit(_translate_one, t, to, src)] = i
        for fut in as_completed(jobs):
            i = jobs[fut]
            try:
                results[i] = fut.result()
            except Exception:
                results[i] = texts[i]
    return results

if __name__ == "__main__":
    print(translate("U.S. Air Force buys Chinese-made drones for nuclear missile base"))
    print(translate("Cloudflare detects MCP traffic and helps secure it"))
