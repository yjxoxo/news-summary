import re
import requests
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timezone, timedelta
import time
import json
from typing import Optional, List, Dict, Tuple
from requests.exceptions import RequestException, Timeout, ConnectionError, HTTPError
import ssl
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import asyncio
import aiosmtplib
from langchain_ollama import ChatOllama

# 로그 설정 개선
logging.basicConfig(
    filename='mk_news_summary_test.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    encoding='utf-8'
)

logger = logging.getLogger(__name__)

# 전역 설정
base_url = "https://www.mk.co.kr"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# API 설정
API_KEY = os.environ["PERPLEXITY_API_KEY"]
API_URL = "https://api.perplexity.ai/chat/completions"
sonar_model = "sonar"

# 재시도 설정
MAX_RETRIES = 3
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
    """뉴스 본문 추출 함수 - 예외처리 강화"""
    try:            
        if not link.startswith("http"):
            link = base_url + link
            
        response = safe_request(link)
        if not response:
            raise NewsScrapingError(f"뉴스 페이지 요청 실패: {link}")
            
        try:
            news_html = BeautifulSoup(response.text, "html.parser")
        except Exception as e:
            raise NewsScrapingError(f"HTML 파싱 실패: {e}")
            
        # 본문 선택자 시도
        content_selectors = [
            "div.news_cnt_detail_wrap",
            "#articleBody",
            ".article_txt",
            ".news_content"
        ]
        
        content = None
        for selector in content_selectors:
            content = news_html.select(selector)
            if content:
                #logger.info(f"본문 추출 성공 (선택자: {selector})")
                break
                
        if not content:
            logger.warning(f"본문을 찾을 수 없음: {link}")
            # 페이지 전체 텍스트에서 추출 시도
            page_text = news_html.get_text()
            if len(page_text) > 100:
                content_text = page_text[:1000]  # 처음 1000자만
                #logger.info("페이지 전체 텍스트에서 부분 추출")
            else:
                logger.warning(f"본문 내용이 너무 짧음: {link}")
                return ""  
        else:
            content_text = ' '.join([element.get_text() for element in content])
            
        # 본문 정리
        content_text = content_text.replace('사진 확대', '')
        content_text = clean_text(content_text)
        
        if len(content_text.strip()) < 50:
            logger.warning(f"추출 내용이 너무 짧음: {link}")
            return ""            
        return content_text
        
    except Exception as e:
        logger.error(f"본문 추출 실패: {e}")
        return ""

def get_main_news_list() -> Tuple[List[str], List[str], List[str], str]:
    """메인 뉴스 목록 추출 - 예외처리 강화"""
    try:
        response = safe_request(base_url)
        if not response:
            raise NewsScrapingError("메인 페이지 요청 실패")
            
        try:
            soup = BeautifulSoup(response.text, 'html.parser')
        except Exception as e:
            raise NewsScrapingError(f"메인 페이지 HTML 파싱 실패: {e}")
            
        # 뉴스 목록 선택자 시도
        news_selectors = [
            '#container > section.contents.main_page.mixcolor > div.mk_head_news_group > div > div > div.col.sub_col > section.news_sec.top_news_sec.is_active > div > div > ul > li',
            '.top_news_sec li',
            '.news_sec li'
        ]
        
        news_list = None
        for selector in news_selectors:
            news_list = soup.select(selector)
            if news_list:
                #logger.info(f"뉴스 목록 추출 성공 (선택자: {selector}): {len(news_list)}개")
                break
                
        if not news_list:
            raise NewsScrapingError("뉴스 목록을 찾을 수 없음")
            
        # 날짜 정보 추출
        date_selectors = [
            '#container > section.contents.main_page.mixcolor > div.mk_head_news_group > div > div > div.col.sub_col > section.news_sec.top_news_sec.is_active > header > div.ttl_area > p.time_info',
            '.time_info',
            '.date_info'
        ]
        
        date = None
        for selector in date_selectors:
            date_element = soup.select_one(selector)
            if date_element:
                date = date_element.text.strip()
                break
                
        if not date:
            date = datetime.now(timezone(timedelta(hours=9))).strftime('%Y.%m.%d %H:%M')
            logger.warning(f"날짜 정보를 찾을 수 없어 현재 시간 사용: {date}")
            
        # 뉴스 정보 추출
        news_titles = []
        news_contents = []
        final_urls = []
        
        for i, news in enumerate(news_list):
            try:
                link_tag = news.select_one('a')
                if not link_tag:
                    logger.warning(f"뉴스 {i+1}: 링크 태그를 찾을 수 없음")
                    continue
                    
                title_element = link_tag.select_one('h3.news_ttl')
                if not title_element:
                    # 다른 제목 선택자 시도
                    title_element = link_tag.select_one('h3') or link_tag.select_one('.title')
                    
                if not title_element:
                    logger.warning(f"뉴스 {i+1}: 제목을 찾을 수 없음")
                    continue
                    
                title = title_element.text.strip().replace('회원용\n\n', '')
                link = link_tag.get('href', '')
                
                if not title or not link:
                    logger.warning(f"뉴스 {i+1}: 제목 또는 링크가 비어있음")
                    continue
                    
                try:
                    content = get_news_content(link)
                    news_titles.append(title)
                    news_contents.append(content)
                    final_urls.append(link if link.startswith('http') else base_url + link)
                    
                    #logger.info(f"뉴스 {i+1} 추출 성공: {title[:50]}...")
                    
                except NewsScrapingError as e:
                    logger.error(f"뉴스 {i+1} 본문 추출 실패: {e}")
                    continue
                    
            except Exception as e:
                logger.error(f"뉴스 {i+1} 처리 중 오류: {e}")
                continue
                
        if not news_titles:
            raise NewsScrapingError("추출된 뉴스가 없음")
            
        logger.info(f"메인 뉴스 목록 추출 완료: {len(news_titles)}개")
        return news_titles, news_contents, final_urls, date
        
    except NewsScrapingError:
        raise
    except Exception as e:
        logger.error(f"메인 뉴스 목록 추출 중 예상치 못한 오류: {e}")
        raise NewsScrapingError(f"뉴스 목록 추출 실패: {e}")

def finish_sentence_api(summary_text: str, model: str, retries: int = MAX_RETRIES) -> str:
    """API를 통한 문장 다듬기 - 예외처리 강화"""
    prompt = f"""
    다음 요약문을 뉴스 기사 문체로 자연스럽게 다듬어 주세요.
    문장의 형식(🚀, 해시태그, 1. 2. 3. 형식 등)은 절대 바꾸지 마세요.

    [지침]
    1. 각 문장은 '-니다' 또는 '입니다.'로 끝나야 합니다.
    2. 문장이 개조식(~함, ~정리 등)으로 끝나도 자연스럽게 마무리합니다.
    3. 불필요한 반복은 줄이고, 연결을 자연스럽게 합니다.
    4. 숫자, 소수점, % 등 원문 표기를 그대로 사용하세요.
    5. 각 번호 앞의 '🚀', '1.', '2.', '3.'은 반드시 그대로 출력에 포함되어야 합니다.

    [요약문]
    {summary_text}
    """

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    
    for attempt in range(retries):
        try:
            logger.info(f"API 문장 다듬기 시도 {attempt + 1}/{retries}")
            
            response = requests.post(
                API_URL, 
                headers=headers, 
                json=payload, 
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                raise SummarizeError(f"API 응답 JSON 파싱 실패: {e}")
                
            if "choices" not in data or not data["choices"]:
                raise SummarizeError("API 응답에 choices가 없음")
                
            if "message" not in data["choices"][0] or "content" not in data["choices"][0]["message"]:
                raise SummarizeError("API 응답 형식이 올바르지 않음")
                
            result = data["choices"][0]["message"]["content"].strip()
            
            if not result:
                raise SummarizeError("API 응답이 비어있음")
                
            logger.info("API 문장 다듬기 성공")
            return result
            
        except Timeout as e:
            logger.warning(f"API 요청 시간 초과 (시도 {attempt + 1}/{retries}): {e}")
        except HTTPError as e:
            logger.warning(f"API HTTP 오류 (시도 {attempt + 1}/{retries}): {e.response.status_code}")
        except SummarizeError as e:
            logger.warning(f"API 응답 처리 오류 (시도 {attempt + 1}/{retries}): {e}")
        except Exception as e:
            logger.warning(f"API 호출 중 예상치 못한 오류 (시도 {attempt + 1}/{retries}): {e}")
            
        if attempt < retries - 1:
            time.sleep(RETRY_DELAY)
    
    logger.error("API 문장 다듬기 모든 시도 실패, 원문 반환")
    return summary_text

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
        다음 뉴스 기사를 분석하고 종결어미를 '-니다'로 자연스럽게 요약해 주세요.

        제목: {title}
        내용: {content}

        정확히 다음 형식을 따라 요약해주세요:
        🚀 #해시태그1 #해시태그2
        1.핵심 요점 1
        2.핵심 요점 2  
        3.핵심 요점 3

        **주의사항**
        - 해시태그 2-3개를 한국어로 작성하세요.
        - 숫자, 소수점, % 등 원문 표기를 그대로 사용하세요.
        - 뉴스의 핵심 내용을 간단한 한 문장으로 요약하세요.
        - 인명, 기업명 등 고유명사는 정확하게 써 주세요.
        """

        max_llm_retries = 3
        for attempt in range(max_llm_retries):
            try:
                logger.info(f"LLM 요약 시도 {attempt + 1}/{max_llm_retries}")
                
                response = llm.invoke(prompt)
                
                if hasattr(response, "content"):
                    result = response.content
                else:
                    result = str(response)
                    
                if not result or not result.strip():
                    logger.warning(f"뉴스 {num}: 요약 없음")
                    return None
                    
                # 응답 형식 검증
                #if '🚀' not in result or '#' not in result:
                #    logger.warning(f"LLM 응답 형식 오류 (시도 {attempt + 1}): 🚀 또는 # 누락")
                #    logger.warning(f"LLM 응답: {result}")
                #    if attempt < max_llm_retries - 1:
                #        continue
                        
                logger.info(f"뉴스 {num} 요약 성공")
                return result
                
            except Exception as e:
                logger.warning(f"LLM 호출 오류 (시도 {attempt + 1}/{max_llm_retries}): {e}")
                if attempt < max_llm_retries - 1:
                    time.sleep(1)
                    
        raise SummarizeError("모든 LLM 요약 시도 실패")
        
    except SummarizeError:
        raise
    except Exception as e:
        logger.error(f"뉴스 {num} 요약 중 예상치 못한 오류: {e}")
        raise SummarizeError(f"요약 실패: {e}")

def remove_hashtag_second_line(summary_text):
    """두 번째 줄에 해시태그가 2개 이상 있으면 삭제"""
    try:
        lines = summary_text.strip().splitlines()
        
        if len(lines) <= 1:
            return summary_text
        
        # 두 번째 줄 체크
        second_line = lines[1]
        hashtags_count = len(re.findall(r'#\S+', second_line))
        
        if hashtags_count >= 2:
            logger.info(f"두 번째 줄 삭제됨 (해시태그 {hashtags_count}개): {second_line}")
            # 두 번째 줄 제거
            cleaned_lines = [lines[0]] + lines[2:]  # 첫 번째 + 세 번째부터
            return '\n'.join(cleaned_lines)
        
        return summary_text
        
    except Exception as e:
        logger.error(f"두 번째 줄 정리 중 오류: {e}")
        return summary_text


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
                if line and not line.endswith(('니다.', '세요.', '습니다.')):
                    logger.warning(f"라인 {idx}: '{line}' - 올바른 종결어미 없음")
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
            recipients = to_email
        else:
            to_email_str = to_email
            recipients = [to_email]
            
        msg['From'] = os.environ["SMTP_EMAIL"]
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
            
        send_time = datetime.now(timezone(timedelta(hours=9)))
        logger.info(f"이메일 발송 성공: {to_email_str} | 발송 시각: {send_time.strftime('%Y-%m-%d %H:%M:%S')}")
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


def main():
    """메인 실행 함수 - 전체 프로세스 예외처리"""
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
            
        # 3. 뉴스 요약
        summaries = []
        emoji_numbers = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 
                        6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"}
        
        for i, (title, content, url) in enumerate(zip(news_titles, news_contents, final_urls)):
            print(f"뉴스 {i+1} 요약 중...")
            num = i + 1
            num_emoji = emoji_numbers.get(num, str(num))
            
            while True:
                summary_text = summarize_individual_news(num, title, content, url, llm)
                
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
                        finish_sentence = finish_sentence_api(summary_text_truncated, sonar_model)
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
                            finish_sentence = finish_sentence_api(summary_text_truncated, sonar_model)
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

                
        # 4. 최종 요약문 생성
        try:
            result = f"Right Now! MK's Top 10 NEWS!!!💌🔥 #매경뉴스요약 \n📅 {date}\n\n"
            
            for summary in summaries:
                title = summary["title"]
                summary_text = summary["summary"]
                url = summary["url"]
                num_emoji = summary["num_emoji"]
                result += f"📰 {num_emoji} {title}\n{summary_text}\n🔗 {url}\n\n"
                
            result += "매일경제 TOP10 뉴스 요약을 마칩니다! 📰✅ \n*수신거부는 김유정에게 직접 말해주세용\n**뉴스 요약 결과에 오류 발견 시 신고 요망"
            
            # 후처리
            final_summary = re.sub(r'\[?핵심\s?요점\s?\d\]?', '', result)
            final_summary = final_summary.replace(';', '.')
            
            logger.info("최종 요약문 생성 완료")
            
        except Exception as e:
            logger.error(f"최종 요약문 생성 실패: {e}")
            return False
            
        # 5. 이메일 발송
        try:
            to_email_list = os.environ["TO_EMAIL"].split(",")
            subject = f"💌MK TOP10 NEWS SUMMARY TEST {date[5:16] if len(date) > 16 else date}"
            
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
            
    except Exception as e:
        logger.error(f"전체 프로세스 중 예상치 못한 오류: {e}")
        return False
    finally:
        logger.info("=== 뉴스 요약 및 메일링 프로세스 종료 ===")
        logger.info("*" * 80 + "\n\n")

if __name__ == "__main__":
    success = main()
    if success:
        print("프로세스가 성공적으로 완료되었습니다.")
    else:
        print("프로세스 실행 중 오류가 발생했습니다. 로그를 확인해주세요.")
