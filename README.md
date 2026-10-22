# 로또 6/45 텔레그램 분석 봇

GitHub Actions + Python으로 동행복권 공개 회차 결과를 수집하고, 통계/워크포워드 백테스트/10게임 후보 조합을 텔레그램으로 보냅니다.

## 중요한 한계
로또 추첨이 공정하고 독립적이라면 모든 6개 조합의 1등 확률은 1/8,145,060으로 같습니다. 출현 빈도, 미출현 기간, 홀짝이나 합계 필터는 다음 회차 당첨 확률을 높인다고 보장할 수 없습니다. 백테스트는 과거에 관찰된 결과이며 미래 수익/당첨을 보장하지 않습니다.

## 새 저장소 만들기
1. GitHub에서 **New repository**를 누르고 `lotto-telegram-analyzer` 같은 이름으로 새 저장소를 만듭니다.
2. 이 ZIP을 풀어 안의 파일과 `.github` 폴더를 저장소 루트에 업로드합니다. 폴더 구조는 아래와 같아야 합니다.

```text
lotto-telegram-analyzer/
├── main.py
├── requirements.txt
├── README.md
├── .gitignore
├── .github/workflows/lotto.yml
└── data/
```

3. Telegram에서 `@BotFather`를 열고 `/newbot`으로 봇을 만든 다음 토큰을 복사합니다.
4. 봇과 대화창을 열고 `/start`를 보냅니다. 그룹에서 쓸 경우 봇을 그룹에 추가하고 그룹에서 메시지를 보내야 합니다.
5. `https://api.telegram.org/bot<토큰>/getUpdates`를 브라우저에서 열어 응답의 `message.chat.id`를 확인합니다. 토큰을 다른 사람에게 공개하지 마세요.
6. GitHub 저장소 → **Settings → Secrets and variables → Actions → New repository secret**에서 다음 두 개를 등록합니다.
   - `TELEGRAM_TOKEN` = BotFather 토큰
   - `TELEGRAM_CHAT_ID` = 본인 채팅 ID 또는 그룹 ID
7. 저장소 → **Actions**에서 workflow 사용을 허용하고 `Lotto 6/45 Telegram Analyzer`를 선택한 뒤 **Run workflow**로 첫 실행을 합니다.
8. 첫 실행은 역대 데이터를 채우므로 시간이 걸릴 수 있습니다. 성공한 뒤 `data/draws.json`, `data/latest_report.json`, `data/state.json`이 저장소에 커밋되었는지 확인합니다.

## 자동 실행
- 매일 09:05 한국 시간: 일일 통계 리포트
- 매주 토요일 10:00 한국 시간: 10게임 후보 조합 최종 리포트
- GitHub 예약 작업은 지연될 수 있으므로 정확한 분 단위 발송은 보장되지 않습니다. GitHub Actions의 `schedule`은 UTC 기준입니다.

## 백테스트
`main.py`의 `backtest()`는 각 테스트 회차 직전의 과거 데이터만 사용해 번호를 생성하고 해당 회차와 비교합니다. 무작위 기준선도 같은 테스트 회차 수와 회차당 게임 수로 비교합니다. 전략별 결과는 `data/latest_report.json`에 기록됩니다. 최초 실행 데이터가 부족하거나 공식 사이트 형식이 바뀌면 Actions 로그를 확인하세요.

## 파일 설명
- `main.py`: 데이터 수집, 통계, 번호 생성, 워크포워드 백테스트, 텔레그램 발송
- `data/draws.json`: 공식 회차 데이터
- `data/latest_report.json`: 최근 통계와 백테스트
- `data/recommendations.json`: 회차별 10게임 추천 기록
- `data/state.json`: 중복 발송 방지 상태
- `.github/workflows/lotto.yml`: 예약 실행 및 데이터 자동 커밋

## 참고
공식 회차별 결과: https://dhlottery.co.kr/gameResult.do?method=byWin
공식 로또 6/45 안내: https://m.dhlottery.co.kr/lt645/intro
