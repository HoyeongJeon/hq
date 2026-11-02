# 검증 항목 결과

스펙 `docs/specs/2026-10-15-hq-design.md` 14장의 V1~V9를 실서버로 확인한 결과다.
확인한 날짜: 2026-10-16 / 서버 OS: 확인하지 않음 (SRV-B, 도메인 가입)

실제 서버 주소와 컴퓨터 이름은 이 문서와 테스트 표본에 남기지 않는다. 주소는 문서용 주소(198.51.100.20은 쓴 서버, 198.51.100.10은 쓰지 않은 첫 서버)로, 컴퓨터 이름은 `SRV-B`로 바꿔 적었다. 192.0.2.10은 원래 시험에 쓴 문서용 주소 그대로다. 나머지 문구와 FQID는 보고서 그대로다.

| # | 결과 | 내용 | 반영 |
|---|---|---|---|
| V1 | 통과 | `hq.verify` 1회차에서 새 창이 원격 프롬프트로 이어졌고 `exit`를 칠 때까지 열려 있었다(사용자 확인). `[1회차] ... 종료 코드 0 → USER_EXIT` | 없음 |
| V2 | 통과 | 세션 창의 프롬프트: `[198.51.100.20]: [웹서버] PS C:\Users\Administrator\Documents>` (한글 별명이 바르게 보임). 서버의 PowerShell 버전은 확인하지 않음 | 없음 |
| V3 | 통과 | 틀린 비밀번호: `FQID=AccessDenied,PSSessionOpenFailed`, `ErrorCode=5` / TrustedHosts 없음: `FQID=1312,PSSessionOpenFailed`, `ErrorCode=1312` / 포트 닫힘: `FQID=WinRMOperationTimeout,PSSessionOpenFailed`, `ErrorCode=-2144108250` / 동시 세션 한도: 시험하지 않음(사용자가 N으로 답함). 세 표본 모두 `HResult=-2146233087`. 메시지는 한국어로 왔다(TrustedHosts 없음은 가운데 WinRM 부분만 영어). 메시지 원문은 `tests/integration/test_bootstrap_ps.py`의 `CASES`에 있다 | Task 6에서 CASES와 Get-HqExitCode에 반영: FQID의 쉼표 앞부분으로 분류(`AccessDenied` → 10, `1312` → 12, `WinRMOperationTimeout` → 11). 한도 초과는 표본이 없어 기존 메시지 규칙을 그대로 두고 맨 먼저 검사 |
| V4 | 통과 | 이 PC의 WinRM 서비스: `Stopped` / 시작 유형: `Manual`. 서비스가 'Stopped' 상태에서 정상 접속: 성공. WSMan 드라이브로 TrustedHosts 읽기: 실패 `FQID=PathNotFound,Microsoft.PowerShell.Commands.GetItemCommand`, 메시지 `Cannot find path 'WSMan:\localhost\Client\TrustedHosts' because it does not exist.` / 레지스트리 `HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Client`: `trusted_hosts = '198.51.100.10,198.51.100.20'` / 레지스트리 `HKLM:\SOFTWARE\Policies\Microsoft\Windows\WinRM\Client`: 키 없음 | Task 10에서 반영: 서비스는 켜 두지 않아도 접속된다. TrustedHosts는 WSMan 드라이브가 아니라 레지스트리 값 `trusted_hosts`로 읽는다 |
| V5 | 통과 | `ipconfig`와 `Get-Service \| Select-Object -First 5`의 한글이 세션 콘솔 창에서 바르게 보였다(사용자 확인). hq 탭(ConPTY·xterm)에서도 한글 출력이 바르게 보였다(체크포인트 B, 2026-10-19, 사용자 확인). 설치한 exe의 탭에서도 한글 출력이 바르게 보였다(체크포인트 C, 2026-10-19, 사용자 확인) | 없음 |
| V6 | 통과 | `[성공] Administrator`, `[성공] .\Administrator`, `[성공] SRV-B\Administrator`. 접두 없는 `Administrator`로 연 세션의 `whoami`: `srv-b\administrator`(도메인 계정이 아니라 로컬 계정) | Task 8(엑셀 설명), Task 15(입력 안내)에서 반영 |
| V7 | 통과 | `exit` → `[1회차] ... 종료 코드 0 → USER_EXIT` / `Stop-Process -Id $PID -Force` → 창이 저절로 닫힘, `[2회차] ... 종료 코드 20 → DISCONNECTED` | 없음. 서버에서 WinRM을 다시 시작해 끊는 경우는 체크포인트 B에서 확인(2026-10-19, 사용자: 문제없음) |
| V8 | 통과 | 설치한 exe(`%LOCALAPPDATA%\Programs\hq\hq.exe`)가 콘솔 창 없이 떴고 AhnLab V3가 막거나 지우지 않았다(체크포인트 C, 2026-10-19, 사용자 확인). AC-8 수정(콘솔 호스트 내부를 리플렉션으로 다룸)을 넣고 다시 빌드한 exe도 V3가 막지 않았고 탭이 정상으로 열렸다(2026-11-02, 사용자 확인) | 없음 |
| V9 | 통과 | 개발 실행(`uv run python -m hq`)에서 한글 IME 입력과 `dir C:\Windows\System32` 출력이 문제없었다(체크포인트 B, 2026-10-19, 사용자 확인). exe 탭에서 한글 IME 입력과 `dir C:\Windows\System32` 출력이 문제없었다(체크포인트 C, 2026-10-19, 사용자 확인) | 없음 |

## 참고

- 내 PC: Windows 11, Windows PowerShell 5.1. 확인하는 동안 WinRM 서비스는 `Stopped` / `Manual`이었다.
- 처음 고른 서버 1(198.51.100.10)은 5985가 닫혀 있어(3389는 열림) 쓰지 않았다. 이 서버에 대한 접속은 모두 포트 닫힘 표본과 같은 `FQID=WinRMOperationTimeout,PSSessionOpenFailed`, `ErrorCode=-2144108250`으로 끝났다.
- 도메인에 가입된 서버에서 접두 없는 `Administrator`는 로컬 계정으로 로그인됐다(`whoami`: `srv-b\administrator`).
- 내 PC의 WinRM 서비스가 멈춰 있으면 `WSMan:` 드라이브로는 TrustedHosts를 읽을 수 없다. 레지스트리 값 `trusted_hosts`는 관리자 권한 없이 읽힌다.
