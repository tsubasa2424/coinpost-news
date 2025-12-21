# main.py
# URLを開いた瞬間にニュースを自動要約して表示するバージョン

import os
import feedparser
import httpx
from fastapi import FastAPI
from selectolax.parser import HTMLParser
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

NEWS_RSS = "https://coinpost.jp/?feed=rss2"
DATABASE_URL = "sqlite:///news.db"

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


def summarize(text: str) -> str:
    prompt = f"""
以下のニュースを300字以内で重要部分だけ日本語で要約してください：

{text}
"""
    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return res.choices[0].message["content"]


async def fetch_article(url: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10)
        html = HTMLParser(r.text)
        paragraphs = [p.text().strip() for p in html.css("p")]
        return "\n".join(paragraphs)


async def update_news_data():
    db = SessionLocal()
    rss = feedparser.parse(NEWS_RSS)

    for entry in rss.entries[:5]:
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


# ======================
# FastAPI
# ======================
app = FastAPI()


@app.get("/")
async def home():
    # ① 開いた瞬間にニュースを更新
    await update_news_data()

    # ② 最新ニュースを DB から取得してそのまま返す
    db = SessionLocal()
    rows = db.query(News).order_by(News.id.desc()).limit(5).all()
    db.close()

    return [
        {
            "title": r.title,
            "summary": r.summary,
            "url": r.url,
            "created_at": r.created_at
        }
        for r in rows
    ]
