#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Telegram 自检工具
用法:
  python tg_setup.py token <BOT_TOKEN>            # 验证 token 有效性，打印 bot 信息
  python tg_setup.py chats <BOT_TOKEN>            # 列出 bot 最近收到消息的会话(群/私聊)及其 chat_id
  python tg_setup.py send  <BOT_TOKEN> <CHAT_ID> <文本>   # 发一条测试消息
  python tg_setup.py test  <BOT_TOKEN> <CHAT_ID>  # 发一条完整格式样稿(验证 HTML 解析)
"""
import sys, json, requests

def api(token, method, **kw):
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        r = requests.post(url, json=kw, timeout=30)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def cmd_token(token):
    d = api(token, "getMe")
    if d.get("ok"):
        u = d["result"]
        print("✅ Token 有效")
        print(f"  bot 用户名: @{u.get('username')}  (id={u.get('id')})")
        print(f"  名称: {u.get('first_name')}")
    else:
        print("❌ Token 无效:", d)

def cmd_chats(token):
    d = api(token, "getUpdates", timeout=30, allowed_updates=["message","my_chat_member"])
    if not d.get("ok"):
        print("❌ 获取会话失败:", d); return
    ups = d.get("result", [])
    if not ups:
        print("⚠️ 暂无会话记录。请先在目标群里 @你的bot 发一条消息(或把 bot 拉进群并说话)，再运行此命令。")
        return
    seen = {}
    for u in ups:
        msg = u.get("message") or u.get("my_chat_member") or {}
        ch = msg.get("chat", {})
        cid = ch.get("id")
        if cid and cid not in seen:
            seen[cid] = ch
    print(f"发现 {len(seen)} 个会话：")
    for cid, ch in seen.items():
        typ = ch.get("type")
        title = ch.get("title") or ch.get("username") or ch.get("first_name") or "?"
        print(f"  chat_id={cid}  类型={typ}  名称={title}")

def cmd_send(token, chat_id, text):
    d = api(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
    if d.get("ok"):
        print("✅ 已发送测试消息, message_id=", d["result"]["message_id"])
    else:
        print("❌ 发送失败:", d)

def cmd_test(token, chat_id):
    sample = ("📰 <b>全球资讯速递</b>\n"
              "🕗 2026-08-17 08:00（北京时间）\n\n"
              "🛡️ 全球军事\n"
              "• <b>示例：U.S. Air Force buys drones</b>\n"
              '  <a href="https://defence-blog.com/">来源: Defence Blog · 08-16</a>\n'
              "  This is a sample summary with apostrophe’s and quotes “ok”.\n\n"
              "🤖 AI 前沿\n"
              "• <b>示例：OpenAI launches GPT-Next</b>\n"
              '  <a href="https://openai.com/">来源: OpenAI Blog · 08-16</a>\n\n'
              "☁️ Cloudflare 教程 / 动态\n"
              "• <b>示例：Cloudflare 推出新 Workers 功能</b>\n"
              '  <a href="https://blog.cloudflare.com/">来源: Cloudflare Blog · 08-15</a>')
    cmd_send(token, chat_id, sample)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    token = sys.argv[2]
    if cmd == "token":
        cmd_token(token)
    elif cmd == "chats":
        cmd_chats(token)
    elif cmd == "send" and len(sys.argv) >= 5:
        cmd_send(token, sys.argv[3], sys.argv[4])
    elif cmd == "test" and len(sys.argv) >= 4:
        cmd_test(token, sys.argv[3])
    else:
        print(__doc__)
