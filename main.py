from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
import os
import pandas as pd
import requests
import io
import base64

app = FastAPI()

# CORS: 프론트(별도 도메인)에서 호출 가능하게
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 배포 후엔 프론트 도메인으로 좁히는 걸 추천
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"ok": True, "service": "fineplay-apply"}

@app.get("/health")
def health():
    return {"ok": True}

class Player(BaseModel):
    name: str
    position: str
    number: str

class Application(BaseModel):
    plan: str
    match_date: str
    kickoff_time: str
    location: str
    home_team: str
    away_team: str
    representative_name: Optional[str] = ""
    representative_contact: Optional[str] = ""
    video_url_1: str
    video_url_2: Optional[str] = ""
    formation: str
    players: List[Player] = Field(default_factory=list)
    substitutes: List[Player] = Field(default_factory=list)

def sendgrid_send_email(to_email: str, subject: str, content: str, attachments: list):
    """Send email via SendGrid with multiple attachments.

    attachments: list of dicts with keys: filename (str), data (bytes), type (mime type)
    """
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("SENDGRID_FROM_EMAIL", "no-reply@fineplay.kr")
    if not api_key:
        raise RuntimeError("SENDGRID_API_KEY is not set")

    sg_attachments = []
    for att in attachments:
        encoded = base64.b64encode(att["data"]).decode("utf-8")
        sg_attachments.append({
            "content": encoded,
            "type": att.get("type", "application/octet-stream"),
            "filename": att.get("filename", "attachment"),
            "disposition": "attachment",
        })

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email},
        "subject": subject,
        "content": [{"type": "text/plain", "value": content}],
        "attachments": sg_attachments,
    }

    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    if r.status_code not in (200, 202):
        raise RuntimeError(f"SendGrid error: {r.status_code} {r.text}")

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/submit-application")
def submit_application(data: Application):
    # 최소 요구조건(원하시는 정책에 맞춰 조정 가능)
    total_players = len(data.players) + len(data.substitutes)
    if total_players < 11:
        raise HTTPException(status_code=400, detail="최소 11명(선발) 입력이 필요합니다.")

    # 파일명 및 CSV 생성 (Excel 대신 CSV로 전송하여 속도 개선)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_home = (data.home_team or "HOME").replace("/", "_")
    summary_filename = f"fineplay_application_summary_{safe_home}_{ts}.csv"
    players_filename = f"fineplay_application_players_{safe_home}_{ts}.csv"

    # Summary CSV
    summary_df = pd.DataFrame([
        {
            "created_at_utc": ts,
            "plan": data.plan,
            "match_date": data.match_date,
            "kickoff_time": data.kickoff_time,
            "location": data.location,
            "home_team": data.home_team,
            "away_team": data.away_team,
            "representative_name": data.representative_name,
            "representative_contact": data.representative_contact,
            "video_url_1": data.video_url_1,
            "video_url_2": data.video_url_2,
            "formation": data.formation,
            "players_count": len(data.players),
            "substitutes_count": len(data.substitutes),
            "total_count": total_players,
        }
    ])

    # Players CSV
    player_rows = []
    for p in data.players:
        player_rows.append({"type": "starter", "name": p.name, "position": p.position, "number": p.number})
    for p in data.substitutes:
        player_rows.append({"type": "sub", "name": p.name, "position": p.position, "number": p.number})
    players_df = pd.DataFrame(player_rows)

    # Convert to CSV bytes (UTF-8 with BOM for Excel compatibility)
    summary_csv = summary_df.to_csv(index=False, encoding="utf-8-sig")
    players_csv = players_df.to_csv(index=False, encoding="utf-8-sig")
    summary_bytes = summary_csv.encode("utf-8-sig")
    players_bytes = players_csv.encode("utf-8-sig")

    # 이메일 전송 (두 개의 CSV 첨부)
    to_email = os.environ.get("OPS_EMAIL", "official@fineplay.kr")
    attachments = [
        {"filename": summary_filename, "data": summary_bytes, "type": "text/csv"},
        {"filename": players_filename, "data": players_bytes, "type": "text/csv"},
    ]

    sendgrid_send_email(
        to_email=to_email,
        subject="[Fine Play] 신규 분석 신청 접수",
        content="신규 분석 신청이 접수되었습니다. 첨부된 CSV 파일을 확인해주세요.",
        attachments=attachments,
    )

    return {"status": "ok", "sent_to": to_email}
