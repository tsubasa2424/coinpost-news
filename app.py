"""
仮想通貨ニュース LINE通知 - Web管理アプリ (Flask バックエンド)

必要なライブラリ:
    pip install flask requests feedparser apscheduler

環境変数:
    LINE_CHANNEL_ACCESS_TOKEN  : LINEチャネルアクセストークン
    LINE_USER_ID               : 送信先ユーザーID (Uxxx...)
    SECRET_KEY                 : Flaskセッション用シークレットキー（任意）

起動方法:
    export LINE_CHANNEL_ACCESS_TOKEN="your_token"
    export LINE_USER_ID="Uxxxxxxxxxxxxxxxxxxxx"
    python app.py

    ブラウザで http://localhost:5000 を開く
    スマホからアクセスする場合は同じWi-Fi上で http://<PCのIPアドレス>:5000

本番運用 (Renderなどのクラウドへのデプロイ推奨):
    https://render.com でWebサービスとしてデプロイすると24時間稼働できます
"""

import os
import re
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path

import feedparser
import requests
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

# ============================================================
# Flask アプリ設定
# ============================================================

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "crypto-news-secret-2024")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# 設定
# ============================================================

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")
LINE_API_URL = "https://api.line.me/v2/bot/message/push"

RSS_FEEDS = [
    {"name": "CoinPost",           "url": "https://coinpost.jp/?feed=rss2"},
    {"name": "CoinDesk Japan",     "url": "https://www.coindeskjapan.com/feed/"},
    {"name": "CoinDesk (English)", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
]

MAX_ARTICLES_PER_SOURCE = 5
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
KEYWORDS_FILE      = DATA_DIR / "keywords.json"
SENT_ARTICLES_FILE = DATA_DIR / "sent_articles.json"
LOGS_FILE          = DATA_DIR / "logs.json"

# ============================================================
# データ管理
# ============================================================

def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_keywords() -> list:
    return load_json(KEYWORDS_FILE, [])


def save_keywords(keywords: list):
    save_json(KEYWORDS_FILE, keywords)


def load_sent_articles() -> set:
    return set(load_json(SENT_ARTICLES_FILE, []))


def save_sent_articles(sent: set):
    save_json(SENT_ARTICLES_FILE, list(sent)[-1000:])


def load_logs() -> list:
    return load_json(LOGS_FILE, [])


def add_log(message: str, level: str = "info"):
    logs = load_logs()
    logs.append({
        "time": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
        "level": level,
        "message": message,
    })
    save_json(LOGS_FILE, logs[-100:])  # 最新100件保持

# ============================================================
# ニュース取得・フィルタ
# ============================================================

def article_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def fetch_articles(feed_info: dict) -> list:
    try:
        feed = feedparser.parse(feed_info["url"])
        articles = []
        for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
            summary = re.sub(r"<[^>]+>", "", entry.get("summary", "")).strip()[:120]
            articles.append({
                "source":  feed_info["name"],
                "title":   entry.get("title", ""),
                "url":     entry.get("link", ""),
                "summary": summary,
            })
        return articles
    except Exception as e:
        logger.error(f"{feed_info['name']} 取得失敗: {e}")
        return []


def filter_by_keywords(articles: list, keywords: list) -> list:
    result = []
    for article in articles:
        text = article["title"] + " " + article["summary"]
        for kw in keywords:
            if kw.lower() in text.lower():
                result.append((kw, article))
                break
    return result

# ============================================================
# LINE 送信
# ============================================================

def send_line_message(messages: list) -> bool:
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        logger.warning("LINE認証情報が未設定")
        return False

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {"to": LINE_USER_ID, "messages": messages}
    try:
        resp = requests.post(LINE_API_URL, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"LINE送信失敗: {resp.status_code} {resp.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE送信エラー: {e}")
        return False


def build_flex_message(keyword: str, article: dict) -> dict:
    body_contents = [
        {"type": "text", "text": article["title"], "wrap": True, "weight": "bold", "size": "md", "color": "#1A1A2E"},
    ]
    if article["summary"]:
        body_contents.append({
            "type": "text", "text": article["summary"] + "...",
            "wrap": True, "size": "sm", "color": "#555555", "margin": "md",
        })
    body_contents.append({
        "type": "text", "text": f"🔑 キーワード: {keyword}",
        "size": "xs", "color": "#0F3460", "margin": "md", "weight": "bold",
    })

    return {
        "type": "flex",
        "altText": f"【{keyword}】{article['title']}",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box", "layout": "vertical",
                "contents": [{"type": "text", "text": f"📰 {article['source']}", "size": "sm", "color": "#ffffff", "weight": "bold"}],
                "backgroundColor": "#1A1A2E", "paddingAll": "12px",
            },
            "body": {
                "type": "box", "layout": "vertical",
                "contents": body_contents, "paddingAll": "16px",
            },
            "footer": {
                "type": "box", "layout": "vertical",
                "contents": [{"type": "button", "action": {"type": "uri", "label": "記事を読む", "uri": article["url"]}, "style": "primary", "color": "#0F3460"}],
                "paddingAll": "12px",
            },
        },
    }

# ============================================================
# ニュースチェック（スケジューラ & API共用）
# ============================================================

def run_news_check() -> dict:
    """ニュースをチェックしてLINE通知。結果をdictで返す。"""
    logger.info("ニュースチェック開始")
    keywords = load_keywords()

    if not keywords:
        msg = "キーワードが未設定です"
        add_log(msg, "warning")
        return {"success": False, "message": msg, "sent": 0}

    sent_articles = load_sent_articles()
    matched = []

    for feed_info in RSS_FEEDS:
        articles = fetch_articles(feed_info)
        for kw, article in filter_by_keywords(articles, keywords):
            if article_id(article["url"]) not in sent_articles:
                matched.append((kw, article))

    if not matched:
        msg = "新着記事なし"
        add_log(msg)
        return {"success": True, "message": msg, "sent": 0}

    # ヘッダー通知
    kw_summary = ", ".join(dict.fromkeys(kw for kw, _ in matched))
    header_text = (
        f"🚀 キーワードニュース {len(matched)}件\n"
        f"🔑 {kw_summary}\n"
        f"{datetime.now().strftime('%Y/%m/%d %H:%M')}"
    )
    send_line_message([{"type": "text", "text": header_text}])

    # 記事を送信（最大5件/リクエスト）
    sent_ids = []
    for i in range(0, len(matched), 5):
        batch = matched[i:i + 5]
        messages = [build_flex_message(kw, art) for kw, art in batch]
        if send_line_message(messages):
            for kw, art in batch:
                sent_ids.append(article_id(art["url"]))

    for aid in sent_ids:
        sent_articles.add(aid)
    save_sent_articles(sent_articles)

    msg = f"{len(sent_ids)}件の記事を通知しました（キーワード: {kw_summary}）"
    add_log(msg)
    logger.info(msg)
    return {"success": True, "message": msg, "sent": len(sent_ids)}

# ============================================================
# スケジューラ（毎時自動チェック）
# ============================================================

scheduler = BackgroundScheduler()
scheduler.add_job(run_news_check, "interval", hours=1, id="news_check")
scheduler.start()

# ============================================================
# API エンドポイント
# ============================================================

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/keywords", methods=["GET"])
def get_keywords():
    return jsonify({"keywords": load_keywords()})


@app.route("/api/keywords", methods=["POST"])
def add_keywords():
    data = request.get_json()
    raw = data.get("keywords", "")
    new_kws = [kw.strip() for kw in raw.split(",") if kw.strip()]
    keywords = load_keywords()
    added = []
    for kw in new_kws:
        if kw and kw not in keywords:
            keywords.append(kw)
            added.append(kw)
    save_keywords(keywords)
    if added:
        add_log(f"キーワード追加: {', '.join(added)}")
    return jsonify({"keywords": keywords, "added": added})


@app.route("/api/keywords/<int:index>", methods=["DELETE"])
def delete_keyword(index):
    keywords = load_keywords()
    if 0 <= index < len(keywords):
        removed = keywords.pop(index)
        save_keywords(keywords)
        add_log(f"キーワード削除: {removed}")
        return jsonify({"keywords": keywords, "removed": removed})
    return jsonify({"error": "Invalid index"}), 400


@app.route("/api/check", methods=["POST"])
def check_now():
    result = run_news_check()
    return jsonify(result)


@app.route("/api/logs", methods=["GET"])
def get_logs():
    logs = load_logs()
    return jsonify({"logs": list(reversed(logs))})


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify({
        "line_configured": bool(LINE_CHANNEL_ACCESS_TOKEN and LINE_USER_ID),
        "keywords_count": len(load_keywords()),
        "next_check": scheduler.get_job("news_check").next_run_time.strftime("%Y/%m/%d %H:%M") if scheduler.get_job("news_check") else "N/A",
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
