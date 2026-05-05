from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from selenium.webdriver.common.by import By
import asyncio
import json
import re
import time
import threading
from typing import Optional

# LLM.py에서 드라이버 초기화 및 메시지 전송 함수 가져오기
# (LLM.py 파일이 동일 경로에 있어야 합니다)
from LLM import init_driver, send_and_get_response

app = FastAPI()

# 🔥 CORS 설정: 프론트엔드 통신을 위한 보안 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 전역 변수: 드라이버 세션 관리 ---
driver = None
wait = None
driver_lock = asyncio.Lock()
abort_event = threading.Event()
driver_ready_event = asyncio.Event()

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 제미나이 세션을 미리 초기화합니다."""
    global driver, wait
    async with driver_lock:
        driver, wait = await asyncio.to_thread(init_driver, version=147)
        if driver and wait:
            driver_ready_event.set()

@app.on_event("shutdown")
def shutdown_event():
    """서버 종료 시 브라우저를 안전하게 닫습니다."""
    if driver:
        driver.quit()

async def restart_driver_session():
    """현재 드라이버를 정상 종료하고 새로운 세션을 초기화합니다."""
    global driver, wait, abort_event
    abort_event.set()
    driver_ready_event.clear()
    async with driver_lock:
        try:
            if driver:
                try:
                    await asyncio.to_thread(driver.quit)
                except Exception as e:
                    print(f"Driver quit error: {e}")
                driver = None
                wait = None
            driver, wait = await asyncio.to_thread(init_driver, version=147)
            if not driver or not wait:
                raise RuntimeError("AI 세션 초기화에 실패했습니다.")
            driver_ready_event.set()
            return driver, wait
        finally:
            abort_event.clear()

@app.post("/restart-driver")
async def restart_driver():
    try:
        await restart_driver_session()
        return {"status": "ok", "message": "AI 세션이 재시작되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 데이터 모델 정의 ---
class QuizRequest(BaseModel):
    difficulty: str = "medium"  # easy, medium, difficult
    count: int = 5             # 생성할 문제 수
    material: str              # 학습 자료 텍스트
    category: Optional[str] = "한국사"

# --- 핵심 로직: 프롬프트 구성 ---
def build_quiz_prompt(request: QuizRequest):
    """2022 개정 교육과정 기준에 맞춘 정밀 프롬프트"""
    return f"""
당신은 한국사 전문 출제 위원입니다. 아래 제공된 [학습 자료]를 바탕으로만 문제를 출제하세요.

[학습 자료]
{request.material}

[출제 가이드라인]
1. 난이도: {request.difficulty}
2. 문제 개수: 정확히 {request.count}개.
3. 형식: 4지 선다형 객관식.
4. **줄바꿈 제한 (매우 중요)**: 
   - JSON 결과값 내부에 실제 줄바꿈(Enter 키)을 절대 포함하지 마세요.
   - 문제 내용(question)이나 해설(explanation)에서 줄바꿈이 필요한 경우 반드시 `<br>` 태그를 사용하세요. 
5. 반드시 아래 JSON 배열 형식으로만 응답하고 이외의 텍스트(인사말 등)는 모두 생략하세요.
6. 해설은 A(0),B(1),C(2),D(3) 알파벳 선지를 기반으로 작성하세요.

[JSON 반환 포맷]
[
  {{
    "question": "문제 내용",
    "choices": ["선지1", "선지2", "선지3", "선지4"],
    "answer": 0,
    "explanation": "해설",
    "keywords": ["핵심 키워드1", "핵심 키워드2", "핵심 키워드3"],
    "difficulty": "{request.difficulty}",
    "category": "{request.category}"
  }}
]
"""

# --- 유틸리티 함수 ---

def extract_json_array(text: str) -> str:
    """텍스트 내에서 JSON 배열([...]) 부분만 정밀하게 추출합니다."""
    start_index = text.find('[')
    if start_index == -1:
        raise ValueError('AI 응답에서 JSON 배열 시작점을 찾을 수 없습니다.')

    depth = 0
    in_string = False
    escape = False

    for index, ch in enumerate(text[start_index:], start=start_index):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                return text[start_index:index + 1]

    raise ValueError('AI 응답에서 JSON 배열이 완성되지 않았습니다(닫는 괄호 미달).')

def sanitize_json_string(json_text: str) -> str:
    """JSON 파싱 에러를 방지하기 위해 내부 특수문자 및 줄바꿈을 정규화합니다."""
    def replace_with_br(match):
        content = match.group(0)
        content = content.replace('\n', '<br>').replace('\r', '')
        content = content.replace('\\n', '<br>')
        return content

    string_pattern = r'("(?:\\.|[^"\\])*")'
    return re.sub(string_pattern, replace_with_br, json_text)

def get_current_response_only(driver):
    """채팅을 새로 보내지 않고 현재 화면에 렌더링된 마지막 답변만 다시 읽어옵니다."""
    try:
        response_selector = ".model-response-text .markdown-main-panel"
        responses = driver.find_elements(By.CSS_SELECTOR, response_selector)
        return responses[-1].text if responses else ""
    except:
        return ""

# --- 메인 API 엔드포인트 ---

@app.post("/generate-quiz")
async def generate_quiz(request: QuizRequest):
    global driver, wait
    
    if not driver_ready_event.is_set():
        try:
            await asyncio.wait_for(driver_ready_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="AI 세션 재시작 중입니다. 잠시 후 다시 시도해 주세요.")

    if not driver:
        raise HTTPException(status_code=500, detail="AI 세션이 준비되지 않았습니다.")

    prompt = build_quiz_prompt(request)
    
    # [Outer Loop] 1. 프롬프트 재전송 시도 (최대 2회)
    for prompt_attempt in range(2):
        print(f"[Log] 문제 생성 시도 시작... (차수: {prompt_attempt + 1}/2)")
        
        # LLM.py의 스크롤 자극 로직이 포함된 함수 호출
        raw_response = await asyncio.to_thread(send_and_get_response, driver, wait, prompt, abort_event)
        
        if raw_response.startswith("Error: Aborted by restart request"):
            print("[Info] 현재 세션이 재시작되었습니다. 새로운 드라이버가 준비될 때까지 대기합니다.")
            try:
                await asyncio.wait_for(driver_ready_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                raise HTTPException(status_code=503, detail="AI 세션 재시작 중입니다. 잠시 후 다시 시도해 주세요.")
            continue
        if raw_response.startswith("Error:"):
            print(f"[Warning] LLM 응답 단계 에러: {raw_response}")
            # 세션 리셋 시도 (세션 오염 복구)
            try:
                await asyncio.to_thread(driver.refresh)
                await asyncio.sleep(3)
            except:
                pass
            continue

        # [Inner Loop] 2. 데이터 재추출 시도 (최대 3회)
        for scrape_attempt in range(3):
            try:
                # 2회차 추출부터는 화면을 다시 읽음 (AI가 뒤늦게 답변을 완성할 경우 대비)
                current_raw = raw_response if scrape_attempt == 0 else get_current_response_only(driver)
                
                if not current_raw:
                    raise ValueError("추출된 텍스트가 비어 있습니다.")

                # JSON 배열 추출 및 정제
                json_text = extract_json_array(current_raw)
                sanitized_json = sanitize_json_string(json_text)
                ai_quizzes = json.loads(sanitized_json)
                
                # 데이터 유효성 검증
                if isinstance(ai_quizzes, list) and len(ai_quizzes) > 0:
                    print(f"[Success] 문제 생성 완료 ({len(ai_quizzes)}개)")
                    return ai_quizzes[:request.count]
                
                raise ValueError("파싱된 데이터가 올바른 배열 형식이 아닙니다.")

            except (ValueError, json.JSONDecodeError) as e:
                if scrape_attempt < 2:
                    wait_time = 4
                    print(f"[Retry] 파싱 실패 ({e}). {wait_time}초 후 재스크롤 및 추출 시도... ({scrape_attempt + 1}/3)")
                    # UI 강제 동기화를 위한 하단 스크롤
                    await asyncio.to_thread(driver.execute_script, "window.scrollTo(0, document.body.scrollHeight);")
                    await asyncio.sleep(wait_time)
                else:
                    print("[Fail] 해당 응답에서 JSON 추출 불가.")

        # Inner Loop 실패 시 약간의 대기 후 Outer Loop(재전송) 진행
        await asyncio.sleep(2)

    raise HTTPException(
        status_code=500, 
        detail="제미나이가 응답을 완성하지 못했거나 형식이 잘못되었습니다. 잠시 후 다시 시도해 주세요."
    )

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Korean History Quiz AI Server is running."}
