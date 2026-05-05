# LLM.py
import undetected_chromedriver as uc
import threading
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def init_driver(version=147):
    options = uc.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument("--window-size=1920,1080")
    
    driver = uc.Chrome(version_main=version, options=options)
    wait = WebDriverWait(driver, 30)
    
    try:
        driver.get("https://gemini.google.com/app")
        print("--- 드라이버 및 세션 초기화 완료 ---")
        return driver, wait
    except Exception as e:
        print(f"초기화 오류: {e}")
        if driver: driver.quit()
        return None, None

def send_and_get_response(driver, wait, message, abort_event=None):
    if abort_event is None:
        abort_event = threading.Event()
    try:
        input_selector = "div[role='textbox']"
        stop_btn_selector = "button[aria-label*='중지'], button[aria-label*='Stop']"
        mic_container_selector = ".mic-button-container"
        response_selector = ".model-response-text .markdown-main-panel"

        # 기존 응답 상태 기록: 이전 대화 결과를 재사용하지 않기 위함
        initial_responses = driver.find_elements(By.CSS_SELECTOR, response_selector)
        initial_response_count = len(initial_responses)
        initial_last_text = initial_responses[-1].text if initial_responses else ""

        # 1. 입력창 대기 및 텍스트 주입
        if abort_event.is_set():
            print("[Action] 요청 취소됨: 재시작 트리거 감지")
            return "Error: Aborted by restart request"
        input_area = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, input_selector)))
        driver.execute_script("arguments[0].textContent = arguments[1];", input_area, message)
        input_area.send_keys(Keys.ENTER)

        last_len = 0
        stable_count = 0
        start_time = time.time()
        timeout = 90

        while time.time() - start_time < timeout:
            if abort_event.is_set():
                print("[Action] 요청 취소됨: 재시작 트리거 감지")
                return "Error: Aborted by restart request"

            # 현재 응답 텍스트 추출
            responses = driver.find_elements(By.CSS_SELECTOR, response_selector)
            current_text = responses[-1].text if responses else ""
            current_len = len(current_text)
            response_is_new = len(responses) > initial_response_count or current_text != initial_last_text

            # [정상 종료 확인] 마이크 버튼이 보이면 완료
            try:
                mic_container = driver.find_element(By.CSS_SELECTOR, mic_container_selector)
                if "hidden" not in (mic_container.get_attribute("class") or ""):
                    if response_is_new and current_len > 0:
                        return current_text.strip()
            except:
                pass

            # [정체 감지 로직]
            if current_len > 0 and current_len == last_len:
                stable_count += 1
            else:
                stable_count = 0
                last_len = current_len

            # 기다리는 동안 계속 아래로 스크롤 (강화된 스크롤 기능)
            if responses:
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'end'});", responses[-1])

            # 10초 이상 정체 시 강제 종료 및 현재까지 내용 반환
            if stable_count >= 10:
                print("[Action] 강제 종료 및 데이터 추출")
                try:
                    stop_btn = driver.find_element(By.CSS_SELECTOR, stop_btn_selector)
                    stop_btn.click()
                    time.sleep(1)
                except: pass
                return current_text.strip() if current_text else "Error: Response Stagnated"

            time.sleep(1)
        return "Error: Timeout"
    except Exception as e:
        return f"Error: {str(e)}"