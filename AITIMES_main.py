import re
import requests
from bs4 import BeautifulSoup
import logging
import signal
import os
from datetime import datetime, timezone, timedelta

def _handle_interrupt(sig, frame):
    print("\n\n프로세스가 중단되었습니다.")
    os._exit(0)

signal.signal(signal.SIGINT, _handle_interrupt)

# 로그 설정
logging.basicConfig(
    filename='aitimes_news_summary_test.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)

# 기본 도메인 설정
base_url = "https://www.aitimes.com/"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

send_time = datetime.now(timezone(timedelta(hours=9)))
date = send_time.strftime('%Y-%m-%d %H:%M')

def clean_text(text):
    # 기자명+이메일 뒤 전체 삭제 (ex: ** 기자 ***@aitimes.com ~ 이후 모두)
    text = re.sub(
        r'(?:[\w\s]+기자\s*)?(?:[A-Za-z0-9._%+-]+@aitimes\.com).*', 
        '', 
        text, 
        flags=re.DOTALL
    )
    # 'AI타임스' + 이메일 패턴도 삭제
    text = re.sub(r'(?:AI타임스\s*)$', '', text, flags=re.DOTALL)
    # 연속된 공백을 하나의 공백으로 대체
    text = re.sub(r'\s+', ' ', text)
    # 연속된 줄바꿈을 하나의 줄바꿈으로 대체
    text = re.sub(r'\n+', '\n', text)
    # 앞뒤 공백 제거
    text = text.strip()
    return text

def get_news_content(link):
    if not link.startswith("http"):
        link = base_url + link
    news = requests.get(link, headers=headers)
    news_html = BeautifulSoup(news.text, "html.parser")
    content = news_html.select("div.article-body")
    if not content:
        content = news_html.select("#articleBody")
    # for element in content:
    #     for br in element.find_all("br"):
    #         br.replace_with(".")
    content_text = ' '.join([element.get_text() for element in content])
    content_text = content_text.replace('사진 확대', '')
    content_text = clean_text(content_text)
    return content_text

def truncate_after_third_point(summary_text):
    # 1. 줄바꿈 정규화
    summary_text = re.sub(r'(\r\n|\r|\n)+', '\n', summary_text)
    summary_text = summary_text.replace('...', '.')
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

    # 2. 모든 해시태그 수집
    lines = truncated_text.splitlines()
    all_hashtags = []
    
    # 모든 줄에서 해시태그 수집
    for line in lines:
        hashtags_in_line = re.findall(r'#\S+', line)
        all_hashtags.extend(hashtags_in_line)
    
    # 해시태그 정리 (언더스코어 제거)
    cleaned_hashtags = [h.replace('_', '') for h in all_hashtags]
    
    # ★ 해시태그가 3개 이상이면 3개만 남김
    if len(cleaned_hashtags) > 3:
        cleaned_hashtags = cleaned_hashtags[:3]
    
    # 해시태그가 아닌 줄들만 남기기
    non_hashtag_lines = []
    for line in lines:
        # 해시태그만 있는 줄이거나 🤖로 시작하는 줄이 아니라면 추가
        if not re.match(r'^\s*🤖', line) and not re.match(r'^\s*#', line):
            non_hashtag_lines.append(line)
    
    # ★ 첫 줄을 🤖 + 해시태그 줄로 재구성
    if cleaned_hashtags:
        hashtag_line = '🤖 ' + ' '.join(cleaned_hashtags)
        final_lines = [hashtag_line] + non_hashtag_lines
    else:
        # 해시태그가 없으면 원래 첫 줄에 🤖 추가
        first_line = lines[0] if lines else ""
        if not first_line.startswith('🤖'):
            first_line = '🤖 ' + first_line.strip()
        final_lines = [first_line] + non_hashtag_lines[1:]
    
    new_text = '\n'.join(final_lines)
    return new_text, found, next_line_exists

def is_lines_ending_with_nida(text, line_indices=[1,2,3]):
    # line_indices: 0-based, 즉 2,3,4번째 줄은 1,2,3
    lines = text.strip().splitlines()

    for idx in line_indices:
        if idx < len(lines):
            if not lines[idx].strip().endswith(('니다.', '세요.')):
                logger.warning("*** -니다로 끝나지 않음")
                return False

    return True

def remove_hashtag_second_line(summary_text):
    """두 번째 줄에 해시태그가 2개 이상 + 1.로 시작하지 않으면 삭제"""
    try:
        lines = summary_text.strip().splitlines()
        if len(lines) <= 1:
            return summary_text
        second_line = lines[1].strip()
        hashtags_count = len(re.findall(r'#\S+', second_line))
        if hashtags_count >= 2 and not second_line.startswith('1.'):
            logger.info(f"두 번째 줄 삭제됨 (해시태그 {hashtags_count}개): {second_line}")
            cleaned_lines = [lines[0]] + lines[2:]
            return '\n'.join(cleaned_lines)
        return summary_text
    except Exception as e:
        logger.error(f"두 번째 줄 정리 중 오류: {e}")
        return summary_text

def check_number_sequence(summary_text):
    """두 번째 줄부터 1., 2., 3. 순서 확인"""
    try:
        lines = summary_text.strip().splitlines()
        if len(lines) < 4:
            return False
        for i, expected in enumerate(['1.', '2.', '3.']):
            line_idx = i + 1
            if line_idx >= len(lines):
                return False
            if not lines[line_idx].strip().startswith(expected):
                return False
        return True
    except Exception as e:
        logger.error(f"번호 순서 확인 중 오류: {e}")
        return False

from dotenv import load_dotenv
load_dotenv()

API_KEY = os.environ["PERPLEXITY_API_KEY"]
API_URL = "https://api.perplexity.ai/chat/completions"
sonar_model = "sonar"

_KNOWN_TERMS = [
    # Claude 모델명
    (r'\bPabl[eé]\b', 'Fable'),
    (r'\bCloud\s+Pabl?\w*\b', 'Claude Fable'),
    (r'\bClaude\s+Pabl[eé]\b', 'Claude Fable'),
    (r'\bOccupus\b', 'Opus'),
    (r'\bOrpheus(?:-level)?\b', 'Opus'),
    # AI 회사/서비스
    (r'\bAntropics?\b', 'Anthropic'),
    (r'\bJemena[iy]\b', 'Gemini'),
    (r'\bJeeminai\b', 'Gemini'),
    (r'\bJemini\b', 'Gemini'),
    (r'\bJambon\b', 'Gemini'),
    (r'\bPurple\s*XYZ\b', 'Perplexity'),
    (r'\bPurplexity\b', 'Perplexity'),
    # Claude 제품
    (r'\bCloud\s+Tags?\b', 'Claude Tags'),
    (r'\bCloud\s+Code\b', 'Claude Code'),
]

def _fix_known_terms(text):
    """EEVE가 음역한 잘못된 브랜드/모델명을 올바른 영어로 교체."""
    for pattern, replacement in _KNOWN_TERMS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _fix_korean_summary_terms(text):
    """한국어 요약에서 영문 약어를 한국어 음절로 쓴 오류 교정 (예: GP트→GPT)."""
    text = re.sub(r'GP트', 'GPT', text)
    text = re.sub(r'챗GPT워크', '챗GPT 워크', text)
    return text


def _extract_title_line(text):
    """번역 결과에서 실제 제목 줄만 추출."""
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        # 한글 포함 줄은 설명 텍스트로 간주하고 스킵
        if any('가' <= c <= '힣' for c in line):
            continue
        # "Overview:", "Title:", "Translation:" 같은 메타 접두어 제거
        line = re.sub(r'^(overview|title|translation|translated\s+title)\s*:\s*', '', line, flags=re.IGNORECASE).strip()
        if not line:
            continue
        # 두 문장이 합쳐진 경우 첫 문장만 사용 (". 소문자" 또는 ". 대문자" 패턴)
        sentence_end = re.search(r'(?<=[.!?])\s+', line)
        if sentence_end and len(line) > 80:
            line = line[:sentence_end.start()].strip()
        # 제목 끝 마침표 제거
        line = line.rstrip('.')
        return line
    return text.strip().split('\n')[0]


def _is_bad_title(result, korean_title):
    """번역 결과가 불량인지 판단."""
    if any('가' <= c <= '힣' for c in result):
        return True
    if len(result) < len(korean_title) * 0.5:
        return True
    if re.search(r'\bKnown\s+terms?\s*:', result, flags=re.IGNORECASE):
        return True
    if result.count('"') % 2 != 0 or result.count("'") % 2 != 0:
        return True
    return False


def _clean_english_body(text, korean_summary=None):
    """번역 결과에서 프리앰블 제거, 🤖 이모지 정상화."""
    lines = text.split('\n')
    start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        # 🤖 또는 이모지+# 줄, 또는 번호 목록 시작
        if s[0] in '🤖✅🔹🔸' or (s[0].isdigit() and len(s) > 1 and s[1] in '.)'):
            start = i
            break
    result_lines = lines[start:]
    result = '\n'.join(result_lines).strip()
    # 첫 줄이 이모지+해시태그 형식인데 🤖이 아닌 경우 교정
    first = result.split('\n')[0].strip() if result else ''
    if first and '#' in first and not first.startswith('🤖'):
        corrected = re.sub(r'^[^\w#\s]*\s*', '🤖 ', first)
        result = corrected + '\n' + '\n'.join(result.split('\n')[1:])
    # 🤖 줄이 없으면 한국어 원본에서 가져오기
    if result and not result.startswith('🤖') and korean_summary:
        ko_robot = next((l.strip() for l in korean_summary.split('\n') if l.strip().startswith('🤖')), None)
        if ko_robot:
            result = ko_robot + '\n' + result
    return result.strip()


_TRANSLATE_TITLE_PROMPT = (
    "Translate this Korean news headline to English. Output only the translated title, nothing else.\n"
    "Known terms: 페이블=Fable, 오퍼스=Opus, 소넷=Sonnet, 하이쿠=Haiku, 클로드=Claude, "
    "앤트로픽=Anthropic, 제미나이=Gemini, 퍼플렉시티=Perplexity, 엑사원=EXAONE, "
    "허깅페이스=HuggingFace, 라마=Llama, 미스트랄=Mistral\n\n"
)

def translate_title(korean_title):
    prompt = _TRANSLATE_TITLE_PROMPT + korean_title
    try:
        llm_en = ChatOllama(model="gpt-oss:20b")
        response = llm_en.invoke(prompt)
        result = response.content if hasattr(response, "content") else str(response)
        result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
        result = _extract_title_line(result)
        if _is_bad_title(result, korean_title):
            raise ValueError(f"번역 결과 불량: {result[:50]}")
        result = _fix_known_terms(result)
        logger.info(f"제목 영어 번역 완료: {result}")
        return result
    except Exception as e:
        logger.error(f"제목 영어 번역 실패: {e} → EEVE 폴백")
        try:
            response = llm.invoke(prompt)
            result = response.content if hasattr(response, "content") else str(response)
            result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
            result = _extract_title_line(result)
            if _is_bad_title(result, korean_title):
                logger.warning(f"제목 EEVE 번역 결과 불량 → 단순 프롬프트로 재시도: {result[:50]}")
                simple_prompt = f"Translate to English (title only, no explanation):\n{korean_title}"
                response2 = llm.invoke(simple_prompt)
                result = response2.content if hasattr(response2, "content") else str(response2)
                result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
                result = _extract_title_line(result)
                if _is_bad_title(result, korean_title):
                    logger.warning(f"제목 EEVE 재시도도 불량 → 원본 반환")
                    return korean_title
            result = _fix_known_terms(result)
            logger.info(f"제목 영어 번역 EEVE 완료: {result}")
            return result
        except Exception as e2:
            logger.error(f"제목 영어 번역 EEVE 폴백도 실패: {e2}")
            return korean_title


def translate_to_english(korean_summary):
    prompt = f"""Translate the following Korean news summary into natural English.

Rules:
- Keep the exact format: 🤖 line first, then 1. 2. 3.
- Each numbered point must be a complete sentence with a clear subject (e.g. "Google released..." not "Released...").
- Do NOT use a comma after the subject (e.g. "Google releases" not "Google, releases").
- Translate hashtags to short English CamelCase words. Max 2 words per hashtag. No underscores. (e.g. #AIRegulation, #PriceWar, #ChineseAI — NOT #AIcostsforbusinesses or #pricingcompetition)
- Known terms: 페이블=Fable, 오퍼스=Opus, 소넷=Sonnet, 하이쿠=Haiku, 클로드=Claude, 앤트로픽=Anthropic, 제미나이=Gemini, 퍼플렉시티=Perplexity, 엑사원=EXAONE, 허깅페이스=HuggingFace
- Keep proper nouns accurate (e.g. "Claude" not "Cloud", model names exactly as written).
- Do not add or remove any information.

{korean_summary}
"""
    logger.info("영어 번역 시작")
    try:
        llm_en = ChatOllama(model="gpt-oss:20b")
        response = llm_en.invoke(prompt)
        result = response.content if hasattr(response, "content") else str(response)
        result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
        result = _fix_known_terms(_clean_english_body(result))
        logger.info(f"영어 번역 완료:\n{result}")
        return result
    except Exception as e:
        logger.error(f"영어 번역 실패: {e}")
        logger.info("영어 번역 실패 → EEVE 폴백")
        print(f"  → 영어 번역 실패, EEVE로 대체 처리 중...")
        try:
            response = llm.invoke(prompt)
            result = response.content if hasattr(response, "content") else str(response)
            result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
            result = _fix_known_terms(_clean_english_body(result))
            logger.info(f"영어 번역 EEVE 폴백 완료:\n{result}")
            return result
        except Exception as e2:
            logger.error(f"영어 번역 EEVE 폴백도 실패: {e2}")
            return None


def finish_sentence_api(summary_text, sonar_model):

    prompt = f"""
다음 요약문의 종결어미를 제거하고 개조식으로 간결하게 다듬어 주세요.
문장의 형식(🤖해시태그, 1. 2. 3. 형식 등)은 절대 바꾸지 마세요.
아래 지침을 반드시 지켜야 합니다.

[지침]
1. 각 문장은 '~함', '~됨', '~임', '~예정' 등 명사형(개조식)으로 끝나야 합니다. '-니다'로 끝나지 마세요.
2. 불필요한 반복은 줄이고, 연결을 자연스럽게 합니다.
3. 숫자, 소수점, % 등 원문 표기를 그대로 사용하세요.
4. 각 번호 앞의 '🤖', '1.', '2.', '3.'은 반드시 그대로 출력에 포함되어야 합니다. 절대 생략하지 마세요.
5. 형식과 문장이 지침에 준수하는지 한번 더 확인하세요.
6. 추가 정보를 생성하지 마세요.

[요약문]
{summary_text}
"""
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": sonar_model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }
    
    def _is_valid_summary(text, original=None):
        """출력이 정상 요약 형식인지 확인 (프롬프트 지침 내용 반환 방지 + 언어 일치 확인)."""
        if '[지침]' in text:
            return False
        if not re.search(r'🤖\s*#', text):
            return False
        if original:
            has_korean_input = any('가' <= c <= '힣' for c in original)
            has_korean_output = any('가' <= c <= '힣' for c in text)
            if has_korean_input and not has_korean_output:
                return False
        return True

    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        result = data["choices"][0]["message"]["content"].strip()
        if not _is_valid_summary(result, summary_text):
            logger.warning("finish_sentence_api: Perplexity 출력 비정상 → 원문 사용")
            return summary_text
        return result
    except Exception as e:
        logger.error(f"API 호출 실패: {e}")
        logger.info("Perplexity API 실패 → EEVE 모델로 폴백")
        print(f"  → Perplexity API 실패, EEVE로 대체 처리 중...")
        try:
            response = llm.invoke(prompt)
            result = response.content if hasattr(response, "content") else str(response)
            result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
            if not _is_valid_summary(result, summary_text):
                logger.warning("finish_sentence_api: EEVE 출력 비정상 (영어 번역 또는 [지침] 반환 등) → 원문 사용")
                return summary_text
            return result
        except Exception as e2:
            logger.error(f"EEVE 폴백도 실패: {e2}")
            return summary_text  # 둘 다 실패 시 원문 반환

response = requests.get(base_url)
soup = BeautifulSoup(response.text, 'html.parser')
most_popular_section = soup.select_one('div.index-item.grid-2')
news_list = most_popular_section.select('div.auto-article > div.item') if most_popular_section else []


news_titles = []
news_contents = []
final_urls = []

for i, news in enumerate(news_list):
    link_tag = news.select_one('a')
    if link_tag:
        title_tag = link_tag.select_one('h2.auto-titles.line-x2.onload')
        if title_tag:
            title = title_tag.text.strip()
        else:
            title = None
        link = link_tag['href']
        content = get_news_content(link)
        news_titles.append(title)
        news_contents.append(content)
        final_urls.append(link)


from langchain_ollama import ChatOllama
llm = ChatOllama(model="EEVE-Korean-10.8B:latest")

def summarize_individual_news(num, title, content, url, llm):
    sentences = re.split(r'[.!?]', content)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) <= 3:
        print(f"뉴스 {num}: 문장이 3개 이하이므로 요약을 건너뜁니다.")
        logger.info(f"뉴스 {num}: 문장이 3개 이하이므로 요약을 건너뜀")
        return None
    prompt = f"""
    다음 뉴스 기사를 분석하고 요약해 주세요.
    제목: {title}
    내용: {content}
    
    정확히 다음 형식을 따라 요약해주세요:
    
    🤖 #해시태그1 #해시태그2
    1.핵심 요점 1
    2.핵심 요점 2
    3.핵심 요점 3
    
    **주의사항**
    - 해시태그 2-3개를 한국어로 작성하세요.
    - 숫자, 소수점, % 등 원문 표기를 그대로 사용하세요.
    - 뉴스의 핵심 내용을 간단한 한 문장으로 요약하세요.
    - 인명, 기업명 등 고유명사는 정확하게 써 주세요.
    - 모든 중요한 정보가 포함되었는지 확인하세요.
    - 제공된 뉴스 내용만 사용하고 추가 정보를 생성하지 마세요.
    """
    response = llm.invoke(prompt)
    if hasattr(response, "content"):
        return response.content
    else:
        return str(response)

summaries = []
emoji_numbers = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"}

total = len(news_titles)
for i, (title, content, url) in enumerate(zip(news_titles, news_contents, final_urls)):
    num = i + 1
    num_emoji = emoji_numbers.get(num, str(num))
    print(f"\n[{num}/{total}] {title[:50]}...")
    attempt_count = 0
    MAX_WHILE_RETRIES = 20

    while True:
        attempt_count += 1
        if attempt_count > MAX_WHILE_RETRIES:
            logger.warning(f"뉴스 {num}: 최대 재시도 {MAX_WHILE_RETRIES}회 초과, default 응답 사용")
            print(f"  → 최대 재시도 초과, 기본 응답 사용")
            summary_text = None
            break
        if attempt_count > 1:
            print(f"  → 형식 오류, LLM 재시도 {attempt_count}회차...")

        summary_text = summarize_individual_news(num, title, content, url, llm)
        if summary_text is None:
            break

        # 기본 패턴 체크
        if (re.search(r'#\s*해시태그', summary_text) or
                '##' in summary_text or
                '#요약' in summary_text):
            logger.info(f"뉴스 {num}: '#해시태그' 패턴 포함됨, 요약 재진행")
            continue

        # 첫 줄에 해시태그가 없으면 요약 재진행
        first_line = summary_text.strip().split('\n')[0]
        if '#' not in first_line:
            logger.info(f"뉴스 {num}: 첫 줄에 # 없음, 요약 재진행")
            continue

        # 두 번째 줄 해시태그 정리
        cleaned_summary = remove_hashtag_second_line(summary_text)

        # 번호 순서 확인
        if not check_number_sequence(cleaned_summary):
            logger.info(f"뉴스 {num}: 번호 순서 오류, 요약 재진행")
            continue

        summary_text = cleaned_summary
        break


    if summary_text is None or re.search(r'#\s*해시태그', summary_text):
        if "[속보]" in title:
            formatted_summary = f"🤖 속보: 자세한 내용은 추후 업데이트될 예정입니다."
        else:
            formatted_summary = f"🤖 본문 요약이 제공되지 않습니다. 내용을 직접 확인해주세요."
    else:
        summary_text_truncated, found, next_line_exists = truncate_after_third_point(summary_text)
        summary_text_truncated = remove_hashtag_second_line(summary_text_truncated)

        # 원본 🤖 해시태그 줄 보존 (finish_sentence_api가 영어로 바꾸는 것 방지)
        original_robot_line = next(
            (l.strip() for l in summary_text_truncated.split('\n') if l.strip().startswith('🤖')), None
        )

        max_api_attempts = 3
        finish_sentence = finish_sentence_api(summary_text_truncated, sonar_model)
        finish_sentence, _, _ = truncate_after_third_point(finish_sentence)
        finish_sentence = remove_hashtag_second_line(finish_sentence)

        for attempt in range(max_api_attempts - 1):
            if check_number_sequence(finish_sentence):
                break
            logger.info(f"API 후 번호 순서 오류 (시도 {attempt + 1}/{max_api_attempts}), API 재실행")
            finish_sentence = finish_sentence_api(summary_text_truncated, sonar_model)
            finish_sentence, _, _ = truncate_after_third_point(finish_sentence)
            finish_sentence = remove_hashtag_second_line(finish_sentence)
        else:
            if not check_number_sequence(finish_sentence):
                finish_sentence = summary_text_truncated

        # 🤖 줄이 바뀌었으면 원본으로 복원
        if original_robot_line:
            lines = finish_sentence.split('\n')
            robot_idx = next((i for i, l in enumerate(lines) if l.strip().startswith('🤖')), None)
            if robot_idx is not None and lines[robot_idx].strip() != original_robot_line:
                lines[robot_idx] = original_robot_line
                finish_sentence = '\n'.join(lines)

        logger.info(f"summary_text_truncated*\n{summary_text_truncated}\nfinish_sentence*\n {finish_sentence}\n\n")
        formatted_summary, _, _ = truncate_after_third_point(finish_sentence)
        formatted_summary = remove_hashtag_second_line(formatted_summary)
        formatted_summary = _fix_korean_summary_terms(formatted_summary)

    print(f"  → 영어 번역 중...")
    logger.info(f"뉴스 {num} 영어 번역 요청")
    title_en = translate_title(title)
    en_summary = translate_to_english(formatted_summary)
    if en_summary:
        logger.info(f"뉴스 {num} 영어 번역 성공")
    else:
        logger.warning(f"뉴스 {num} 영어 번역 결과 없음")

    summaries.append({
        "title": title,
        "title_en": title_en,
        "summary": formatted_summary,
        "summary_en": en_summary,
        "url": url,
        "num": num,
        "num_emoji": num_emoji
    })

def combine_summaries(summaries):
    result = f"Right Now! AI TIMES Headline NEWS!!!💌🔥\n📅 {date}\n\n"
    for summary in summaries:
        title = summary["title"]
        summary_text = summary["summary"]
        url = summary["url"]
        num_emoji = summary["num_emoji"]
        result += f"📰 {num_emoji} {title}\n{summary_text}\n🔗 {url}\n\n"
    result += "AI TIMES Headlines 뉴스 요약을 마칩니다! 📰✅ \n*수신거부는 김유정에게 직접 말해주세용\n**뉴스 요약 결과에 오류 발견 시 신고 요망"

    result += "\n\n" + "=" * 40 + "\n"
    result += f"📢 English Version\n\n"
    for summary in summaries:
        en = summary.get("summary_en")
        if not en:
            en = "🤖 Summary not available."
        en_title = summary.get("title_en") or summary["title"]
        num_emoji = summary["num_emoji"]
        result += f"📰 {num_emoji} {en_title}\n{en}\n🔗 {summary['url']}\n\n"
    result += "That's a wrap for AI TIMES Headlines! 📰✅ \n*To unsubscribe, please contact Kim Yujeong directly\n**Please report any errors found in the news summaries"

    return result

final_summary = combine_summaries(summaries)
# LLM에러 후처리
final_summary = re.sub(r'\[?핵심\s?요점\s?\d\]?', '', final_summary)
final_summary = re.sub(r'\[해시\s?태그\s?\]', '', final_summary)
final_summary = final_summary.replace(';', '.')
final_summary = re.sub(r'챗\s*[Gg][Pp]트', 'ChatGPT', final_summary)
final_summary = re.sub(r'[Gg][Pp]트', 'GPT', final_summary)


logger.info("최종 뉴스 요약 결과:\n%s", final_summary)

import ssl
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import asyncio
import aiosmtplib

smtp_server = "smtps.hiworks.com"
smtp_port = 465
from_email = os.environ["SMTP_EMAIL"]
password = os.environ["SMTP_PASSWORD"]

async def send_email_async(to_email, subject, body):
    try:
        msg = MIMEMultipart()
        if isinstance(to_email, list):
            to_email_str = ', '.join(to_email)
            recipients = to_email
        else:
            to_email_str = to_email
            recipients = [to_email]

        msg['From'] = from_email
        msg['To'] = to_email_str
        msg['Subject'] = subject
        if isinstance(body, list):
            body = '\n\n'.join(body)
        msg.attach(MIMEText(body, 'plain'))

        # ★ SSL 컨텍스트 생성 (핸드셰이크 실패 방지용)
        ssl_context = ssl.create_default_context()
        ssl_context.set_ciphers('DEFAULT@SECLEVEL=1')

        # 465포트로 SSL 연결
        async with aiosmtplib.SMTP(
            hostname=smtp_server,
            port=smtp_port,
            use_tls=True,              # TLS 연결 즉시 사용
            tls_context=ssl_context,   # 만든 컨텍스트 지정
        ) as server:
            await server.login(from_email, password)
            await server.send_message(msg)

        # 메일 보낸 시간 (한국시간)
        #send_time = datetime.now(timezone(timedelta(hours=9)))
        logger.info(f"이메일 전송 성공: {to_email_str} | 발송 시각: {send_time.strftime('%Y-%m-%d %H:%M:%S')}")
        return True
    except Exception as e:
        logger.exception(f"이메일 전송 중 오류 발생: {e}")
        return False

async def send_bulk_email(to_emails, subject, body):
    results = []
    for email in to_emails:
        result = await send_email_async(email, subject, body)
        results.append(result)
    logger.info("-" * 80 + "\n\n")
    return results

to_email_list = os.environ["AITIMES_TO_EMAIL"].split(",")
subject = f"📰🤖AI TIMES NEWS SUMMARY TEST {date[5:16]}"
asyncio.run(send_bulk_email(to_email_list, subject, final_summary))
