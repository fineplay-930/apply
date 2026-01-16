from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
import os
import pandas as pd
import requests


@app.get("/")
def root():
    return {"ok": True, "service": "fineplay-apply"}

@app.get("/health")
def health():
    return {"ok": True}

app = FastAPI()

# CORS: 프론트(별도 도메인)에서 호출 가능하게
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 배포 후엔 프론트 도메인으로 좁히는 걸 추천
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

def sendgrid_send_email(to_email: str, subject: str, content: str, attachment_path: str):
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("SENDGRID_FROM_EMAIL", "no-reply@fineplay.kr")
    if not api_key:
        raise RuntimeError("SENDGRID_API_KEY is not set")

    import base64
    with open(attachment_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email},
        "subject": subject,
        "content": [{"type": "text/plain", "value": content}],
        "attachments": [{
            "content": encoded,
            "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": os.path.basename(attachment_path),
            "disposition": "attachment"
        }]
    }

    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=20
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

    # 파일명
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_home = (data.home_team or "HOME").replace("/", "_")
    filename = f"fineplay_application_{safe_home}_{ts}.xlsx"

    # Sheet1: 신청 요약 (1행)
    summary = pd.DataFrame([{
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
    }])

    # Sheet2: 선수 목록 (여러 행)
    player_rows = []
    for p in data.players:
        player_rows.append({"type": "starter", "name": p.name, "position": p.position, "number": p.number})
    for p in data.substitutes:
        player_rows.append({"type": "sub", "name": p.name, "position": p.position, "number": p.number})
    players_df = pd.DataFrame(player_rows)

    # 엑셀 저장
    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="summary")
        players_df.to_excel(writer, index=False, sheet_name="players")

    # 이메일 전송
    to_email = os.environ.get("OPS_EMAIL", "official@fineplay.kr")
    sendgrid_send_email(
        to_email=to_email,
        subject="[Fine Play] 신규 분석 신청 접수",
        content="신규 분석 신청이 접수되었습니다. 첨부된 엑셀 파일을 확인해주세요.",
        attachment_path=filename
    )

    return {"status": "ok", "sent_to": to_email}
