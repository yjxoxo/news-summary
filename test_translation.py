import re
from langchain_ollama import ChatOllama

samples = [
    ("앤트로픽 AI 규제",
     """🤖 #앤트로픽 #AI모델 #정부규제
1. 앤트로픽, 위험한 AI 모델 출시 방지를 위한 정부 규제 필요성 강조
2. 가장 진보된 AI 모델에 항공기 수준의 안전성 테스트와 독립적 감사를 의무화할 것을 주장
3. e25 플롭스 이상을 학습하는 거대 모델과 AI 관련 매출액이 높은 기업을 대상으로 한 규제에 대한 제안"""),

    ("독일 법원 구글 판결",
     """🤖 #독일법원 #허위정보생성 #구글책임
1. 독일 법원, AI 개요 관련 구글에 직접 책임 판결
2. AI가 만든 허위 정보 차단 가처분 명령 내림
3. AI 기반 검색 서비스의 법적 책임 범위 설정에 중대한 선례"""),

    ("앤트로픽 Claude Fable 5 논란",
     """🤖 #앤트로픽 #클로드페이블5 #논란
1. 과도한 보안 제한과 특정 연구 분야 성능 저하 논란에 직면함
2. 사이버보안 및 생물학 관련 작업 차단으로 AI 연구 방해 가능성 제기됨
3. 일부 기능은 사용자에게 표시되지 않아 경쟁 기술 개발 견제 전략 비판을 받음"""),
]

def translate_to_english(korean_summary):
    prompt = f"""Translate the following Korean news summary into natural English.

Rules:
- Keep the exact format: 🤖 line first, then 1. 2. 3.
- Each numbered point must be a complete sentence with a clear subject (e.g. "Google released..." not "Released...").
- Do NOT use a comma after the subject (e.g. "Google releases" not "Google, releases").
- Translate hashtags to English. No underscores in hashtags (e.g. #AIRegulation not #AI_Regulation).
- Keep proper nouns accurate (e.g. "Claude" not "Cloud", model names exactly as written).
- Do not add or remove any information.

{korean_summary}
"""
    llm_en = ChatOllama(model="llama3.3:70b")
    response = llm_en.invoke(prompt)
    result = response.content if hasattr(response, "content") else str(response)
    result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
    return result

for title, sample in samples:
    print(f"\n{'='*60}")
    print(f"[{title}]")
    print(f"\n원문:\n{sample}")
    print(f"\n번역:")
    print(translate_to_english(sample))
