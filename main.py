# main.py
# Gemini (Google AI) 版：ニュースを自動取得→要約→表示
# OpenAI不要・無料枠で動作

import os
import feedparser
import httpx
from fastapi import FastAPI
from selectolax.parser import HTMLParser
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import google.generativeai as genai

# ========= Google Gemini 設定 =========
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
MODEL = genai.GenerativeModel("gemini-1.5-flash")  # 無料で高速

# ========= 設定 =========
NEWS_RSS = "https://coinpost.jp/?feed=rss2"
DATABASE_URL = "sqlite:///news.db"

# ========= DB =========
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

# ========= 要約（Gemini） =========
def summarize_with_gemini(text: str) -> str:
    prompt = f"""
以下のニュース記事を300字以内で分かりやすく日本語要約してください。

{text}
"""
    response = MODEL.generate_content(prompt)
    return response.text


# ========= 本文抽出 =========
async def fetch_article(url: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10)
        html = HTMLParser(r.text)
        paragraphs = [p.text().strip() for p in html.css("p")]
        return "\n".join(paragraphs)


# ========= ニュース取得と要約 =========
async def update_news():
    db = SessionLocal()
    rss = feedparser.parse(NEWS_RSS)

    for entry in rss.entries[:5]:  # 最新5件
        exists = db.query(News).filter(News.url == entry.link).first()
        if exists:
            continue

        text = await fetch_article(entry.link)
        summary = summarize_with_gemini(text)

        news = News(
            title=entry.title,
            url=entry.link,
            summary=summary,
        )
        db.add(news)
        db.commit()

    db.close()


# ========= FastAPI =========
app = FastAPI()

@app.get("/")
async def auto_news():
    """アクセスした瞬間に自動で要約を生成し、そのまま返す"""
    await update_news()

    db = SessionLocal()
    rows = db.query(News).order_by(News.id.desc()).limit(5).all()
    db.close()

    return [
        {
            "title": r.title,
            "summary": r.summary,
            "url": r.url,
            "created_at": r.created_at,
        }
        for r in rows
    ]
