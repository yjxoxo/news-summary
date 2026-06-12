# news-summary

AI 뉴스 자동 요약 및 이메일 발송 스크립트입니다.

## 스크립트 소개

| 파일 | 대상 | 설명 |
|------|------|------|
| `AITIMES_main.py` | [AI타임스](https://www.aitimes.com/) | 인기 헤드라인 스크래핑 → 한국어 요약 + 영어 번역 → 이메일 발송 |
| `mk_main_260121.py` | [매일경제](https://www.mk.co.kr/news/ranking) | TOP 10 랭킹 뉴스 스크래핑 → 한국어 요약 → 이메일 발송 |

## 동작 흐름

```
뉴스 스크래핑
    ↓
EEVE-Korean-10.8B (Ollama) — 한국어 요약 생성
    ↓
Perplexity API — 문장 다듬기 (실패 시 EEVE 폴백)
    ↓
llama3.3:70b (Ollama) — 영어 번역 (AITIMES only)
    ↓
이메일 발송 (SMTP)
```

## 요구사항

- Python 3.10+
- [Ollama](https://ollama.com/) 실행 중
  - `EEVE-Korean-10.8B:latest`
  - `llama3.3:70b` (AITIMES 영어 번역용)
- Perplexity API 키 (없으면 EEVE로 자동 폴백)

### 패키지 설치

```bash
pip install requests beautifulsoup4 langchain-ollama aiosmtplib python-dotenv
```

## 설정

프로젝트 루트에 `.env` 파일 생성 (`.env.example` 참고):

```
PERPLEXITY_API_KEY=your_perplexity_api_key
SMTP_EMAIL=your_email@example.com
SMTP_PASSWORD=your_smtp_password
AITIMES_TO_EMAIL=recipient@example.com,recipient2@example.com
MK_TO_EMAIL=recipient@example.com,recipient2@example.com
```

수신자 여러 명은 쉼표로 구분합니다. AI타임스와 매일경제 수신자를 별도로 설정할 수 있습니다.

## 실행

```bash
# AI타임스 요약
python AITIMES_main.py

# 매일경제 TOP10 요약
python mk_main_260121.py
```

## 이메일 형식

**한국어 섹션**
```
📰 1️⃣ 뉴스 제목
🤖 #해시태그1 #해시태그2
1. 핵심 요점 1
2. 핵심 요점 2
3. 핵심 요점 3
🔗 기사 링크
```

**English Section** (AITIMES only)
```
📰 1️⃣ News Title
🤖 #hashtag1 #hashtag2
1. Key point 1
2. Key point 2
3. Key point 3
🔗 Article link
```

## 로그

실행 로그는 아래 파일에 기록됩니다:
- `aitimes_news_summary_test.log`
- `mk_news_summary_test.log`
