import os
import feedparser
import httpx
from fastapi import FastAPI, Query
from selectolax.parser import HTMLParser
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import google.generativeai as genai
from linebot import LineBotApi
from linebot.models import TextSendMessage

# ========== Gemini ==========
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
MODEL = genai.GenerativeModel("gemini-pro")

# ========== LINE ==========
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)

# ========== DB ==========
DATABASE_URL = "sqlite:///news.db"
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True)
    word = Column(String, unique=True)

class News(Base):
    __tablename__ = "news"
    id = Column(Integer, primary_key=True)
    url = Column(String, unique=True)  # 既読管理用
    title = Column(String)
    summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ========== 定数 ==========
COINPOST_RSS = "https://coinpost.jp/?feed=rss2"

# ========== Utils ==========
def send_line_message(text):
    line_bot_api.push_message(
        to=LINE_USER_ID,
        messages=TextSendMessage(text=text)
    )

async def fetch_article(url):
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10)
        html = HTMLParser(r.text)
        paragraphs = [p.text().strip() for p in html.css("p")]
        return "\n".join(paragraphs)

def summarize(text):
    prompt = f"""
以下のCoinPostニュースを300字以内で要約してください：
{text}
"""
    res = MODEL.generate_content(prompt)
    return res.text

# ========== FastAPI ==========
app = FastAPI()

@app.get("/")
def home():
    return {
        "message": "CoinPost 自動監視ニュースAI",
        "usage": "キーワード登録: /add?word=ビットコイン"
    }

# --------------------
# 📌 ① キーワード登録API
# --------------------
@app.get("/add")
def add_keyword(word: str):
    db = SessionLocal()

    if db.query(Keyword).filter_by(word=word).first():
        db.close()
        return {"status": "already_exists"}

    db.add(Keyword(word=word))
    db.commit()
    db.close()

    return {"status": "added", "word": word}

# --------------------
# 📌 ② 登録したキーワード一覧
# --------------------
@app.get("/keywords")
def list_keywords():
    db = SessionLocal()
    rows = db.query(Keyword).all()
    db.close()
    return [k.word for k in rows]

# --------------------
# 📌 ③ Cron Job用：自動監視API
# --------------------
@app.get("/auto-check")
async def auto_check():
    """
    Render の Cron Job から毎10分実行される。
    新着ニュースを監視し、キーワードに一致すれば通知。
    """
    db = SessionLocal()
    rss = feedparser.parse(COINPOST_RSS)

    keywords = [k.word for k in db.query(Keyword).all()]
    if not keywords:
        db.close()
        return {"status": "no_keywords"}

    new_hits = []

    for entry in rss.entries[:20]:
        # 既に処理済みの記事ならスキップ
        if db.query(News).filter_by(url=entry.link).first():
            continue

        # 記事本文取得
        article_text = await fetch_article(entry.link)

        # キーワード一致判定（タイトル or 本文）
        for kw in keywords:
            if kw in entry.title or kw in article_text:
                summary = summarize(article_text)

                # DBへ保存（再通知防止）
                db.add(News(url=entry.link, title=entry.title, summary=summary))
                db.commit()

                # LINE通知
                msg = f"""
🔍 キーワード一致: 「{kw}」

📰 {entry.title}

📘 要約:
{summary}

🔗 {entry.link}
"""
                send_line_message(msg)

                new_hits.append(entry.title)

    db.close()

    return {"status": "done", "hits": new_hits}
