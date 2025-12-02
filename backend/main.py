# main.py - Final corrected version (Use gemma3:12b)
import os
import json
import re
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import httpx
from jose import jwt
from dotenv import load_dotenv
from jose import JWTError, ExpiredSignatureError

# load .env (required)
load_dotenv()

# local project imports
from database import get_db_connection
from auth import create_access_token, SECRET_KEY, ALGORITHM
from weather import get_weather_summary_for_prompt

# App setup
app = FastAPI(title="KEN ASSISTANT API (Final Fixed)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------
# MODELS
# -----------------------------------------------------------

class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class ChatMessage(BaseModel):
    chat_id: int
    message: str

class ChatRename(BaseModel):
    chat_id: int
    title: str

class ChatDelete(BaseModel):
    chat_id: int


# -----------------------------------------------------------
# HELPERS: Location extraction, followups, auth
# -----------------------------------------------------------

def extract_location(query: str) -> Optional[str]:
    """
    Improved location extraction:
    - Supports 'weather delhi'
    - Supports 'weather in th delhi'
    - Supports 'delhi weather'
    - Supports typos and multiple-word city names
    - Fallback: last noun phrase in the sentence
    """
    if not query or not isinstance(query, str):
        return None

    q = query.strip().lower()

    # 1. Strong patterns first
    patterns = [
        r"weather in ([\w\s,.'-]+)",
        r"weather at ([\w\s,.'-]+)",
        r"temperature in ([\w\s,.'-]+)",
        r"forecast for ([\w\s,.'-]+)",
        r"what.*weather in ([\w\s,.'-]+)",
        r"how.*weather in ([\w\s,.'-]+)",
        r"current weather in ([\w\s,.'-]+)",
        r"what's the weather in ([\w\s,.'-]+)",
        r"weather ([\w\s,.'-]+)",
        r"temperature ([\w\s,.'-]+)"
    ]

    for p in patterns:
        m = re.search(p, q, re.IGNORECASE)
        if m:
            loc = m.group(1).strip()
            loc = re.sub(r"\b(the|at|in|for|near|th)\b", "", loc, flags=re.IGNORECASE).strip()
            if len(loc) > 2:
                return loc.title()

    # 2. Reverse pattern: "delhi weather", "mumbai temperature"
    rev = re.search(r"([\w\s,.'-]+)\s+(weather|temperature|forecast)", q)
    if rev:
        loc = rev.group(1).strip()
        loc = re.sub(r"\b(the|at|in|for|near|th)\b", "", loc, flags=re.IGNORECASE).strip()
        if len(loc) > 2:
            return loc.title()

    # 3. Fallback: last word(s) as possible location
    words = q.split()
    if len(words) >= 1:
        for size in [3, 2, 1]:
            if len(words) >= size:
                candidate = " ".join(words[-size:])
                candidate = re.sub(r"\b(the|at|in|for|near|th|is|present)\b", "", candidate).strip()
                if len(candidate) > 2:
                    return candidate.title()

    return None



def is_weather_followup(query: str) -> bool:
    if not query:
        return False
    q = query.lower()
    weather_terms = ["weather", "temperature", "forecast", "rain", "snow", "wind", "sunrise", "sunset"]
    followup_terms = ["tomorrow", "day after", "next", "again", "same place", "how about", "what about", "later", "today"]
    return any(w in q for w in weather_terms) or any(f in q for f in followup_terms)


def get_current_user_id(request: Request):
    """
    Resolve user id from cookie 'access_token' or Authorization header.
    Gives clear errors for: missing, invalid, or expired tokens.
    """
    # 1) Try cookie first
    token = request.cookies.get("access_token")

    # 2) Fallback: Authorization: Bearer <token>
    if not token:
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()

    # 3) If still no token → reject
    if not token or token in ("", "null", "undefined"):
        raise HTTPException(status_code=401, detail="Not authenticated: token missing")

    # 4) Decode JWT safely
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # 5) Extract user ID
    uid = payload.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token payload: missing 'sub'")

    # 6) Convert to int safely
    try:
        return int(uid)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid user id in token")



def generate_chat_title(text: str) -> str:
    if not text:
        return "New Chat"
    w = text.split()[:4]
    t = " ".join(w)
    return t if len(t) <= 30 else (t[:27] + "...")


# -----------------------------------------------------------
# AUTH ENDPOINTS (unchanged logic)
# -----------------------------------------------------------



@app.post("/signup")
async def signup(user: UserCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE email=%s OR username=%s", (user.email, user.username))
        if cursor.fetchone():
            raise HTTPException(400, "User already exists")

        cursor.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)",
            (user.username, user.email, user.password),
        )
        conn.commit()
        uid = cursor.lastrowid

        token = create_access_token(uid)

        resp = JSONResponse({"access_token": token})
        resp.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            secure=False,
            samesite="lax"
        )
        return resp
    finally:
        cursor.close()
        conn.close()


@app.post("/login")
async def login(user: UserLogin):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, password_hash FROM users WHERE email=%s", (user.email,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(401, "Invalid credentials")

        uid, pw = row
        if isinstance(pw, (bytes, bytearray)):
            pw = pw.decode()

        if user.password != pw:
            raise HTTPException(401, "Invalid credentials")

        token = create_access_token(uid)

        resp = JSONResponse({"access_token": token})
        resp.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            secure=False,
            samesite="lax"
        )
        return resp
    finally:
        cursor.close()
        conn.close()
        
@app.get("/protected")
async def protected(user_id: int = Depends(get_current_user_id)):
    return {"message": "Authenticated"}


# -----------------------------------------------------------
# CHAT CRUD (unchanged)
# -----------------------------------------------------------

@app.post("/chat/new")
async def new_chat(user_id: int = Depends(get_current_user_id)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO chat_titles (user_id, title) VALUES (%s, 'New Chat')", (user_id,))
        conn.commit()
        return {"chat_id": cursor.lastrowid}
    finally:
        cursor.close()
        conn.close()


@app.get("/chat/history-list")
async def history(user_id: int = Depends(get_current_user_id)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM chat_titles WHERE user_id=%s ORDER BY updated_at DESC", (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [{"id": r[0], "title": r[1]} for r in rows]


@app.get("/chat/{chat_id}")
async def get_chat(chat_id: int, user_id: int = Depends(get_current_user_id)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM chat_titles WHERE id=%s AND user_id=%s", (chat_id, user_id))
    title = cursor.fetchone()
    if not title:
        raise HTTPException(404, "Chat not found")
    cursor.execute("SELECT message, role FROM chats WHERE chat_id=%s ORDER BY created_at ASC", (chat_id,))
    msgs = [{"role": r[1], "content": r[0]} for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return {"title": title[0], "messages": msgs}


# -----------------------------------------------------------
# CHAT STREAM: Corrected WEATHER injection + persistence
# -----------------------------------------------------------

@app.post("/chat/stream")
async def stream(msg: ChatMessage, request: Request, user_id: int = Depends(get_current_user_id)):
    # Validate chat and fetch last_location
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, last_location FROM chat_titles WHERE id=%s AND user_id=%s", (msg.chat_id, user_id))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        raise HTTPException(404, "Chat not found")
    chat_id = row[0]
    chat_last_location = row[1]

    # Save user message
    cursor.execute("INSERT INTO chats (user_id, chat_id, message, role) VALUES (%s, %s, %s, 'user')",
                   (user_id, chat_id, msg.message))
    conn.commit()

    # Auto title update for first message
    cursor.execute("SELECT COUNT(*) FROM chats WHERE chat_id=%s AND role='user'", (chat_id,))
    if cursor.fetchone()[0] == 1:
        cursor.execute("UPDATE chat_titles SET title=%s WHERE id=%s", (generate_chat_title(msg.message), chat_id))
        conn.commit()

    # Load history (we'll inject weather before passing to LLM)
    cursor.execute("SELECT message, role FROM chats WHERE chat_id=%s ORDER BY created_at ASC", (chat_id,))
    history = cursor.fetchall()

    # Weather handling: extract location or use persisted last_location on follow-ups
    location = extract_location(msg.message)
    if not location and is_weather_followup(msg.message):
        location = chat_last_location

    weather_summary = None
    weather_packet = None
    if location:
        try:
            res = get_weather_summary_for_prompt(location, forecast_days=5)
            if res:
                weather_summary = res.get("summary")
                weather_packet = res.get("packet")
                # persist last_location (store canonical location string)
                try:
                    cursor.execute("UPDATE chat_titles SET last_location=%s WHERE id=%s", (location, chat_id))
                    conn.commit()
                except Exception:
                    # ignore write errors but continue (we still have weather)
                    pass
        except Exception:
            # If weather engine fails, set None and proceed so model sees no weather
            weather_summary = None
            weather_packet = None

    # close DB connection before streaming to free connections
    cursor.close()
    conn.close()

    # Build the strict system prompt (triple-quoted to avoid unterminated string issues)
    system_prompt = """
You are KEN ASSISTANT.

IMPORTANT RULES FOR WEATHER ANSWERS:
1. When WEATHER_PACKET_JSON is provided, you MUST use ONLY that data.
2. NEVER guess, assume, or fabricate any weather info.
3. NEVER use AccuWeather, Google, or any external sources.
4. ONLY use data explicitly given inside WEATHER_PACKET_JSON.
5. If something is missing in the packet, say "Weather data unavailable".
6. Do NOT invent dates, temperatures, humidity, wind speeds, or conditions.
7. Your weather response MUST be based strictly on WEATHER_PACKET_JSON and/or the human-readable Weather Summary.

Respond clearly, accurately, and in Markdown format.
"""

    # Build messages in the correct order:
    # 1) system prompt (rules)
    # 2) weather summary (human-friendly) and code-fenced JSON packet (if available)
    # 3) conversation history
    # 4) current user message
    messages = []
    messages.append({"role": "system", "content": system_prompt})

    if weather_summary and weather_packet:
        # Human readable summary
        messages.append({"role": "system", "content": f"Weather summary for {location}: {weather_summary}"})

        # Code-fenced JSON to make parsing reliable
        try:
            packet_json = json.dumps(weather_packet, indent=2, default=str)
        except Exception:
            packet_json = json.dumps(weather_packet, default=str)

        messages.append({
            "role": "system",
            "content": "WEATHER_PACKET_JSON:\n```json\n" + packet_json + "\n```"
        })

    # Append history (assistant + user messages)
    for m, r in history:
        messages.append({"role": r, "content": m})

    # Append current user message last
    messages.append({"role": "user", "content": msg.message})

    # Stream to local LLM (Ollama) and save assistant's output
    async def generate():
        full_response = ""
        cancelled = False

        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream(
                    "POST",
                    "http://localhost:11434/api/chat",
                    json={"model": "gemma3:12b", "messages": messages, "stream": True},
                ) as resp:

                    if resp.status_code != 200:
                        # Attempt to surface the model error if available
                        try:
                            txt = await resp.text()
                        except Exception:
                            txt = "Model error"
                        yield f"❌ LLM model error: {txt}"
                        return

                    async for chunk in resp.aiter_bytes():
                        if await request.is_disconnected():
                            cancelled = True
                            break

                        text = chunk.decode("utf-8", errors="ignore")
                        # Ollama streaming may give newline-delimited JSON lines
                        for line in text.split("\n"):
                            if not line.strip():
                                continue
                            try:
                                j = json.loads(line)
                                token = j.get("message", {}).get("content", "")
                                if token:
                                    full_response += token
                                    yield token
                            except Exception:
                                # Fallback: stream raw text if parsing fails
                                full_response += line
                                yield line

            except httpx.RequestError as e:
                yield f"Network error: {str(e)}"
                return
            except Exception as e:
                yield f"Unexpected error: {str(e)}"
                return

        # Save assistant response if not cancelled
        if not cancelled:
            conn2 = get_db_connection()
            cur2 = conn2.cursor()
            try:
                cur2.execute(
                    "INSERT INTO chats (user_id, chat_id, message, role) VALUES (%s, %s, %s, 'assistant')",
                    (user_id, msg.chat_id, full_response),
                )
                conn2.commit()
            except Exception:
                # swallow DB write errors
                pass
            finally:
                cur2.close()
                conn2.close()

    return StreamingResponse(generate(), media_type="text/plain")


# -----------------------------------------------------------
# CHAT RENAME / DELETE
# -----------------------------------------------------------

@app.post("/chat/rename")
async def rename(chat: ChatRename, user_id: int = Depends(get_current_user_id)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE chat_titles SET title=%s WHERE id=%s AND user_id=%s", (chat.title[:100], chat.chat_id, user_id))
    conn.commit()
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        raise HTTPException(404, "Chat not found")
    cursor.close()
    conn.close()
    return {"status": "ok"}


@app.post("/chat/delete")
async def delete(chat: ChatDelete, user_id: int = Depends(get_current_user_id)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM chat_titles WHERE id=%s AND user_id=%s", (chat.chat_id, user_id))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        raise HTTPException(404, "Chat not found")
    cursor.execute("DELETE FROM chat_titles WHERE id=%s", (chat.chat_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok"}


@app.post("/password-reset")
async def reset():
    return {"message": "OK"}

