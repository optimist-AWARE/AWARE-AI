import os
import io
import base64
import re
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from typing import List, Optional, Tuple
import openai
import httpx

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFilesff

from pydantic import BaseModel, Field
from PIL import Image

from google import genai
from google.genai import types


# -------------------------
# ENV / Client
# -------------------------
load_dotenv()
load_dotenv("AI.env")

API_KEY = os.getenv("GEMINI_API_KEY")
TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3-flash-preview")
# "나노바나나" 계열은 계정/프로젝트에 따라 모델명이 다를 수 있어 env로 바꿀 수 있게 둠
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
# 예: Gemini 3 Pro Image preview를 쓰고 싶으면 아래처럼 env에 설정:
# GEMINI_IMAGE_MODEL=gemini-3-pro-image-preview  (Gemini 3 문서에 표기된 이미지 모델) :contentReference[oaicite:1]{index=1}
openai.api_key = os.getenv("OPENAI_API_KEY")
openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
GPT_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

GMAIL_USER         = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY가 없습니다. .env 또는 AI.env에 GEMINI_API_KEY를 설정하세요.")

client = genai.Client(api_key=API_KEY)

app = FastAPI(title="AWARE AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 배포 땐 도메인으로 좁히기
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


# -------------------------
# Utils
# -------------------------
def _load_image(file: UploadFile) -> Image.Image:
    try:
        raw = file.file.read()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return img
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이미지 읽기 실패: {e}")


def _pil_to_jpeg_part(img: Image.Image, quality: int = 95) -> types.Part:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")


def _get_finish_reason(res) -> str:
    try:
        if getattr(res, "candidates", None) and res.candidates:
            fr = getattr(res.candidates[0], "finish_reason", None)
            return str(fr) if fr is not None else "UNKNOWN"
    except Exception:
        pass
    return "UNKNOWN"


def _extract_text_from_response(res) -> str:
    # res.text 우선
    if getattr(res, "text", None):
        return res.text

    # candidates/parts에서 텍스트만 모으기
    parts = None
    if getattr(res, "parts", None):
        parts = res.parts
    elif getattr(res, "candidates", None) and res.candidates:
        c0 = res.candidates[0]
        if getattr(c0, "content", None) and getattr(c0.content, "parts", None):
            parts = c0.content.parts

    if not parts:
        return ""

    texts = []
    for p in parts:
        t = getattr(p, "text", None)
        if t:
            texts.append(t)
    return "\n".join(texts)


def _find_first_json_object(s: str) -> str:
    """
    문자열에서 첫 JSON 객체({...})를 안전하게 추출.
    - 따옴표("...") 안의 { } 는 카운팅하지 않음
    - ```json ... ``` 코드펜스 제거
    """
    s = (s or "").strip()

    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()

    start = s.find("{")
    if start == -1:
        raise ValueError("JSON 시작 '{'를 찾지 못했습니다.")

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(s)):
        ch = s[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]

    raise ValueError("JSON 끝 '}'를 찾지 못했습니다.")


def _contains_foreign_script(text: str) -> bool:
    """
    한국어/영어/숫자/기본문장부호 외 문자(일본어, 힌디어, 아랍어 등)가 포함되어 있으면 True.
    LLM 응답의 mojibake(다국어 깨짐) 감지용.
    """
    if not text:
        return False
    # 허용 범위: 한글(가-힣, ㄱ-ㅎ, ㅏ-ㅣ), 영문, 숫자, ASCII 기호, 일반 공백/줄바꿈
    allowed = re.compile(
        r'^[\uAC00-\uD7A3\u3131-\u3163\u314F-\u3163'
        r'a-zA-Z0-9'
        r'\s\.,!?\-:;\'\"()\[\]{}/\\@#$%^&*_+=~`<>|'
        r'\u2018\u2019\u201C\u201D\u2026\u00B7\u2022\u25CF\u2605\u2606'
        r']+$'
    )
    return not bool(allowed.match(text))


def _sanitize_foreign_text(data: dict) -> dict:
    """
    LLM 응답 dict에서 한국어 외 문자가 섞인 텍스트 필드를 감지하고,
    해당 필드의 비허용 문자를 제거한다.
    """
    text_fields = ["vibe_reason"]
    for field in text_fields:
        val = data.get(field, "")
        if isinstance(val, str) and _contains_foreign_script(val):
            # 비한국어/비영어 문자 제거
            data[field] = re.sub(
                r'[^\uAC00-\uD7A3\u3131-\u3163\u314F-\u3163'
                r'a-zA-Z0-9'
                r'\s\.,!?\-:;\'\"()\[\]{}/\\@#$%^&*_+=~`<>|]',
                '', val
            ).strip()

    # styling_tips 배열
    tips = data.get("styling_tips", [])
    if isinstance(tips, list):
        data["styling_tips"] = [
            re.sub(r'[^\uAC00-\uD7A3\u3131-\u3163\u314F-\u3163a-zA-Z0-9\s\.,!?\-:;\'\"()\[\]{}/\\@#$%^&*_+=~`<>|]', '', t).strip()
            for t in tips if isinstance(t, str)
        ]

    # style_options 배열
    options = data.get("style_options", [])
    if isinstance(options, list):
        for opt in options:
            if not isinstance(opt, dict):
                continue
            for key in ["title", "summary", "edit_prompt"]:
                val = opt.get(key, "")
                if isinstance(val, str) and _contains_foreign_script(val):
                    opt[key] = re.sub(
                        r'[^\uAC00-\uD7A3\u3131-\u3163\u314F-\u3163a-zA-Z0-9\s\.,!?\-:;\'\"()\[\]{}/\\@#$%^&*_+=~`<>|]',
                        '', val
                    ).strip()

    return data


def _thinking_config_for(model_name: str) -> types.ThinkingConfig:
    # Gemini 3는 thinking_level 권장. Flash는 minimal 지원 :contentReference[oaicite:2]{index=2}
    if (model_name or "").startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level="minimal")
    # Gemini 2.5 계열이면 thinking_budget=0도 가능
    return types.ThinkingConfig(thinking_budget=0)


def _extract_inline_image(response) -> Tuple[bytes, str]:
    """
    google-genai 응답에서 이미지 바이트(inline_data.data)와 mime_type 추출
    """
    parts = None

    if getattr(response, "parts", None):
        parts = response.parts
    elif getattr(response, "candidates", None) and response.candidates:
        cand0 = response.candidates[0]
        if getattr(cand0, "content", None) and getattr(cand0.content, "parts", None):
            parts = cand0.content.parts

    if not parts:
        raise ValueError("response에 parts가 없습니다.")

    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            data = inline.data
            mime = getattr(inline, "mime_type", None) or "image/png"

            if isinstance(data, str):
                data = base64.b64decode(data)

            if not isinstance(data, (bytes, bytearray)):
                raise ValueError(f"inline_data.data 타입이 bytes가 아닙니다: {type(data)}")

            return bytes(data), mime

    raise ValueError("inline_data 이미지가 없습니다.")


# -------------------------
# Response Models
# -------------------------
class StyleOption(BaseModel):
    key: str = Field(description="스타일 고유 키 (AI가 생성, 예: 'soft_casual', 'dark_chic' 등)")
    title: str
    summary: str
    edit_prompt: str


class AnalyzeResult(BaseModel):
    face_detected: bool = Field(description="사람 얼굴이 명확히 감지되었는지 여부")
    name: str = Field(description="사용자 이름")
    style_score: float = Field(ge=0, le=10, description="스타일/프로필 완성도 점수 (0~10, 소수 1자리)")
    vibe: str
    vibe_reason: str
    styling_tips: List[str]
    style_options: List[StyleOption]
    default_style_key: str = Field(description="style_options 중 추천 스타일의 key")


class StyleOptionInput(BaseModel):
    key: str
    title: str
    summary: str
    edit_prompt: str

class ShoppingRequest(BaseModel):
    style_options: list[StyleOptionInput]
    gender: Optional[str] = None  # 'm' | 'f' | None

class ShoppingItem(BaseModel):
    category: str   # '상의' | '하의' | '원피스' | '신발' | '악세사리'
    name: str
    price: str
    mall: str
    image: str
    link: str

class StyleSet(BaseModel):
    type: str       # 'separates' | 'onepiece'
    items: list[ShoppingItem]

class ShoppingResponse(BaseModel):
    sets: dict[str, StyleSet]


# -------------------------
# Shopping Helpers
# -------------------------
def strip_html(text: str) -> str:
    """네이버 API 상품명에서 <b> 태그 제거"""
    return re.sub(r'<[^>]+>', '', text)


async def naver_search_one(keyword: str) -> Optional[dict]:
    """
    네이버 쇼핑 API에서 키워드로 상품 1개를 검색한다.
    성공 시 dict 반환, 실패 시 None 반환.
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return None
    url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": keyword, "display": 3, "sort": "sim"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            res = await c.get(url, headers=headers, params=params)
            res.raise_for_status()
            data = res.json()
        items = data.get("items", [])
        for item in items:
            if item.get("image"):
                return {
                    "name":  strip_html(item.get("title", "")),
                    "price": item.get("lprice", "0"),
                    "mall":  item.get("mallName", ""),
                    "image": item.get("image", ""),
                    "link":  item.get("link", ""),
                }
        return None
    except Exception as e:
        print(f"[naver_search_one] 실패: {e}")
        return None


# -------------------------
# Routes
# -------------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/analyze", response_model=AnalyzeResult)
def analyze_face(
    image: UploadFile = File(...),
    name: str = Form("000"),
):
    """
    이미지 + 이름을 받아
    - 스타일/프로필 완성도 점수
    - vibe(상) + 이유
    - 스타일링 팁
    - 3가지 스타일 옵션(선택지) + 각각의 edit_prompt
    JSON 반환
    """
    img = _load_image(image)
    safe_name = (name or "000").strip() or "000"

    # ✅ (중요) 외모/매력 평가가 아니라 "스타일/프로필 완성도" 중심으로만
    # 프롬프트 엔지니어링 사용됨 — 역할 부여(Role Prompting) + 행동 제약(Negative Prompting)
    system_instruction = (
        """You are an encouraging but professional image consultant and fashion stylist.
Your job is to give POSITIVE, UPLIFTING styling scores that boost user confidence while still being honest about styling potential.

SCORING RULES (style_score: 5.0 ~ 9.0, one decimal):
The score MUST always fall between 5.0 (minimum) and 9.0 (maximum). NEVER score below 5.0 or above 9.0.
Score each of the 5 criteria below from 1.0~1.8, then sum for a total between 5.0 and 9.0.

1. Outfit coordination (1.0~1.8): color harmony, fit, overall outfit completeness
   - 1.0: needs improvement but has potential
   - 1.4: solid styling, well put together
   - 1.8: excellent coordination, intentional styling

2. Hairstyle (1.0~1.8): how well it suits the face shape, styling effort, cleanliness
   - 1.0: clean but could use more styling
   - 1.4: nicely styled, suits the person
   - 1.8: perfectly styled, suits face shape excellently

3. Grooming / Skin presentation (1.0~1.8): overall neatness, skin appearance, facial grooming
   - 1.0: clean and presentable
   - 1.4: well-groomed and polished
   - 1.8: exceptionally well-groomed

4. Accessories & details (1.0~1.8): glasses, jewelry, bag, shoes (if visible), small details
   - 1.0: minimal but clean look
   - 1.4: thoughtful accessory choices
   - 1.8: accessories elevate the entire look

5. Overall photo/style impression (1.0~1.8): confidence in pose, background, lighting, total vibe
   - 1.0: pleasant overall impression
   - 1.4: confident and put-together vibe
   - 1.8: strong, magazine-worthy impression

IMPORTANT CALIBRATION — follow this distribution strictly:
- 5.0 ~ 6.4: solid baseline styling with good potential (rare — only for very basic looks)
- 6.5 ~ 7.4: nicely put together, typical above-average everyday styling
- 7.5 ~ 8.4: clearly well-styled, attractive overall
- 8.5 ~ 9.0: very well styled, exceptional look

MOST users should score in the 7.0 ~ 9.0 range. Default to a generous, encouraging score.
A plain t-shirt and jeans with reasonable grooming is a 7.0~7.5, not lower.
ABSOLUTE LIMITS: minimum score is 5.0, maximum score is 9.0. Never go outside this range."""
    )

    # 프롬프트 엔지니어링 사용됨 — 출력 형식 지정(Structured Output) + JSON 스키마 명시 + 규칙 기반 제약(Constraint Prompting)
    prompt = (
        f"사용자 이름은 '{safe_name}'이다.\n"
        "아래 사진을 보고 JSON만 출력해. 설명/마크다운/코드펜스 금지.\n"  # 프롬프트 엔지니어링 사용됨 — 출력 포맷 제한(Output Format Control)
        "반드시 아래 스키마를 정확히 지켜:\n"  # 프롬프트 엔지니어링 사용됨 — 명시적 지시(Explicit Instruction)
        "{\n"
        '  "face_detected": boolean (사람 얼굴이 명확히 보이면 true, 얼굴이 없거나 불명확/가려진 경우 false),\n'
        '  "name": string,\n'
        '  "style_score": number (5.0~9.0, 소수1자리. 시스템 프롬프트의 5개 기준 각 1.0~1.8점 합산. 최저 5.0, 최고 9.0. 대부분 7.0~9.0 범위. 평범한 캐주얼도 7.0~7.5점),\n'
        '  "vibe": string (예: 강아지상/고양이상/사슴상/여우상/곰상/토끼상/공룡상 등등 가장 닮은 동물을 한가지 골라 출력),\n'  # 프롬프트 엔지니어링 사용됨 — 선택지 제한(Constrained Choice)
        '  "vibe_reason": string (긍정적/중립적으로 1~2문장),\n'
        '  "styling_tips": string[] (4~6개, 선택형 팁: 헤어/안경/의상/피부상태 등),\n'
        '  "style_options": [\n'
        '    {"key":string,"title":string,"summary":string,"edit_prompt":string},\n'  # 프롬프트 엔지니어링 사용됨 — AI 자율 생성(Open-ended Generation)
        '    ... (정확히 5개, 사진 속 인물에게 가장 어울리는 스타일 5가지를 AI가 자유롭게 제안)\n'
        '  ],\n'
        '  "default_style_key": string (style_options 중 가장 추천하는 스타일의 key)\n'
        "}\n"
        "규칙:\n"
        "- face_detected가 false이면 나머지 필드는 기본값(빈 문자열/0/빈 배열)으로 채워.\n"
        "- 외모(매력/못생김/예쁨) 평가 금지.\n"  # 프롬프트 엔지니어링 사용됨 — 네거티브 프롬프팅(Negative Prompting)
        "- name 필드는 반드시 입력 이름 그대로 넣어.\n"
        "- style_options의 key는 스타일을 나타내는 영문 snake_case로 AI가 자유롭게 생성 (예: 'soft_casual', 'dark_chic', 'preppy_classic' 등).\n"  # 프롬프트 엔지니어링 사용됨 — AI 자율 생성 + 포맷 제약(Format Constraint)
        "- style_options 5가지는 사진 속 인물의 얼굴형, 분위기, 체형 등을 고려해 가장 어울리는 스타일을 추천.\n"  # 프롬프트 엔지니어링 사용됨 — 맥락 기반 추론 유도(Context-aware Reasoning)
        "- edit_prompt는 '원본 인물 동일성 유지(얼굴/표정/신원 유지)'를 전제로 스타일만 적용하도록 한국어로 써.\n"  # 프롬프트 엔지니어링 사용됨 — 안전 가드레일(Safety Guardrail)
        "- JSON은 한 번에 끝까지 완성해(중간에 끊기지 않게 간결하게).\n"  # 프롬프트 엔지니어링 사용됨 — 완성도 지시(Completion Instruction)
        "- 모든 텍스트 필드(vibe, vibe_reason, styling_tips, style_options의 title/summary/edit_prompt)는 반드시 한국어(ko-KR)로만 작성해. "
        "영어 브랜드명/고유명사 외에 일본어, 중국어, 힌디어, 아랍어 등 다른 언어의 문자를 절대 포함하지 마.\n"  # 프롬프트 엔지니어링 사용됨 — 언어 고정(Language Constraint)
    )

    # 이미지를 base64로 인코딩 (OpenAI Vision API용)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    def call_once():
        return openai.chat.completions.create(
            model=GPT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_instruction + "\n\nYou MUST respond with valid JSON only."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}",
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
        )

    try:
        res = call_once()

        raw_text = res.choices[0].message.content or ""
        print("\n[ANALYZE FINISH_REASON]", res.choices[0].finish_reason)
        print("[ANALYZE TEXT LEN]", len(raw_text))
        print("[ANALYZE RAW HEAD]\n", raw_text[:200])

        json_text = _find_first_json_object(raw_text)
        parsed = json.loads(json_text)
        parsed = _sanitize_foreign_text(parsed)
        result = AnalyzeResult.model_validate(parsed)
        if not result.face_detected:
            raise HTTPException(status_code=400, detail="NO_FACE_DETECTED")
        return result.model_dump()

    except HTTPException:
        raise
    except Exception as e1:
        # 재시도
        try:
            print("\n[ANALYZE RETRY] reason =", repr(e1))
            res2 = call_once()

            raw2 = res2.choices[0].message.content or ""
            print("[ANALYZE RETRY FINISH_REASON]", res2.choices[0].finish_reason)
            print("[ANALYZE RETRY TEXT LEN]", len(raw2))
            print("[ANALYZE RETRY RAW HEAD]\n", raw2[:200])

            json_text2 = _find_first_json_object(raw2)
            parsed2 = json.loads(json_text2)
            parsed2 = _sanitize_foreign_text(parsed2)
            result2 = AnalyzeResult.model_validate(parsed2)
            if not result2.face_detected:
                raise HTTPException(status_code=400, detail="NO_FACE_DETECTED")
            return result2.model_dump()

        except HTTPException:
            raise
        except Exception as e2:
            raw_preview = (res2.choices[0].message.content if 'res2' in locals() else "") or ""
            raw_preview = raw_preview[:800]
            raise HTTPException(
                status_code=500,
                detail=f"응답 JSON 파싱 실패: {repr(e2)} / raw_preview={raw_preview}",
            )


@app.post("/api/shopping", response_model=ShoppingResponse)
async def shopping(req: ShoppingRequest):
    import asyncio

    gender_label = "남성" if req.gender == "m" else "여성" if req.gender == "f" else ""

    style_list_text = "\n".join([
        f"- key: {opt.key}, title: {opt.title}, summary: {opt.summary}"
        for opt in req.style_options
    ])

    keyword_prompt = f"""당신은 패션 스타일리스트입니다.
아래 스타일 옵션별로 네이버 쇼핑 검색에 쓸 키워드 세트를 만들어 주세요.
성별: {gender_label if gender_label else "미지정"}

스타일 목록:
{style_list_text}

규칙:
1. 각 스타일은 separates(상하의 분리) 또는 onepiece(원피스/점프수트 일체형) 중 하나.
2. separates이면: 상의, 하의 필수. 스타일에 어울리면 신발, 악세사리 중 1~2개 추가.
3. onepiece이면: 원피스 1개 필수. 신발, 악세사리 중 1~2개 추가.
4. 키워드는 네이버 쇼핑 검색에 최적화된 한국어. 예: '남성 오버핏 블랙 자켓'
5. 최대 아이템 수는 4개.

JSON만 출력. 설명 없음. 마크다운 없음.
형식:
{{
  "dark_chic": {{
    "type": "separates",
    "items": [
      {{"category": "상의", "keyword": "남성 블랙 오버핏 자켓"}},
      {{"category": "하의", "keyword": "남성 블랙 슬랙스"}},
      {{"category": "신발", "keyword": "남성 더비슈즈 블랙"}}
    ]
  }}
}}"""

    keyword_data = {}
    try:
        kw_resp = openai_client.chat.completions.create(
            model=GPT_MODEL,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": keyword_prompt}],
            max_tokens=1000,
        )
        keyword_data = json.loads(kw_resp.choices[0].message.content)
    except Exception as e:
        print(f"[shopping] GPT 키워드 생성 실패: {e}")
        raise HTTPException(status_code=500, detail=f"키워드 생성 실패: {e}")

    result_sets = {}

    for style_key, style_data in keyword_data.items():
        style_type = style_data.get("type", "separates")
        kw_items   = style_data.get("items", [])

        tasks = [naver_search_one(item["keyword"]) for item in kw_items]
        search_results = await asyncio.gather(*tasks)

        shopping_items = []
        for i, found in enumerate(search_results):
            if found and i < len(kw_items):
                shopping_items.append(ShoppingItem(
                    category=kw_items[i]["category"],
                    **found
                ))

        if shopping_items:
            result_sets[style_key] = StyleSet(
                type=style_type,
                items=shopping_items
            )

    return ShoppingResponse(sets=result_sets)


@app.post("/api/apply")
async def apply_style(
    image: UploadFile = File(...),
    edit_prompt: str  = Form(...),
    shopping_items: Optional[str] = Form(None),
):
    """
    원본 이미지를 받아, AI가 생성한 스타일 프롬프트(edit_prompt)로 스타일링 적용 이미지를 생성해 반환(png/jpg)
    """
    img = _load_image(image)

    final_prompt = (edit_prompt or "").strip()
    if not final_prompt:
        raise HTTPException(status_code=400, detail="edit_prompt가 비어 있습니다.")

    # shopping_items가 있으면 옷 정보를 프롬프트에 포함
    item_desc = ""
    if shopping_items:
        try:
            items_list = json.loads(shopping_items)
            parts = [f"{it['category']}: {it['name']}" for it in items_list if it.get('name')]
            if parts:
                item_desc = "착용 아이템: " + ", ".join(parts) + ". "
        except Exception:
            pass  # 파싱 실패 시 무시하고 기존 프롬프트만 사용

    # ✅ 신원 보존 + 실사 강제 + 스타일 정밀 적용 프롬프트
    # 프롬프트 엔지니어링 사용됨 — 안전 가드레일 + 네거티브 프롬프팅 + 신원 보존 + 실사 강제
    safe_edit_prompt = (
        # ── [1] 최우선: 실사 사진 강제
        "OUTPUT REQUIREMENT: Generate a PHOTOREALISTIC image only. "
        "The result must look like a real photograph taken with a camera. "
        "Do NOT generate anime, illustration, cartoon, painting, sketch, CG render, "
        "webtoon, manga, or any non-photographic art style under any circumstances. "

        # ── [2] 동일 인물 보존 (절대 조건)
        "IDENTITY PRESERVATION (ABSOLUTE): "
        "The person in the output MUST be the exact same individual as in the input photo. "
        "Preserve ALL of the following without any modification: "
        "face shape, facial bone structure, eye shape and color, nose shape, lip shape, "
        "eyebrow shape, skin tone, skin texture, body proportions, shoulder width, and age appearance. "
        "The face must be immediately recognizable as the same person. "
        "Do NOT change, improve, smooth, or alter the face or body in any way. "
        "Do NOT apply beauty filters, skin smoothing, or facial reshaping. "

        # ── [3] 스타일 적용 대상 명시
        "STYLING CHANGES ONLY — apply ONLY what is listed below, nothing else: "
        + (f"Clothing items to wear: {item_desc} " if item_desc else "")
        + f"Style instruction: {final_prompt} "

        # ── [4] 변경 금지 항목 명시
        "DO NOT CHANGE: face, skin color, body shape, facial features, eye color, "
        "hair color unless explicitly requested, or any physical characteristic "
        "that cannot be changed by styling. "
        "Do NOT add accessories or clothing beyond what is explicitly requested. "

        # ── [5] 품질 및 구도 유지
        "QUALITY: Natural lighting consistent with the original photo. "
        "High-quality photorealistic portrait or full-body shot "
        "matching the composition and framing of the original image."
    )

    image_part = _pil_to_jpeg_part(img)

    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=[image_part, safe_edit_prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"이미지 생성 호출 실패: {repr(e)}")

    try:
        img_bytes, mime = _extract_inline_image(response)
        return StreamingResponse(io.BytesIO(img_bytes), media_type=mime)
    except Exception as e:
        debug_text = getattr(response, "text", None)
        raise HTTPException(status_code=500, detail=f"이미지 추출 실패: {repr(e)} / response.text={debug_text}")


# -------------------------
# Email Helper
# -------------------------
def _build_email_html(result: dict, has_original: bool = False, generated_images: list = None, shopping_sets: dict = None) -> str:
    name        = result.get("name", "사용자")
    score       = result.get("style_score", 0)
    vibe        = result.get("vibe", "")
    vibe_reason = result.get("vibe_reason", "")
    tips        = result.get("styling_tips", [])
    options     = result.get("style_options", [])
    gen_list    = generated_images or []
    shops       = shopping_sets or {}

    score_color = "#4CAF50" if score >= 7 else "#FF9800" if score >= 5 else "#F44336"

    tips_html = "".join(f"""
      <tr>
        <td style="padding:8px 0;border-bottom:1px solid #f0f0f0;color:#444;font-size:14px;line-height:1.6;">
          <span style="color:#2d6cff;font-weight:700;margin-right:8px;">•</span>{tip}
        </td>
      </tr>""" for tip in tips)

    def _shopping_items_html(key: str) -> str:
        style_set = shops.get(key, {})
        items = style_set.get("items", []) if style_set else []
        if not items:
            return ""
        cells = ""
        for item in items:
            img_url  = item.get("image", "")
            link     = item.get("link", "#")
            cat      = item.get("category", "")
            item_name = item.get("name", "")
            price    = item.get("price", "0")
            mall     = item.get("mall", "")
            try:
                price_str = f"{int(price):,}원"
            except Exception:
                price_str = f"{price}원"
            cells += f"""
              <td style="width:25%;padding:4px;vertical-align:top;text-align:center;">
                <a href="{link}" target="_blank" style="text-decoration:none;color:inherit;display:block;">
                  <img src="{img_url}" width="80" height="80"
                       style="width:80px;height:80px;object-fit:cover;border-radius:8px;display:block;margin:0 auto 6px;"
                       onerror="this.style.display='none'">
                  <div style="font-size:9px;color:#2d6cff;font-weight:700;margin-bottom:2px;">{cat}</div>
                  <div style="font-size:10px;color:#333;line-height:1.3;margin-bottom:2px;word-break:keep-all;">{item_name[:20]}{"..." if len(item_name)>20 else ""}</div>
                  <div style="font-size:10px;font-weight:700;color:#1a1a2e;">{price_str}</div>
                  <div style="font-size:9px;color:#999;">{mall}</div>
                  <div style="margin-top:4px;font-size:9px;color:#2d6cff;text-decoration:underline;">네이버 쇼핑 ↗</div>
                </a>
              </td>"""
        return f"""
          <div style="margin-top:10px;">
            <div style="font-size:11px;color:#999;margin-bottom:6px;">추천 아이템</div>
            <table width="100%" cellpadding="0" cellspacing="0"><tr>{cells}</tr></table>
          </div>"""

    options_html = "".join(f"""
      <div style="background:#f8f9ff;border:1px solid #e0e8ff;border-radius:12px;padding:14px 16px;margin-bottom:10px;">
        <div style="font-weight:700;color:#2d6cff;font-size:14px;margin-bottom:4px;">{opt.get('title','')}</div>
        <div style="color:#555;font-size:13px;line-height:1.5;">{opt.get('summary','')}</div>
        {_shopping_items_html(opt.get('key',''))}
      </div>""" for opt in options)

    # 원본 사진
    orig_section = ""
    if has_original:
        orig_section = """
        <tr><td style="padding:24px 32px 0;"><div style="height:1px;background:#eee;"></div></td></tr>
        <tr>
          <td style="padding:24px 32px 0;">
            <div style="font-size:13px;font-weight:700;color:#2d6cff;letter-spacing:0.04em;margin-bottom:14px;">✦ 원본 사진</div>
            <img src="cid:original_photo" style="width:100%;border-radius:10px;display:block;">
          </td>
        </tr>"""

    # 생성된 스타일링 이미지들
    gen_items_html = ""
    for i, g in enumerate(gen_list):
        label   = g.get("label", f"스타일링 {i+1}")
        prompt  = g.get("prompt", "")
        cid     = f"generated_photo_{i}"
        desc    = label
        if prompt and prompt != label:
            desc = f"{label} — {prompt}"
        gen_items_html += f"""
            <div style="margin-bottom:20px;">
              <div style="font-size:12px;color:#888;margin-bottom:8px;line-height:1.5;">{desc}</div>
              <img src="cid:{cid}" style="width:100%;border-radius:10px;display:block;">
            </div>"""

    gen_section = ""
    if gen_items_html:
        gen_section = f"""
        <tr><td style="padding:24px 32px 0;"><div style="height:1px;background:#eee;"></div></td></tr>
        <tr>
          <td style="padding:24px 32px 0;">
            <div style="font-size:13px;font-weight:700;color:#2d6cff;letter-spacing:0.04em;margin-bottom:14px;">✦ 생성된 스타일링 이미지</div>
            {gen_items_html}
          </td>
        </tr>"""

    photos_section = orig_section + gen_section

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6fb;padding:32px 16px;">
    <tr><td align="center">
      <table width="100%" style="max-width:560px;background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">

        <!-- 헤더 -->
        <tr>
          <td style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);padding:32px 32px 28px;text-align:center;">
            <div style="font-size:11px;letter-spacing:0.15em;color:rgba(255,255,255,0.5);margin-bottom:6px;">PERSONAL STYLING AI</div>
            <div style="font-size:26px;font-weight:900;color:#ffffff;letter-spacing:0.08em;">AWARE AI</div>
            <div style="margin-top:16px;font-size:15px;color:rgba(255,255,255,0.75);">{name}님의 스타일 분석 리포트</div>
          </td>
        </tr>

        <!-- 점수 -->
        <tr>
          <td style="padding:32px 32px 0;text-align:center;">
            <div style="display:inline-block;background:#f8f9ff;border-radius:16px;padding:24px 40px;">
              <div style="font-size:13px;color:#888;margin-bottom:4px;">스타일 점수</div>
              <div style="font-size:52px;font-weight:900;color:{score_color};line-height:1;">{score}</div>
              <div style="font-size:16px;color:#aaa;font-weight:600;">/ 10</div>
            </div>
          </td>
        </tr>

        <!-- 동물상 + 이유 -->
        <tr>
          <td style="padding:24px 32px 0;text-align:center;">
            <div style="font-size:22px;font-weight:900;color:#1a1a2e;">{vibe}</div>
            <div style="margin-top:10px;font-size:14px;color:#555;line-height:1.7;max-width:440px;margin-left:auto;margin-right:auto;">{vibe_reason}</div>
          </td>
        </tr>

        <!-- 사진 -->
        {photos_section}

        <!-- 구분선 -->
        <tr><td style="padding:28px 32px 0;"><div style="height:1px;background:#eee;"></div></td></tr>

        <!-- 스타일링 팁 -->
        <tr>
          <td style="padding:24px 32px 0;">
            <div style="font-size:13px;font-weight:700;color:#2d6cff;letter-spacing:0.04em;margin-bottom:12px;">✦ 스타일링 팁</div>
            <table width="100%" cellpadding="0" cellspacing="0">
              {tips_html}
            </table>
          </td>
        </tr>

        <!-- 구분선 -->
        <tr><td style="padding:24px 32px 0;"><div style="height:1px;background:#eee;"></div></td></tr>

        <!-- 추천 스타일 -->
        <tr>
          <td style="padding:24px 32px 0;">
            <div style="font-size:13px;font-weight:700;color:#2d6cff;letter-spacing:0.04em;margin-bottom:14px;">✦ 추천 스타일</div>
            {options_html}
          </td>
        </tr>

        <!-- 푸터 -->
        <tr>
          <td style="padding:28px 32px 32px;text-align:center;">
            <div style="font-size:12px;color:#bbb;">이 리포트는 AWARE AI가 자동 생성한 스타일 분석 결과입니다.</div>
            <div style="margin-top:6px;font-size:11px;color:#ccc;">© 2026 AWARE AI</div>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


class GeneratedImageItem(BaseModel):
    image: str        # base64 JPEG
    label: str = ""
    prompt: str = ""

class SendResultRequest(BaseModel):
    email: str
    result: dict
    original_image: Optional[str] = None          # base64 JPEG
    generated_images: Optional[List[GeneratedImageItem]] = None  # 생성된 이미지 배열
    shopping_sets: Optional[dict] = None          # { style_key: { type, items: [...] } }


@app.post("/api/send-result")
def send_result_email(req: SendResultRequest):
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise HTTPException(status_code=503, detail="서버에 발신 이메일이 설정되지 않았습니다.")

    has_orig = bool(req.original_image)
    gen_list = req.generated_images or []

    name = req.result.get("name", "사용자")
    gen_meta = [{"label": g.label, "prompt": g.prompt} for g in gen_list]
    html_content = _build_email_html(
        req.result,
        has_original=has_orig,
        generated_images=gen_meta,
        shopping_sets=req.shopping_sets,
    )

    # multipart/related → 인라인 이미지 CID 참조 가능
    msg_root = MIMEMultipart("related")
    msg_root["Subject"] = f"[AWARE AI] {name}님의 스타일 분석 리포트"
    msg_root["From"]    = f"AWARE AI <{GMAIL_USER}>"
    msg_root["To"]      = req.email

    # HTML 파트
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_content, "html", "utf-8"))
    msg_root.attach(alt)

    # 원본 사진 인라인 첨부
    if has_orig:
        img_bytes = base64.b64decode(req.original_image)
        img_part = MIMEImage(img_bytes, _subtype="jpeg")
        img_part.add_header("Content-ID", "<original_photo>")
        img_part.add_header("Content-Disposition", "inline", filename="original.jpg")
        msg_root.attach(img_part)

    # 생성된 이미지들 인라인 첨부 (각각 고유 CID)
    for i, g in enumerate(gen_list):
        img_bytes = base64.b64decode(g.image)
        img_part = MIMEImage(img_bytes, _subtype="jpeg")
        img_part.add_header("Content-ID", f"<generated_photo_{i}>")
        img_part.add_header("Content-Disposition", "inline", filename=f"styled_{i+1}.jpg")
        msg_root.attach(img_part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, req.email, msg_root.as_string())
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(status_code=401, detail="Gmail 인증 실패. 앱 비밀번호를 확인해주세요.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"이메일 발송 실패: {repr(e)}")

    return {"ok": True, "to": req.email}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
