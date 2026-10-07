#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_x.py —— 给一条 X/Twitter 链接，回一条能直接进稿的素材笔记。

做的事只有三件，按顺序：

1. 跑官方 oembed —— 确认这条推存在、作者是谁、哪天发的
2. 跑第三方镜像 fxtwitter —— 取回逐字全文和五项互动数据
3. 对比两次结果 —— 官方接口会把长推截断，这里把截断明确告诉你

然后按统一格式输出一条 markdown 笔记（stdout 或 -o 落盘）。

用法：
    python fetch_x.py https://x.com/karpathy/status/2105819303471976479
    python fetch_x.py <url> -o research/06-primary-sources.md

依赖：Python 3.8+ 标准库，不需要 pip install 任何东西。

已知边界（诚实写在最前面，不用的人才不会踩坑）：
- fxtwitter 是第三方公共镜像，随时可能停服或限流。它挂了就退回第 1 步的 oembed，
  但那时只能确认存在性，取不到全文 —— 这时候该怎么办，见 README 的降级链。
- 只处理公开推文。需要登录才能看的内容，本脚本不会尝试，也不该尝试。
- 国内平台（微博/公众号）不走这条路，没有可用的公开结构化接口，本脚本不覆盖。
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

OEMBED = "https://publish.twitter.com/oembed?url={url}&omit_script=1"
MIRROR = "https://api.fxtwitter.com/{user}/status/{sid}"
UA = "primary-source-fetch/1.0 (+https://github.com/TaurenRen/ai-leader-primary-sources)"
TIMEOUT = 30

STATUS_RE = re.compile(
    r"(?:x|twitter)\.com/(?P<user>[A-Za-z0-9_]+)/status/(?P<sid>\d+)"
)


def parse_target(raw):
    """接受完整 URL、裸 ID，或 'user/status/id' 形式的简写。"""
    raw = raw.strip()
    if raw.isdigit():
        return None, raw
    m = STATUS_RE.search(raw)
    if m:
        return m.group("user"), m.group("sid")
    if "/" in raw:
        parts = [p for p in raw.split("/") if p]
        if len(parts) >= 2 and parts[-1].isdigit():
            return parts[-2], parts[-1]
    raise SystemExit(
        "认不出这条链接。给我下面任意一种：\n"
        "  https://x.com/someone/status/1234567890\n"
        "  someone/status/1234567890\n"
        "  1234567890（只有 ID 时，第 2 步会失败，第 1 步需要完整 URL）"
    )


def http_get(url, expect="text"):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, len(body), body


def step_oembed(url):
    """第 1 步：官方接口，确认存在性。**它返回的正文会被截断。**"""
    try:
        status, size, body = http_get(OEMBED.format(url=urllib.parse.quote(url, safe="")))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP %s（这条推可能已删除或不可见）" % e.code}
    except Exception as e:  # 网络层问题
        return {"ok": False, "error": str(e)}

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "error": "返回的不是 JSON"}

    html = data.get("html", "")
    snippet = re.sub(r"<[^>]+>", "", html)
    snippet = (snippet.replace("&amp;", "&").replace("&#39;", "'")
                      .replace("&quot;", '"').replace("&mdash;", "—"))
    snippet = re.sub(r"\s+", " ", snippet).strip()
    snippet = re.sub(r"—\s*\S+\s*\(@\w+\).*$", "", snippet).strip()
    trailing_tco = re.search(r"(https?://t\.co/\w+|pic\.twitter\.com/\w+)\s*$", snippet)
    if trailing_tco:
        snippet = snippet[: trailing_tco.start()].strip()

    return {
        "ok": True,
        "http": status,
        "bytes": size,
        "author": data.get("author_name", ""),
        "url": data.get("url", url),
        "date_text": (re.search(r">([A-Z][a-z]+ \d{1,2}, \d{4})</a>", html).group(1)
                      if re.search(r">([A-Z][a-z]+ \d{1,2}, \d{4})</a>", html) else ""),
        "snippet": snippet,
    }


def step_mirror(user, sid):
    """第 2 步：第三方镜像，取全文 + 时间戳 + 互动数据。"""
    if not user or not sid:
        return {"ok": False, "error": "缺 user 或 status id，无法调用镜像"}
    try:
        _, size, body = http_get(MIRROR.format(user=user, sid=sid))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP %s（镜像可能已停服或被限流）" % e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "error": "镜像返回的不是 JSON"}

    tw = data.get("tweet") or {}
    if not tw.get("text"):
        return {"ok": False, "error": "镜像没给正文，可能已失效"}

    created = tw.get("created_at", "")
    stamps = []
    if created:
        try:
            dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
            local = dt.astimezone(timezone(timedelta(hours=8)))
            stamps = [dt.strftime("%Y-%m-%d %H:%M %Z"),
                      "北京时间 " + local.strftime("%Y-%m-%d %H:%M")]
        except ValueError:
            stamps = [created]

    return {
        "ok": True,
        "bytes": size,
        "author": tw.get("author", {}).get("name", ""),
        "handle": tw.get("author", {}).get("screen_name", user),
        "text": tw["text"],
        "created_at": created,
        "timestamps": stamps,
        "stats": {k: tw.get(k) for k in
                  ("views", "bookmarks", "likes", "retweets", "replies")},
    }


def build_note(url, head, full):
    """把两次抓取的结果拼成一条规范素材笔记。"""
    lines = []
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    # 用 oembed 的片段长度 vs 镜像全文长度判断截断，这是这个脚本真正的价值所在
    if head.get("ok") and full.get("ok"):
        snip, whole = len(head["snippet"]), len(full["text"])
        truncated = whole - snip > 40
    else:
        snip, whole, truncated = 0, 0, False

    lines.append("# 一手源素材（自动采集）\n")
    lines.append("> 采集时刻：%s（GMT+8）  " % now)
    lines.append("> 采集方式：官方 oembed + 第三方镜像 fxtwitter，双路径交叉\n")

    lines.append("## 基本信息\n")
    who = full.get("author") or head.get("author") or "?"
    lines.append("人物：%s" % who)
    lines.append("渠道：X / Twitter")
    lines.append("原帖 URL：%s" % url)
    if full.get("timestamps"):
        lines.append("发布时间：%s" % " / ".join(full["timestamps"]))
    elif head.get("date_text"):
        lines.append("发布时间：%s（仅官方接口给出的日期）" % head["date_text"])
    else:
        lines.append("发布时间：待补")

    if full.get("ok"):
        s = full["stats"]
        stat_line = " / ".join(
            "%s %s" % (k, v) for k, v in s.items() if v is not None)
        lines.append("互动数据：%s" % (stat_line if stat_line else "无"))
    lines.append("")

    lines.append("## 原话（逐字）\n")
    if full.get("ok"):
        lines.append("```")
        lines.append(full["text"].strip())
        lines.append("```\n")
    elif head.get("ok"):
        lines.append("> ⚠️ 只取到被截断的片段，**不能当引用源**：\n")
        lines.append("```")
        lines.append(head["snippet"])
        lines.append("```\n")

    lines.append("## 采集路径实测\n")
    lines.append("| 路径 | HTTP | 返回字节 | 拿到什么 |")
    lines.append("|---|---|---|---|")
    if head.get("ok"):
        lines.append("| 官方 oembed | %s | %s | 存在性 + 日期，正文截断至 %s 字符 |"
                     % (head["http"], head["bytes"], snip))
    else:
        lines.append("| 官方 oembed | — | — | 失败：%s |" % head.get("error", ""))
    if full.get("ok"):
        lines.append("| fxtwitter 镜像 | 200 | %s | 逐字全文 %s 字符 + 时间戳 + 互动数据 |"
                     % (full["bytes"], whole))
    else:
        lines.append("| fxtwitter 镜像 | — | — | 失败：%s |" % full.get("error", ""))
    lines.append("")
    if truncated:
        lines.append("⚠️ **官方接口把这条推截断了 %s 个字符**。"
                     "只跑第 1 步就写稿，等于在不知情的情况下替作者补全后半句。\n"
                     % (whole - snip))
    if not full.get("ok"):
        lines.append("⚠️ 镜像没给全文。按降级链处理：找带引号的权威媒体转引并注明，"
                     "或者把这条降级为线索而不是引用源。\n")

    lines.append("## 待你填（脚本不会替你写）\n")
    lines.append("核心主张：")
    lines.append("")
    lines.append("可截图化：是 / 否 / 需补背景")
    lines.append("")
    lines.append("我的加工：")
    lines.append("")
    lines.append("> 「我的加工」是必填项。只搬运原话没有价值，"
                 "文章的分量在于你把它接回到读者的具体麻烦上。\n")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="给一条 X/Twitter 链接，回一条能直接进稿的素材笔记。")
    ap.add_argument("target", help="推文 URL、 user/status/id，或纯 status id")
    ap.add_argument("-o", "--output", help="写进文件而不是直接打印")
    ap.add_argument("-q", "--quiet", action="store_true", help="不打印进度")
    args = ap.parse_args()

    user, sid = parse_target(args.target)
    url = ("https://x.com/%s/status/%s" % (user, sid)) if user and sid else args.target

    if not args.quiet:
        print("[1/3] 官方 oembed：确认存在性…", file=sys.stderr)
    head = step_oembed(url)

    if not args.quiet:
        print("[2/3] 第三方镜像：取全文…", file=sys.stderr)
    full = step_mirror(user, sid)

    if not args.quiet:
        print("[3/3] 拼素材笔记…", file=sys.stderr)
    note = build_note(url, head, full)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(note)
        print("已写入 %s" % args.output, file=sys.stderr)
    else:
        print(note)

    sys.exit(0 if full.get("ok") else 2)


if __name__ == "__main__":
    main()
