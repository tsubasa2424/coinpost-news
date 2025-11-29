# main.py
# ニュース自動取得 → GPT要約 → 保存 → 配信（Render/Railway対応）

import os
import feedparser
import httpx
from fastapi import FastAPI
from selectolax.parser import HTMLParser
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
from openai import OpenAI

# --------------------------
# 設定
# --------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

NEWS_RSS = "https://coinpost.jp/?feed=rss2"

DATABASE_URL = "sqlite:///news.db"

# --------------------------
# DB（SQLite）
# --------------------------
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class News(Base):
    __tablename__ = "news"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    url = Column(String, unique=True)
    summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# --------------------------
# 要約関数（GPT）
# --------------------------
def summarize(text: str) -> str:
    prompt = f"""
以下のニュースを300字以内で重要部分だけ日本語で要約してください：

{text}
"""
    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return res.choices[0].message['content']

# --------------------------
# 記事本文抽出
# --------------------------
async def fetch_article(url: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10)
        html = HTMLParser(r.text)
        paragraphs = [p.text().strip() for p in html.css("p")]
        return "\n".join(paragraphs)

# --------------------------
# ニュース更新処理
# --------------------------
async def update_news_data():
    db = SessionLocal()
    rss = feedparser.parse(NEWS_RSS)

    for entry in rss.entries[:5]:  # 最新5件
        exists = db.query(News).filter(News.url == entry.link).first()
        if exists:
            continue

        text = await fetch_article(entry.link)
        summary = summarize(text)

        news = News(
            title=entry.title,
            url=entry.link,
            summary=summary
        )
        db.add(news)
        db.commit()

    db.close()

# --------------------------
# LINE通知（任意）
# --------------------------
async def notify_line(message: str):
    token = os.getenv("LINE_NOTIFY_TOKEN")
    if not token:
        return

    url = "https://notify-api.line.me/api/notify"
    headers = {"Authorization": f"Bearer {token}"}
    data = {"message": message}

    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, data=data)

# --------------------------
# FastAPI（Public）
# --------------------------
app = FastAPI()

@app.get("/")
def home():
    return {"message": "News Auto Summarizer API (GPT-4o-mini)"}

@app.get("/update")
async def update():
    await update_news_data()
    return {"status": "updated"}

@app.get("/news")
def get_news():
    db = SessionLocal()
    rows = db.query(News).order_by(News.id.desc()).all()
    db.close()
    return [
        {
            "title": r.title,
            "url": r.url,
            "summary": r.summary,
            "created_at": r.created_at,
        }
        for r in rows
    ]

@app.get("/notify")
async def notify():
    db = SessionLocal()
    rows = db.query(News).order_by(News.id.desc()).limit(5).all()
    db.close()

    msg = "📰【最新ニュース要約】\n\n"
    for n in rows:
        msg += f"■ {n.title}\n{n.summary}\n{n.url}\n\n"

    await notify_line(msg)
    return {"status": "sent"}
