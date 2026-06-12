import re
import requests
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timezone, timedelta

# 로그 설정
logging.basicConfig(
    filename='mk_news_summary_test.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)

# 기본 도메인 설정
base_url = "https://www.mk.co.kr"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def clean_text(text):
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n+', '\n', text)
    return text

def get_news_content(link):
    if not link.startswith("http"):
        link = base_url + link
    news = requests.get(link, headers=headers)
    news_html = BeautifulSoup(news.text, "html.parser")
    content = news_html.select("div.news_cnt_detail_wrap")
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
        # 해시태그만 있는 줄이거나 🚀로 시작하는 줄이 아니라면 추가
        if not re.match(r'^\s*🚀', line) and not re.match(r'^\s*#', line):
            non_hashtag_lines.append(line)
    
    # ★ 첫 줄을 🚀 + 해시태그 줄로 재구성
    if cleaned_hashtags:
        hashtag_line = '🚀 ' + ' '.join(cleaned_hashtags)
        final_lines = [hashtag_line] + non_hashtag_lines
    else:
        # 해시태그가 없으면 원래 첫 줄에 🚀 추가
        first_line = lines[0] if lines else ""
        if not first_line.startswith('🚀'):
            first_line = '🚀 ' + first_line.strip()
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



API_KEY = os.environ["PERPLEXITY_API_KEY"]
API_URL = "https://api.perplexity.ai/chat/completions"
sonar_model="sonar"

def finish_sentence_api(summary_text, sonar_model):
    
    prompt = f"""
다음 요약문을 뉴스 기사 문체로 자연스럽게 다듬어 주세요.
문장의 형식(🚀, 해시태그, 1. 2. 3. 형식 등)은 절대 바꾸지 마세요.
아래 지침을 반드시 지켜야 합니다.

[지침]
1. 각 문장은 '-니다' 또는 '입니다.'로 끝나야 합니다.
2. 문장이 명사형(~함, ~정리 등)으로 끝나도 자연스럽게 마무리합니다.
3. 불필요한 반복은 줄이고, 연결을 자연스럽게 합니다.
4. 숫자, 소수점, % 등 원문 표기를 그대로 사용하세요.
5. 각 번호 앞의 '🚀', '1.', '2.', '3.'은 반드시 그대로 출력에 포함되어야 합니다. 절대 생략하지 마세요.
6. 형식과 문장이 지침에 준수하는지 한번 더 확인하세요.
7. 추가 정보를 생성하지 마세요.

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
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"API 호출 실패: {e}")
        
        return summary_text  # 실패 시 원문 반환

response = requests.get(base_url)
soup = BeautifulSoup(response.text, 'html.parser')
news_list = soup.select('#container > section.contents.main_page.mixcolor > div.mk_head_news_group > div > div > div.col.sub_col > section.news_sec.top_news_sec.is_active > div > div > ul > li')
date_element = soup.select_one('#container > section.contents.main_page.mixcolor > div.mk_head_news_group > div > div > div.col.sub_col > section.news_sec.top_news_sec.is_active > header > div.ttl_area > p.time_info')

if date_element:
    date = date_element.text.strip()
    logger.info(f"뉴스 날짜: {date}")
else:
    logger.warning("*** 날짜 정보를 찾을 수 없습니다.")

news_titles = []
news_contents = []
final_urls = []

for i, news in enumerate(news_list):
    link_tag = news.select_one('a')
    if link_tag:
        title = link_tag.select_one('h3.news_ttl').text.strip().replace('회원용\n\n', '')
        link = link_tag['href']
        content = get_news_content(link)
        news_titles.append(title)
        news_contents.append(content)
        final_urls.append(link)
        # print(f"제목: {title}")
        # print(f"링크: {link}")
        # #print(f"본문:\n{content}")
        # print("-" * 80)
        #logger.info(f"[{i+1}] 제목: {title} | 링크: {link}")

from langchain_ollama import ChatOllama
llm = ChatOllama(model="EEVE-Korean-10.8B:latest")
#llm = ChatOllama(model="kanana15.7b")

def summarize_individual_news(num, title, content, url, llm):
    sentences = re.split(r'[.!?]', content)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) <= 3:
        print(f"뉴스 {num}: 문장이 3개 이하이므로 요약을 건너뜁니다.")
        logger.info(f"뉴스 {num}: 문장이 3개 이하이므로 요약을 건너뜀")
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

for i, (title, content, url) in enumerate(zip(news_titles, news_contents, final_urls)):
    print(f"뉴스 {i+1} 요약 중...")
    num = i + 1
    num_emoji = emoji_numbers.get(num, str(num))

    while True:
        summary_text = summarize_individual_news(num, title, content, url, llm)
        if summary_text is None:
            break
        
        # '#해시태그' 또는 '# 해시태그' 모두 걸러냄
        if re.search(r'#\s*해시태그', summary_text):
            logger.info(f"뉴스 {num}: '#해시태그' 패턴 포함됨, 요약 재진행")
            continue
            
        # 첫 줄에 해시태그가 없으면 요약 재진행
        first_line = summary_text.strip().split('\n')[0]
        if '#' not in first_line:
            logger.info(f"뉴스 {num}: 첫 줄에 # 없음, 요약 재진행")
            continue
        
        # 2,3,4번째 줄이 1., 2., 3.으로 시작하는지 검증
        # ✅ 올바른 코드
        lines = summary_text.strip().splitlines()
        format_ok = True

        for i, expected in enumerate(['1.', '2.', '3.']):
            line_idx = i + 1
            if line_idx < len(lines) and not lines[line_idx].strip().startswith(expected):
                logger.info(f"뉴스 {num}: {line_idx+1}번째 줄 형식 오류, 요약 재진행")
                format_ok = False
                break

        if not format_ok:
            continue  # 요약 재진행

        break  # 모든 조건 통과 시 while문 탈출

    if summary_text is None or re.search(r'#\s*해시태그', summary_text):
        if "[속보]" in title:
            formatted_summary = f"🚀 속보: 자세한 내용은 추후 업데이트될 예정입니다."
        elif "오늘의 운세" in title:
            formatted_summary = f"🚀 오늘의 운세, 내용을 직접 확인하세요."
        else:
            formatted_summary = f"🚀 본문 요약이 제공되지 않습니다. 내용을 직접 확인해주세요."
    else:
        summary_text_truncated, found, next_line_exists = truncate_after_third_point(summary_text)

        if is_lines_ending_with_nida(summary_text_truncated):
            finish_sentence = summary_text_truncated
            logger.info(f"summary_text_truncated*\n{summary_text_truncated}\n")
        else:
            finish_sentence = finish_sentence_api(summary_text_truncated, sonar_model)
            finish_sentence, found, next_line_exists =  truncate_after_third_point(finish_sentence)
            logger.info(f"summary_text_truncated*\n{summary_text_truncated}\nfinish_sentence*\n {finish_sentence}\n\n")
            if not is_lines_ending_with_nida(finish_sentence):
                logger.info("2차에서도 '-니다' 로 끝나지 않아서 요약 3차 재진행\n")
                finish_sentence = finish_sentence_api(summary_text_truncated, sonar_model)
                logger.info(f"finish_sentence 2*\n {finish_sentence}\n\n")

        formatted_summary, found, next_line_exists =  truncate_after_third_point(finish_sentence)

    summaries.append({
        "title": title, 
        "summary": formatted_summary,
        "url": url,
        "num": num,
        "num_emoji": num_emoji
    })

def combine_summaries(summaries):
    result = f"Right Now! MK's Top 10 NEWS!!!💌🔥 #매경뉴스요약 \n📅 {date}\n\n"
    for summary in summaries:
        title = summary["title"]
        summary_text = summary["summary"]
        url = summary["url"]
        num_emoji = summary["num_emoji"]
        result += f"📰 {num_emoji} {title}\n{summary_text}\n🔗 {url}\n\n"
    result += "매일경제 TOP10 뉴스 요약을 마칩니다! 📰✅ \n*수신거부는 김유정에게 직접 말해주세용\n**뉴스 요약 결과에 오류 발견 시 신고 요망"
    return result

final_summary = combine_summaries(summaries)
# LLM에러 후처리
final_summary = re.sub(r'\[?핵심\s?요점\s?\d\]?', '', final_summary)
final_summary = final_summary.replace(';', '.')
#final_summary = final_summary.replace('짐니다.', '졌습니다.')
#final_summary = final_summary.replace('됨니다.', '됐습니다.')
#final_summary = final_summary.replace('됨입니다.', '됐습니다.')
#final_summary = final_summary.replace('냄니다.', '냈습니다.')
#final_summary = final_summary.replace('남니다.', '났습니다.')
#final_summary = final_summary.replace('섬니다.', '섰습니다.')
#final_summary = final_summary.replace('함입니다.', '했습니다.')
#final_summary = final_summary.replace('리셨습니다.', '렸습니다.')
#final_summary = final_summary.replace('돌아가셨습니다.', '사망하였습니다.')
#final_summary = final_summary.replace('하셨습니다.', '하였습니다.')
#final_summary = final_summary.replace('내셨습니다.', '냈습니다.')
#final_summary = final_summary.replace('말씀', '말')

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
        send_time = datetime.now(timezone(timedelta(hours=9)))
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

to_email_list = os.environ["TO_EMAIL"].split(",")
subject = f"💌MK TOP10 NEWS SUMMARY TEST {date[5:16]}"
asyncio.run(send_bulk_email(to_email_list, subject, final_summary))