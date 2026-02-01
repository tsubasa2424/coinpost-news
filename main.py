# main.py
# CoinPost → AI要約（GPT-4o-mini） → SQLite保存 → / で HTML 表示

import os
import feedparser
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from selectolax.parser import HTMLParser
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from openai import OpenAI

# -----------------------------
# 設定
# -----------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

DATABASE_URL = "sqlite:///news.db"
COINPOST_RSS = "https://coinpost.jp/?feed=rss2"

# -----------------------------
# HTMLテンプレート設定
# -----------------------------
templates = Jinja2Templates(directory="templates")

# -----------------------------
# DB設定
# -----------------------------
engine = create_engine(DATABASE_URL, echo=False, future=True)
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

# -----------------------------
# AI要約
# -----------------------------
def summarize(text: str) -> str:
    prompt = f"""
以下のCoinPostニュースを300字以内で重要ポイントだけ日本語で要約してください。

{text}
"""
    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return res.choices[0].message["content"]


# -----------------------------
# CoinPost記事本文抽出
# -----------------------------
async def fetch_article(url: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=15)
        html = HTMLParser(r.text)
        paragraphs = [p.text().strip() for p in html.css("p")]
        return "\n".join(paragraphs)


# -----------------------------
# RSS → 要約して保存
# -----------------------------
async def update_coinpost():
    db = SessionLocal()
    rss = feedparser.parse(COINPOST_RSS)

    for entry in rss.entries[:5]:
        if db.query(News).filter(News.url == entry.link).first():
            continue

        article_text = await fetch_article(entry.link)
        summary = summarize(article_text)

        item = News(
            title=entry.title,
            url=entry.link,
            summary=summary,
        )
        db.add(item)
        db.commit()

    db.close()


# -----------------------------
# FastAPI
# -----------------------------
app = FastAPI()


# -----------------------------
# ★ トップページを HTML に変更
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    db = SessionLocal()
    rows = db.query(News).order_by(News.id.desc()).all()
    db.close()

    return templates.TemplateResponse(
        "news.html",
        {"request": request, "news_list": rows}
    )


# -----------------------------
# 手動更新API（調整に使用）
# -----------------------------
@app.get("/update")
async def update():
    await update_coinpost()
    return {"status": "updated"}


# -----------------------------
# JSON版（必要なら残す）
# -----------------------------
@app.get("/news")
def get_news_json():
    db = SessionLocal()
    rows = db.query(News).order_by(News.id.desc()).all()
    db.close()

    return [
        {
            "title": n.title,
            "url": n.url,
            "summary": n.summary,
            "created_at": n.created_at,
        }
        for n in rows
    ]
