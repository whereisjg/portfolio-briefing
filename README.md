# 📈 포트폴리오 자동 일일 브리핑

**GitHub Actions + Claude AI = 매일 아침 7시 자동 실행!**

## 🎯 기능

✅ **실시간 가격 조회** — QLD, SSO, USD 현재가 + 전일비  
✅ **뉴스 자동 수집** — 각 종목 3개 뉴스 (한글 번역)  
✅ **마크다운 생성** — 프로페셔널 브리핑 문서  
✅ **자동 저장** — GitHub에 매일 자동 저장  
✅ **365일 무중단** — 컴퓨터 켤 필요 없음  

## 🚀 빠른 시작

### 1단계: 저장소 생성
[GitHub.com](https://github.com) 에서 새 저장소 `portfolio-briefing` 생성

### 2단계: 파일 3개 추가
1. `.github/workflows/briefing.yml` ← GitHub Actions 설정
2. `portfolio_briefing.py` ← 메인 스크립트
3. `README.md` ← 이 파일

### 3단계: API 키 설정
Settings → Secrets → `CLAUDE_API_KEY` 추가  
(console.anthropic.com 에서 발급)

### 4단계: 테스트
Actions 탭 → Run workflow 클릭

## 📋 파일 설명

| 파일 | 설명 |
|------|------|
| `briefing.yml` | GitHub Actions 워크플로우 (매일 7시 실행) |
| `portfolio_briefing.py` | Claude API로 데이터 수집 + 브리핑 생성 |
| `briefing_YYYYMMDD.md` | 매일 자동 생성되는 결과 파일 |

## ⏰ 실행 시간

**매일 아침 7시 KST (UTC 22:00 전날)**

원하는 시간으로 변경하려면:
```yaml
cron: '0 22 * * *'  # 시 분 요일 월 일
# 예: 오전 8시 = '0 23 * * *'
```

## 🔧 커스터마이징

### 종목 변경
`portfolio_briefing.py` 에서:
```python
TICKERS = ["QLD", "SSO", "USD"]  # 여기 수정
```

### 뉴스 개수 변경
```python
for i, n in enumerate(news_list[:3], 1):  # :3 → :5 (5개로)
```

## 📞 트러블슈팅

**Q: 파일이 생성이 안 돼요**
→ Actions 탭 → 워크플로우 실행 로그 확인

**Q: 시간이 잘못됐어요**
→ UTC/KST 시간차 확인. 한국은 UTC+9

**Q: API 키 오류**
→ `CLAUDE_API_KEY` 시크릿이 정확히 설정됐는지 확인

## 💡 고급 (선택사항)

### 카카오톡 자동 전송
`KAKAO_TOKEN` 환경변수 추가하면 자동 전송 (별도 셋업 필요)

### 이메일로 자동 전송
GitHub Pages나 별도 메일 서비스로 변경 가능

---

**더 자세한 설정은 `SETUP_GUIDE.md` 참고!**
