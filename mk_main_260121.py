from dotenv import load_dotenv
load_dotenv()

import re
import requests
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timezone, timedelta
import time
import signal
import os
import threading
from typing import Optional, List, Dict, Tuple
from requests.exceptions import RequestException, Timeout, ConnectionError, HTTPError
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import asyncio
import aiosmtplib
from langchain_ollama import ChatOllama

LLM_TIMEOUT = 300  # 초: 이 시간 동안 Ollama 응답 없으면 타임아웃 경고

def invoke_llm_with_timeout(llm, prompt):
    """llm.invoke()를 별도 스레드에서 실행해 LLM_TIMEOUT 초 후에도 응답 없으면 TimeoutError 발생"""
    result = [None]
    error = [None]

    def target():
        try:
            result[0] = llm.invoke(prompt)
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(LLM_TIMEOUT)

    if t.is_alive():
        raise TimeoutError(f"Ollama {LLM_TIMEOUT}초 내 응답 없음")
    if error[0]:
        raise error[0]
    return result[0]

def _handle_interrupt(sig, frame):
    print("\n\n프로세스가 중단되었습니다.")
    os._exit(0)

signal.signal(signal.SIGINT, _handle_interrupt)

# 로그 설정 개선
logging.basicConfig(
    filename='mk_news_summary_test.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    encoding='utf-8'
)

logger = logging.getLogger(__name__)

# 전역 설정
base_url = "https://www.mk.co.kr/news/ranking"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# 재시도 설정
MAX_RETRIES = 5
RETRY_DELAY = 2  # 초
REQUEST_TIMEOUT = 30  # 초

class NewsScrapingError(Exception):
    """뉴스 스크래핑 관련 예외"""
    pass

class SummarizeError(Exception):
    """요약 관련 예외"""
    pass

class EmailSendError(Exception):
    """이메일 발송 관련 예외"""
    pass

def safe_request(url: str, retries: int = MAX_RETRIES) -> Optional[requests.Response]:
    """안전한 HTTP 요청 함수"""
    for attempt in range(retries):
        try:
            #logger.info(f"HTTP 요청 시도 {attempt + 1}/{retries}: {url}")
            response = requests.get(
                url, 
                headers=headers, 
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True
            )
            response.raise_for_status()
            #logger.info(f"HTTP 요청 성공: {url}")
            return response
            
        except Timeout as e:
            logger.warning(f"요청 시간 초과 (시도 {attempt + 1}/{retries}): {url} - {e}")
        except ConnectionError as e:
            logger.warning(f"연결 오류 (시도 {attempt + 1}/{retries}): {url} - {e}")
        except HTTPError as e:
            if e.response.status_code >= 500:
                logger.warning(f"서버 오류 {e.response.status_code} (시도 {attempt + 1}/{retries}): {url}")
            else:
                logger.error(f"HTTP 오류 {e.response.status_code}: {url} - 재시도하지 않음")
                break
        except RequestException as e:
            logger.warning(f"요청 오류 (시도 {attempt + 1}/{retries}): {url} - {e}")
        except Exception as e:
            logger.error(f"예상치 못한 오류 (시도 {attempt + 1}/{retries}): {url} - {e}")
            
        if attempt < retries - 1:
            logger.info(f"{RETRY_DELAY}초 후 재시도...")
            time.sleep(RETRY_DELAY)
    
    logger.error(f"모든 시도 실패: {url}")
    return None

def clean_text(text: str) -> str:
    """텍스트 정리 함수"""
    try:
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n+', '\n', text)
        return text
    except Exception as e:
        logger.error(f"텍스트 정리 중 오류: {e}")
        return text if text else ""
        
def get_news_content(link: str) -> str:
    """뉴스 본문 추출 (랭킹 페이지용도 같이 사용)"""
    try:
        if not link:
            return ""

        if not link.startswith("http"):
            link = base_url + link

        response = safe_request(link)
        if not response:
            logger.warning(f"본문 요청 실패: {link}")
            return ""

        news_html = BeautifulSoup(response.text, "html.parser")

        content = news_html.select_one("div.news_cnt_detail_wrap")
        if content is None:
            content = news_html.select_one("#articleBody")
            if content is None:
                logger.warning(f"본문 div를 찾을 수 없음: {link}")
                return ""

        for tag in content.find_all(["div", "figure", "img"]):
            tag.decompose()

        content_text = content.get_text(separator="\n", strip=True)
        content_text = content_text.replace("사진 확대", "")
        content_text = clean_text(content_text)

        return content_text
    except Exception as e:
        logger.error(f"본문 추출 중 오류: {e}")
        return ""


def get_main_news_list() -> Tuple[List[str], List[str], List[str], str]:
    try:
        ranking_url = f"{base_url}"
        response = safe_request(ranking_url)
        if not response:
            raise NewsScrapingError("랭킹 페이지 요청 실패")

        soup = BeautifulSoup(response.text, 'html.parser')

        news_list = soup.select('div.ak_pnews_area ul.ak_pnews_grid_5 > li')
        logger.info(f"랭킹 li 개수: {len(news_list)}")

        if not news_list:
            raise NewsScrapingError("랭킹 뉴스 목록을 찾을 수 없음")

        date = "최근 2시간 조회수 기준"

        news_titles, news_contents, final_urls = [], [], []

        for i, li in enumerate(news_list[:10]):  # TOP 10만 사용
            try:
                link_tag = li.select_one("a")
                if not link_tag:
                    logger.warning(f"뉴스 {i+1}: <a> 태그 없음")
                    continue

                title_el = link_tag.select_one("span.text")
                if not title_el:
                    logger.warning(f"뉴스 {i+1}: 제목(span.text) 없음")
                    continue

                title = title_el.get_text(strip=True)
                link = link_tag.get("href", "")
                if not link:
                    logger.warning(f"뉴스 {i+1}: 링크 없음")
                    continue
                if not link.startswith("http"):
                    link = base_url + link

                content = get_news_content(link)

                news_titles.append(title)
                news_contents.append(content)
                final_urls.append(link)

                logger.info(f"뉴스 {i+1} 추출 성공: {title[:50]}...")
            except Exception as e:
                logger.error(f"뉴스 {i+1} 처리 중 오류: {e}")
                continue

        if not news_titles:
            raise NewsScrapingError("추출된 랭킹 뉴스가 없음")

        return news_titles, news_contents, final_urls, date

    except NewsScrapingError:
        raise
    except Exception as e:
        logger.error(f"랭킹 뉴스 목록 추출 실패: {e}")
        raise NewsScrapingError(f"뉴스 목록 추출 실패: {e}")


def finish_sentence_llm(summary_text, llm):
    prompt = f"""
    다음 문장들의 종결어미 '-니다.'를 삭제하고 개조식으로 간결하게 잘라주세요.
    {summary_text}

    아래 지침을 반드시 지켜야 합니다.

    **지침**
    1. 각 문장은 단어를 짧게 끊는 개조식으로 끝내야 합니다.
    2. 각 번호 앞의 '🚀', '1.', '2.', '3.'은 반드시 그대로 출력에 포함되어야 합니다. 절대 생략하지 마세요.
    3. 형식과 문장이 지침에 준수하는지 한번 더 확인하세요.
    4. 추가 정보를 생성하지 마세요.
    """

    timeout_count = 0
    error_count = 0
    while True:
        try:
            response = invoke_llm_with_timeout(llm, prompt)
            return response.content if hasattr(response, "content") else str(response)
        except TimeoutError:
            timeout_count += 1
            print(f"\n⚠️  Ollama {LLM_TIMEOUT}초 응답 없음! ({timeout_count}/5) GPU VRAM 부족 가능성")
            logger.warning(f"finish_sentence_llm: LLM 타임아웃 ({timeout_count}/5)")
            if timeout_count >= 5:
                logger.error("finish_sentence_llm: 타임아웃 5회 초과 - 프로세스 종료")
                print("\n❌ LLM 타임아웃 5회 초과. Ollama/GPU 상태를 확인하세요. 프로세스를 종료합니다.")
                os._exit(1)
            print(f"   → 30초 대기 후 재시도...")
            time.sleep(30)
        except Exception as e:
            error_count += 1
            logger.error(f"finish_sentence_llm 오류 ({error_count}/3): {e}")
            if error_count >= 3:
                logger.warning("finish_sentence_llm: 오류 3회 초과 - 원본 텍스트 반환")
                return summary_text
            time.sleep(1)

def summarize_individual_news(num: int, title: str, content: str, url: str, llm) -> Optional[str]:
    """개별 뉴스 요약 - 예외처리 강화"""
    try:
        #logger.info(f"뉴스 {num} 요약 시작: {title[:50]}...")
        
        if not content or not content.strip():
            logger.warning(f"뉴스 {num}: 빈 본문 내용, 기본 응답 진입")
            return None
            
        sentences = re.split(r'[.!?]', content)
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if len(sentences) <= 3:
            logger.info(f"뉴스 {num}: 문장이 3개 이하이므로 요약 건너뜀")
            return None
            
        prompt = f"""
        다음 뉴스 기사를 분석하고 개조식으로 간결하게 요약해 주세요.
        제목: {title}
        내용: {content}
        
        정확히 다음 형식을 따라 요약해주세요:
        🚀 #해시태그1 #해시태그2
        1. 핵심 요점 1
        2. 핵심 요점 2
        3. 핵심 요점 3
        
        **주의사항**
        - 각 문장 종결 어미는 단어를 짧게 끊는 개조식으로 끝내세요.
        - 해시태그는 위의 🚀 줄에만 작성하세요. 요점 부분에는 절대 쓰지 마세요.
        - 해시태그 2-3개를 한국어로 작성하세요.
        - 숫자, 소수점, % 등 원문 표기를 그대로 사용하세요.
        - 뉴스의 핵심 내용을 개조식으로 간단히 요약하세요.
        - 인명, 기업명 등 고유명사는 정확하게 써 주세요.
        """

        max_llm_retries = 3
        attempt = 0
        timeout_count = 0
        while True:
            try:
                logger.info(f"LLM 요약 시도 {attempt + 1}/{max_llm_retries}")

                response = invoke_llm_with_timeout(llm, prompt)

                if hasattr(response, "content"):
                    result = response.content
                else:
                    result = str(response)

                if not result or not result.strip():
                    logger.warning(f"뉴스 {num}: 요약 없음")
                    return None

                logger.info(f"뉴스 {num} 요약 성공")
                return result

            except TimeoutError:
                timeout_count += 1
                print(f"\n⚠️  Ollama {LLM_TIMEOUT}초 응답 없음! ({timeout_count}/5) GPU VRAM 부족 가능성")
                logger.warning(f"뉴스 {num}: LLM 타임아웃 ({timeout_count}/5)")
                if timeout_count >= 5:
                    logger.error(f"뉴스 {num}: 타임아웃 5회 초과 - 프로세스 종료")
                    print("\n❌ LLM 타임아웃 5회 초과. Ollama/GPU 상태를 확인하세요. 프로세스를 종료합니다.")
                    os._exit(1)
                print(f"   → 30초 대기 후 재시도...")
                time.sleep(30)

            except Exception as e:
                attempt += 1
                logger.warning(f"LLM 호출 오류 (시도 {attempt}/{max_llm_retries}): {e}")
                if attempt >= max_llm_retries:
                    break
                time.sleep(1)

        raise SummarizeError("모든 LLM 요약 시도 실패")
        
    except SummarizeError:
        raise
    except Exception as e:
        logger.error(f"뉴스 {num} 요약 중 예상치 못한 오류: {e}")
        raise SummarizeError(f"요약 실패: {e}")

def truncate_after_third_point(summary_text: str) -> Tuple[str, bool, bool]:
    """3번 항목 이후 텍스트 정리 - 예외처리 추가"""
    try:
        if not summary_text:
            return "", False, False
            
        # 줄바꿈 정규화
        summary_text = re.sub(r'(\r\n|\r|\n)+', '\n', summary_text)
        summary_text = summary_text.replace('...', '.')
        summary_text = summary_text.replace('**', '')
        
        lines = summary_text.splitlines()
        result_lines = []
        found = False
        next_line_exists = False
        
        for i, line in enumerate(lines):
            result_lines.append(line)
            if re.match(r'^\s*3\.', line):
                found = True
                if i + 1 < len(lines) and lines[i + 1].strip() != '':
                    next_line_exists = True
                break
                
        truncated_text = '\n'.join(result_lines).strip()
        
        # 해시태그 처리
        lines = truncated_text.splitlines()
        all_hashtags = []
        
        for line in lines:
            hashtags_in_line = re.findall(r'#\S+', line)
            all_hashtags.extend(hashtags_in_line)
            
        cleaned_hashtags = [h.replace('_', '') for h in all_hashtags]
        
        if len(cleaned_hashtags) > 3:
            cleaned_hashtags = cleaned_hashtags[:3]
            
        non_hashtag_lines = []
        for line in lines:
            if not re.match(r'^\s*🚀', line) and not re.match(r'^\s*#', line):
                non_hashtag_lines.append(line)
                
        if cleaned_hashtags:
            hashtag_line = '🚀 ' + ' '.join(cleaned_hashtags)
            final_lines = [hashtag_line] + non_hashtag_lines
        else:
            first_line = lines[0] if lines else ""
            if not first_line.startswith('🚀'):
                first_line = '🚀 ' + first_line.strip()
            final_lines = [first_line] + non_hashtag_lines[1:]
            
        new_text = '\n'.join(final_lines)
        return new_text, found, next_line_exists
        
    except Exception as e:
        logger.error(f"텍스트 정리 중 오류: {e}")
        return summary_text, False, False

def is_lines_ending_with_nida(text: str, line_indices: List[int] = [1, 2, 3]) -> bool:
    """문장 종결어미 확인 - 예외처리 추가"""
    try:
        if not text:
            return False
            
        lines = text.strip().splitlines()
        for idx in line_indices:
            if idx < len(lines):
                line = lines[idx].strip()
                if line and line.endswith(('다.', '세요.','니다')):
                    logger.warning(f"라인 {idx}: '{line}' - '-다.'로 끝남")
                    return False
        return True
        
    except Exception as e:
        logger.error(f"종결어미 확인 중 오류: {e}")
        return False

async def send_email_async(to_email: str, subject: str, body: str) -> bool:
    """비동기 이메일 발송 - 예외처리 강화"""
    try:
        logger.info(f"이메일 발송 시작: {to_email}")
        
        if not to_email or not subject or not body:
            raise EmailSendError("이메일 주소, 제목, 또는 본문이 비어있음")
            
        msg = MIMEMultipart()
        
        if isinstance(to_email, list):
            to_email_str = ', '.join(to_email)
        else:
            to_email_str = to_email
            
        msg["From"] = os.environ["SMTP_EMAIL"]
        msg['To'] = to_email_str
        msg['Subject'] = subject
        
        if isinstance(body, list):
            body = '\n\n'.join(body)
            
        msg.attach(MIMEText(body, 'plain'))
        
        # SSL 컨텍스트 생성
        ssl_context = ssl.create_default_context()
        ssl_context.set_ciphers('DEFAULT@SECLEVEL=1')
        
        # SMTP 연결 및 발송
        async with aiosmtplib.SMTP(
            hostname="smtps.hiworks.com",
            port=465,
            use_tls=True,
            tls_context=ssl_context,
        ) as server:
            await server.login(os.environ["SMTP_EMAIL"], os.environ["SMTP_PASSWORD"])
            await server.send_message(msg)
            
        logger.info(f"이메일 발송 성공: {to_email_str} | 발송 시각: {datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')}")
        return True
        
    except aiosmtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP 인증 실패: {e}")
        raise EmailSendError(f"이메일 인증 실패: {e}")
    except aiosmtplib.SMTPConnectError as e:
        logger.error(f"SMTP 연결 실패: {e}")
        raise EmailSendError(f"이메일 서버 연결 실패: {e}")
    except aiosmtplib.SMTPException as e:
        logger.error(f"SMTP 오류: {e}")
        raise EmailSendError(f"이메일 발송 중 SMTP 오류: {e}")
    except Exception as e:
        logger.error(f"이메일 발송 중 예상치 못한 오류: {e}")
        raise EmailSendError(f"이메일 발송 실패: {e}")

async def send_bulk_email(to_emails: List[str], subject: str, body: str) -> List[bool]:
    """대량 이메일 발송 - 예외처리 강화"""
    results = []
    for email in to_emails:
        try:
            result = await send_email_async(email, subject, body)
            results.append(result)
        except EmailSendError as e:
            logger.error(f"이메일 발송 실패 {email}: {e}")
            results.append(False)
        except Exception as e:
            logger.error(f"예상치 못한 이메일 발송 오류 {email}: {e}")
            results.append(False)
            
    success_count = sum(results)
    logger.info(f"이메일 발송 결과: {success_count}/{len(to_emails)} 성공")
    return results

def remove_hashtag_second_line(summary_text):
    """두 번째 줄에 해시태그가 2개 이상 + 1로 시작하지 않으면 삭제"""
    try:
        lines = summary_text.strip().splitlines()
        
        if len(lines) <= 1:
            return summary_text
        
        # 두 번째 줄 체크
        second_line = lines[1].strip()
        hashtags_count = len(re.findall(r'#\S+', second_line))
        
        # 조건: 해시태그 2개 이상 AND 1로 시작하지 않음
        if hashtags_count >= 2 and not second_line.startswith('1.'):
            logger.info(f"두 번째 줄 삭제됨 (해시태그 {hashtags_count}개 + 1로 시작안함): {second_line}")
            # 두 번째 줄 제거
            cleaned_lines = [lines[0]] + lines[2:]  # 첫 번째 + 세 번째부터
            return '\n'.join(cleaned_lines)
        
        return summary_text
        
    except Exception as e:
        logger.error(f"두 번째 줄 정리 중 오류: {e}")
        return summary_text


def check_number_sequence(summary_text):
    """두 번째 줄부터 1., 2., 3. 순서 확인"""
    try:
        lines = summary_text.strip().splitlines()
        
        if len(lines) < 4:  # 최소 첫줄 + 1,2,3 = 4줄
            return False
        
        # 두 번째 줄부터 1., 2., 3. 확인
        expected_numbers = ['1.', '2.', '3.']
        for i, expected in enumerate(expected_numbers):
            line_idx = i + 1  # 두 번째 줄부터 (1, 2, 3번째 인덱스)
            if line_idx >= len(lines):
                logger.info(f"줄 부족: {expected} 줄이 없음")
                return False
            
            line = lines[line_idx].strip()
            if not line.startswith(expected):
                logger.info(f"번호 순서 오류: {line_idx+1}번째 줄 '{line}'이 '{expected}'로 시작하지 않음")
                return False
        
        return True
        
    except Exception as e:
        logger.error(f"번호 순서 확인 중 오류: {e}")
        return False


PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mk_main.pid")

def _kill_previous_instance():
    if not os.path.exists(PID_FILE):
        return
    try:
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        if old_pid == os.getpid():
            return
        import platform
        if platform.system() == "Windows":
            import subprocess
            result = subprocess.run(
                ['taskkill', '/F', '/PID', str(old_pid)],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"이전 프로세스(PID {old_pid}) 종료 완료")
                logger.info(f"이전 프로세스(PID {old_pid}) 종료")
            else:
                logger.warning(f"이전 프로세스 종료 실패: {result.stderr.strip()}")
        else:
            os.kill(old_pid, signal.SIGTERM)
            time.sleep(2)
            try:
                os.kill(old_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            print(f"이전 프로세스(PID {old_pid}) 종료 완료")
            logger.info(f"이전 프로세스(PID {old_pid}) 종료")
    except (ValueError, ProcessLookupError):
        pass
    except Exception as e:
        logger.warning(f"이전 프로세스 종료 실패: {e}")
    finally:
        try:
            os.remove(PID_FILE)
        except Exception:
            pass


def main():
    """메인 실행 함수 - 전체 프로세스 예외처리"""
    _kill_previous_instance()
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    start_time = time.time()
    try:
        logger.info("=== 뉴스 요약 및 메일링 프로세스 시작 ===")
        
        # 1. 뉴스 목록 추출
        try:
            news_titles, news_contents, final_urls, date = get_main_news_list()
        except NewsScrapingError as e:
            logger.error(f"뉴스 수집 실패: {e}")
            return False
            
        # 2. LLM 초기화
        try:
            llm = ChatOllama(model="EEVE-Korean-10.8B:latest")
            logger.info("LLM 초기화 성공")
        except Exception as e:
            logger.error(f"LLM 초기화 실패: {e}")
            return False

        # 모델 warm-up (cold start 로딩 시간 소모)
        print("모델 로딩 중... (최대 5분 소요)")
        try:
            invoke_llm_with_timeout(llm, "안녕")
            print("모델 로딩 완료!")
            logger.info("LLM warm-up 성공")
        except Exception as e:
            logger.warning(f"LLM warm-up 실패 (무시하고 진행): {e}")

        # 3. 뉴스 요약
        summaries = []
        emoji_numbers = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 
                        6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"}
        
        total = len(news_titles)
        for i, (title, content, url) in enumerate(zip(news_titles, news_contents, final_urls)):
            num = i + 1
            num_emoji = emoji_numbers.get(num, str(num))
            print(f"\n[{num}/{total}] {title[:50]}...")
            attempt_count = 0
            MAX_WHILE_RETRIES = 20

            while True:
                try:
                    attempt_count += 1
                    if attempt_count > MAX_WHILE_RETRIES:
                        logger.warning(f"뉴스 {num}: 최대 재시도 {MAX_WHILE_RETRIES}회 초과, default 응답 사용")
                        print(f"  → 최대 재시도 초과, 기본 응답 사용")
                        summary_text = None
                        break
                    if attempt_count > 1:
                        print(f"  → 형식 오류, LLM 재시도 {attempt_count}회차...")
                    summary_text = summarize_individual_news(num, title, content, url, llm)
                except KeyboardInterrupt:
                    print("\n중단되었습니다.")
                    return False
                except SummarizeError as e:
                    logger.error(f"뉴스 {num}: 요약 실패로 프로세스 종료 - {e}")
                    print(f"\n❌ 뉴스 {num} 요약 실패. 프로세스를 종료합니다.")
                    os._exit(1)
                
                if summary_text is None:
                    break
                
                # 1. 기본 패턴 체크
                # '#해시태그', '##', '#요약' 패턴 체크
                if (re.search(r'#\s*해시태그', summary_text) or 
                    '##' in summary_text or 
                    '#요약' in summary_text):
                    logger.info(f"뉴스 {num}: '#해시태그' 패턴 포함됨, 요약 재진행")
                    continue
                
                # 2. 첫 줄에 해시태그가 없으면 요약 재진행
                first_line = summary_text.strip().split('\n')[0]
                if '#' not in first_line:
                    logger.info(f"뉴스 {num}: 첫 줄에 # 없음, 요약 재진행")
                    continue
                
                # 3. 두 번째 줄 해시태그 정리
                cleaned_summary = remove_hashtag_second_line(summary_text)
                
                # 4. 번호 순서 확인 (정리 후)
                if not check_number_sequence(cleaned_summary):
                    logger.info(f"뉴스 {num}: 번호 순서 오류, 요약 재진행")
                    continue
                
                # 모든 조건 통과
                summary_text = cleaned_summary
                break
            
            default_response = ""
            # 나머지 처리
            if summary_text is None or re.search(r'#\s*해시태그', summary_text):
                # 기본 메시지 처리
                if "[속보]" in title:
                    default_response = f"🚀 속보: 자세한 내용은 추후 업데이트될 예정입니다."
                elif "오늘의 운세" in title:
                    default_response = f"🚀 오늘의 운세, 내용을 직접 확인하세요."
                elif "심층기획" in title:
                    default_response = f"🚀 카드뉴스, 본문을 확인하세요."
                else:
                    default_response = f"🚀 본문 요약이 제공되지 않습니다. 내용을 직접 확인해주세요."
            else:
                summary_text_truncated, found, next_line_exists = truncate_after_third_point(summary_text)
                summary_text_truncated = remove_hashtag_second_line(summary_text_truncated)  # 추가 정리
                
                if is_lines_ending_with_nida(summary_text_truncated):
                    formatted_summary = summary_text_truncated
                    logger.info(f"summary_text_truncated*\n{summary_text_truncated}\n")
                else:
                    # API 처리 with 재시도 로직
                    max_api_attempts = 3
                    for attempt in range(max_api_attempts):
                        finish_sentence = finish_sentence_llm(summary_text_truncated, llm)
                        finish_sentence, found, next_line_exists = truncate_after_third_point(finish_sentence)
                        finish_sentence = remove_hashtag_second_line(finish_sentence)  # API 후 정리
                        
                        # API 후 번호 순서 재확인
                        if check_number_sequence(finish_sentence):
                            break
                        else:
                            logger.info(f"API 후 번호 순서 오류 (시도 {attempt + 1}/{max_api_attempts}), API 재실행")
                            if attempt == max_api_attempts - 1:
                                # 최대 시도 후 실패하면 원본 사용
                                finish_sentence = summary_text_truncated
                    
                    logger.info(f"summary_text_truncated*\n{summary_text_truncated}\nfinish_sentence*\n {finish_sentence}\n\n")
                    
                    # 종결어미 체크
                    if not is_lines_ending_with_nida(finish_sentence):
                        logger.info("2차에서도 '-니다' 로 끝나지 않아서 요약 3차 재진행\n")
                        for attempt in range(max_api_attempts):
                            finish_sentence = finish_sentence_llm(summary_text_truncated, llm)
                            finish_sentence = remove_hashtag_second_line(finish_sentence)
                            
                            if check_number_sequence(finish_sentence):
                                break
                            else:
                                logger.info(f"3차 API 후 번호 순서 오류 (시도 {attempt + 1}/{max_api_attempts})")
                                if attempt == max_api_attempts - 1:
                                    finish_sentence = summary_text_truncated
                        
                        logger.info(f"finish_sentence 2*\n {finish_sentence}\n\n")
                    
                    formatted_summary, found, next_line_exists = truncate_after_third_point(finish_sentence)
                    formatted_summary = remove_hashtag_second_line(formatted_summary)  # 최종 정리
            
            # 마지막에 기본 응답이 있으면 할당
            if default_response:
                formatted_summary = default_response
                
            summaries.append({
                "title": title,
                "summary": formatted_summary,
                "url": url,
                "num": num,
                "num_emoji": num_emoji
            })

         # ★ 공통 발송 시각 생성 (위쪽에서 한 번만)
        send_time = datetime.now(timezone(timedelta(hours=9)))
        send_time_str = send_time.strftime("%Y-%m-%d %H:%M")
              
        # 4. 최종 요약문 생성
        try:
            result = f"Right Now! MK's Top 10 NEWS!!!💌🔥 #매경뉴스요약 \n📅 {send_time_str} / {date}\n\n"
            
            for summary in summaries:
                title = summary["title"]
                summary_text = summary["summary"]
                url = summary["url"]
                num_emoji = summary["num_emoji"]
                result += f"📰 {num_emoji} {title}\n{summary_text}\n🔗 {url}\n\n"
                
            result += "매일경제 TOP10 뉴스 요약을 마칩니다! 📰✅ \n*수신거부는 김유정에게 직접 말해주세요\n**뉴스 요약 결과에 오류 발견 시 신고 요망"
            
            # 후처리
            final_summary = re.sub(r'\[?핵심\s?요점\s?\d\]?', '', result)
            final_summary = re.sub(r'\.(?=\n)', '', final_summary)
            final_summary = final_summary.replace(';', '.')
            
            logger.info("최종 요약문 생성 완료")
            
        except Exception as e:
            logger.error(f"최종 요약문 생성 실패: {e}")
            return False
            
        # 5. 이메일 발송
        try:
            to_email_list = os.environ["TO_EMAIL"].split(",")
            subject = f"💌MK TOP10 NEWS SUMMARY TEST {send_time_str}"
            
            results = asyncio.run(send_bulk_email(to_email_list, subject, final_summary))
            
            if any(results):
                logger.info("이메일 발송 완료")
                return True
            else:
                logger.error("모든 이메일 발송 실패")
                return False
                
        except Exception as e:
            logger.error(f"이메일 발송 프로세스 실패: {e}")
            return False
            
    except KeyboardInterrupt:
        print("\n프로세스가 중단되었습니다.")
        logger.info("사용자가 프로세스를 중단했습니다.")
        return False
    except Exception as e:
        logger.error(f"전체 프로세스 중 예상치 못한 오류: {e}")
        return False
    finally:
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
        elapsed = time.time() - start_time
        mins, secs = divmod(int(elapsed), 60)
        print(f"\n총 소요시간: {mins}분 {secs}초")
        logger.info("=== 뉴스 요약 및 메일링 프로세스 종료 ===")
        logger.info("*" * 80 + "\n\n")

if __name__ == "__main__":
    success = main()
    if success:
        print("프로세스가 성공적으로 완료되었습니다.")
    else:
        print("프로세스 실행 중 오류가 발생했습니다. 로그를 확인해주세요.")
