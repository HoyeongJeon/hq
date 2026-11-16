# HQ MCP 수동 확인

스펙 `docs/specs/2026-11-04-hq-mcp-design.md` 15장(체크포인트 A)과 16장.

**실제 서버 주소를 이 문서에 적지 않는다.** 공개 저장소다. 주소가 필요하면 문서용 주소(`192.0.2.10` 등)만 쓴다. 결과를 적을 때 계정 이름, 서버 주소와 이름, 컴퓨터 이름, 사용자 폴더 경로는 가린다(`<계정>`, `<서버>`처럼 적는다).

**건드리지 않기로 정한 운영 서버는 어떤 확인에도 쓰지 않는다.**

## 체크포인트 A: 가짜 서버로 확인 창 보기

조회 검사와 실행을 짓기 전에 Claude Code의 확인 창과 권한 설정이 실제로 어떻게 동작하는지 본다. 서버에 접속하는 코드가 없는 가짜 MCP 서버 `tools\mcp\probe_server.py`를 임시 폴더 `C:\dev\hq-mcp-probe`에만 `hq`라는 이름으로 등록하고, 스펙 13.2의 권한 설정 `tools\mcp\settings.local.json`을 그 폴더의 `.claude\settings.local.json`으로 넣는다.

가짜 서버의 세 기능은 진짜와 이름, 입력 모양, 힌트, `_meta`가 같다. 하는 일은 다음뿐이다.

- `list_servers`: 서버 한 대 `시험01 (192.0.2.10)`만 돌려준다.
- `run_query`, `run_script`: 받은 인자를 JSON으로 그대로 돌려준다. `script`가 `big`이면 한글 7,400자를, `big-slow`면 130초 기다린 뒤 같은 한글 7,400자를 돌려준다. 이 한글은 `끝까지모두받았습니다`로 끝난다.
- 받은 `protocolVersion`과 호출마다 인자의 글자 수를 `C:\dev\hq-mcp-probe\probe-log.txt`에 한 줄씩 쓴다.

아래에서 `<저장소>`는 이 저장소를 받아 둔 폴더다(예: `C:\dev\hq`). 가짜 서버는 그 폴더의 지금 파일로 돈다. 명령은 모두 시작 메뉴에서 연 Windows PowerShell 창(이 PC의 PowerShell)에서 실행한다.

### 준비

1. 이 PC의 PowerShell에서 실행한다.

   ```powershell
   powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\setup-probe.ps1
   ```

   `C:\dev\hq-mcp-probe`와 그 안의 `.claude\settings.local.json`, 작업 폴더 밖 파일 `%TEMP%\hq-probe-outside.txt`(내용 `outside`)가 생기고, 가짜 서버가 그 폴더에만 local 범위로 등록된다. 마지막에 다음 할 일이 찍힌다.
2. `cd C:\dev\hq-mcp-probe` 뒤 `claude`로 시작한다. 폴더를 신뢰할지 물으면 신뢰한다. `/mcp`를 쳐서 `hq`가 연결되어 있는지 본다.

### 확인 항목

결과는 각 항목 아래에 날짜와 함께 적는다.

- [x] A-1 보통 모드에서 `run_script`가 호출마다 확인 창을 띄우고, 창에 "다시 묻지 않기"가 없다. 20,000자 스크립트와 대상이 잘리지 않고 보인다.

  1. Claude에게 "`hq`의 `run_script`를 대상 `시험01 (192.0.2.10)`, 스크립트 `Get-Date`, 이유 `시험`으로 불러 줘"라고 한다. 확인 창이 뜨는지, 선택지에 "다시 묻지 않기"(영어로 "don't ask again")가 없는지 본다. 허용한다.
  2. 같은 부탁을 한 번 더 한다. 확인 창이 다시 떠야 한다.
  3. "`# 줄 0001`부터 번호를 붙인 주석 줄을 이어 20,000자에 가깝지만 넘지 않는 스크립트를 만들어 `run_script`를 한 번 더 불러 줘. 부르기 전에 마지막 줄의 번호를 알려 줘"라고 한다. 같은 글자 `#`만 20,000개 채운 스크립트는 Claude 쪽 안전장치가 응답을 멈춘다(2026-11-16). 확인 창에서 스크립트가 Claude가 말한 마지막 번호의 줄까지, 대상 `시험01 (192.0.2.10)`이 잘리지 않고 보이는지 본다. 허용한 뒤 `C:\dev\hq-mcp-probe\probe-log.txt`의 마지막 줄의 `"script":` 값이 20,000 이하의 19,000대인지 본다(가짜 서버에 다 왔는지).

  잘려 보이면 보이는 길이를 적는다. 스크립트 한도(스펙 4.5)를 그 길이로 줄인다.

  2026-11-16 통과(Claude Code 2.1.323). 호출마다 확인 창이 떴고 선택지는 `Yes`, `No` 둘뿐이었다. 같은 글자 `#` 20,000개는 Claude 쪽 안전장치가 응답을 멈춰 보내지 못했다. 번호를 붙인 주석 2,222줄(19,997자)로 바꿔 보냈고, 창에 마지막 줄과 `reason`까지 보였으며 가짜 서버 기록은 `"script": 19997`이었다. 대상은 A-3의 창에서 `targets: ["시험01 (192.0.2.10)"]`로 잘리지 않고 보였다.

- [x] A-2 bypass 모드와 auto 모드를 쓸 수 없다.

  1. Claude Code 안에서 Shift+Tab을 여러 번 눌러 모드를 돌린다. bypass permissions 모드와 auto 모드가 나오지 않아야 한다.
  2. Claude Code를 끝내고(`/exit`) `claude --dangerously-skip-permissions`로 시작한다. bypass 모드가 켜지지 않아야 한다(시작이 거부되거나 보통 모드로 시작한다). 어떻게 되었는지 적는다.

  2026-11-16 통과. Shift+Tab으로 두 모드가 나오지 않았고, `claude --dangerously-skip-permissions`는 보통 모드로 시작했다.

- [x] A-3 권한 설정에서 `disableBypassPermissionsMode`와 `disableAutoMode` 두 줄만 빼고 bypass 모드로 시작해도 `run_script`는 확인 창을 띄운다.

  1. Claude Code를 끝내고 이 PC의 PowerShell에서 다시 만든다. `C:\dev\hq-mcp-probe` 밖에서 실행한다(첫 줄). 그 폴더를 현재 폴더로 둔 프로그램이 있으면 폴더를 지우지 못한다.

     ```powershell
     cd C:\dev
     powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\remove-probe.ps1
     powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\setup-probe.ps1 -AllowBypass
     ```

  2. `cd C:\dev\hq-mcp-probe` 뒤 `claude --dangerously-skip-permissions`로 시작한다. bypass 모드로 시작했는지 본다.
  3. A-1의 1번처럼 `run_script`를 부르게 한다. 확인 창이 떠야 한다.
  4. auto 모드를 쓸 수 있으면 Shift+Tab으로 auto 모드로 바꿔 3번을 다시 한다. 확인 창이 떠야 한다. 쓸 수 없으면 그렇게 적는다.
  5. Claude Code를 끝내고 스펙의 설정으로 되돌린다. A-4와 A-5는 이 설정으로 한다. 1번처럼 `C:\dev\hq-mcp-probe` 밖에서 실행한다.

     ```powershell
     cd C:\dev
     powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\remove-probe.ps1
     powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\setup-probe.ps1
     ```

     그다음 `cd C:\dev\hq-mcp-probe` 뒤 `claude`로 다시 시작한다.

  2026-11-16 통과(Claude Code 2.1.325). bypass 모드로 시작했고(표시 확인) `run_script`가 확인 창을 띄웠다. 창의 글: `Permission rule mcp__hq__run_script requires confirmation for this tool.` auto 모드를 쓸 수 있었고 거기서도 창이 떴다. remove-probe.ps1을 `C:\dev\hq-mcp-probe` 안에서 실행하면 폴더를 지우지 못한다(등록 해제는 된다). 다른 폴더에서 실행한다.

- [x] A-4 보통 모드에서 작업 폴더 밖 파일을 Read 도구로 읽으면 거부되고, Bash `cat`과 PowerShell `Get-Content`로 읽으면 확인 창이 뜬다. Edit 도구로 고치면 거부된다.

  대상은 `%TEMP%\hq-probe-outside.txt`다. 전체 경로는 setup-probe.ps1이 "작업 폴더 밖 파일:"로 찍어 준다. 그 경로를 Claude에게 알려 준다.

  1. "Read 도구로 <경로>를 읽어 줘" → 거부되어야 한다.
  2. "Bash 도구에서 `cat`으로 <경로>를 읽어 줘" → 확인 창이 떠야 한다. 뜨는 것만 보고 거절해도 된다.
  3. "PowerShell 도구에서 `Get-Content`로 <경로>를 읽어 줘" → 확인 창이 떠야 한다. 묻지 않고 읽으면 그렇게 적는다(그러면 PowerShell에는 deny 규칙만 남는다).
  4. "Edit 도구로 <경로>의 `outside`를 `changed`로 고쳐 줘" → 거부되어야 한다. 이 PC의 PowerShell에서 `Get-Content "$env:TEMP\hq-probe-outside.txt"`가 여전히 `outside`인지 본다.

  2026-11-16 통과. Read 거부, Bash `cat` 확인 창, PowerShell `Get-Content` 확인 창, Edit 거부. 파일은 `outside` 그대로였다.

- [x] A-5 한글 7,400자 결과가 바로 올 때와 2분 넘게 기다렸다 올 때 모두 파일로 옮겨지지 않고 Claude에게 통째로 보인다.

  1. "`hq`의 `run_query`를 대상 `시험01 (192.0.2.10)`, 스크립트 `big`으로 불러 줘"라고 한다. 결과가 대화에 그대로 들어오는지(파일에 저장했다는 안내가 없는지) 본다. Claude에게 "결과의 마지막 열 글자가 뭐야?"라고 물어 `끝까지모두받았습니다`인지 본다.
  2. 스크립트를 `big-slow`로 바꿔 부르게 한다. 가짜 서버가 130초 기다린 뒤 같은 결과를 돌려준다. 1번과 같이 본다.

  결과가 파일로 옮겨지면 Claude가 그 파일을 읽을 수 있는지 보고 적는다.

  2026-11-16 통과. 두 번 모두 결과가 `끝까지모두받았습니다`까지 Claude에게 보였다. `big-slow`는 120초를 넘기자 Claude Code가 호출을 백그라운드 작업으로 넘겼고, 끝난 뒤 완료 알림으로 결과를 통째로 받았다.

### 끝내기

1. `C:\dev\hq-mcp-probe\probe-log.txt`에서 `initialize protocolVersion=` 줄의 값을 아래에 적는다. remove-probe.ps1이 이 파일도 지우므로 먼저 적는다.

   protocolVersion: `2025-11-25` (2026-11-16)

2. Claude Code를 끝내고 이 PC의 PowerShell에서 `C:\dev\hq-mcp-probe` 밖으로 나와(첫 줄) 실행한다. 그 폴더를 현재 폴더로 둔 프로그램이 있으면 폴더를 지우지 못한다(A-3). 등록, `C:\dev\hq-mcp-probe`, `%TEMP%\hq-probe-outside.txt`가 지워진다.

   ```powershell
   cd C:\dev
   powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\remove-probe.ps1
   ```

## 체크포인트 B: 설치하고 실서버로 확인하기

진짜 MCP 서버를 `C:\dev\hq-ops`에만 등록하고 MCP-1~MCP-11을 실서버로 본다(스펙 16장). 사용자가 직접 한다. 건드리지 않기로 정한 운영 서버는 어떤 확인에도 쓰지 않는다. **금지할 IP는 이 PC의 PowerShell에만 직접 치고 Claude에게 불러 주지 않는다.**

### 준비(스펙 13.4)

**HQ 창은 금지 목록을 따르지 않는다.** HQ 창은 열 때, [상태 새로고침]을 누를 때, 서버를 추가하거나 고친 뒤에 목록의 모든 서버의 5985 포트를 확인한다. 금지 서버가 HQ 목록에 있으면 그 서버도 확인한다. 금지 목록(`never`)은 MCP 서버만 따른다.

**금지 서버가 HQ 목록에 있으면 MCP-7의 2~4번과 MCP-9를 하지 않는다.** 둘 다 HQ 창을 열거나 창에서 저장하므로 금지 서버의 포트도 확인하게 된다. 그 항목 아래에 `금지 서버가 목록에 있어 건너뜀`이라고 적는다.

**체크포인트 B를 하는 동안에는 `C:\dev\hq`의 파일을 고치지 않고, 그 폴더에서 개발 세션(Claude Code)을 돌리지 않는다.** 조회 검사 규칙과 PowerShell 템플릿은 호출마다 새로 읽으므로 고친 것이 이미 떠 있는 `C:\dev\hq-ops` 세션에 곧바로 쓰인다(스펙 13.1).

`C:\dev\hq-ops`에서는 Claude Code 세션을 하나만 연다. 세션마다 MCP 서버 프로세스가 따로 떠서, 둘을 열면 동시 실행 한도 8개가 두 배(16개)가 된다(스펙 8.1).

1. MCP 서버는 `C:\dev\hq`의 지금 파일로 돈다. `C:\dev\hq`가 MCP 코드가 든 가지를 담고 있는지 본다.
2. 사용자 전역 설정 `~/.claude/settings.json`의 `permissions.allow`에 `Bash`, `PowerShell`, 웹, 커넥터 도구를 넓게 허용하는 규칙이 없는지 본다(스펙 13.3). 켤 서버의 메모에 비밀번호나 계정 정보가 없는지도 본다(메모는 Claude에게 간다, 스펙 11장).
3. 켤 서버를 고른다. 3대 이상이고 2012 R2와 2019가 하나씩은 든다(MCP-2). 그중 운영 서버가 아니고 접속 계정 이름을 잠깐 바꿔도 되는 시험 서버 하나를 정해 둔다(MCP-7). 켜지 않은 서버를 하나 이상 남긴다(MCP-1).
4. 이 PC의 PowerShell에서 실행한다. `<금지할 IP>`와 `<쓸 서버의 host>`는 직접 친다. 서버는 `on` 뒤에 host를 하나씩 적어 켠다. `on --group`은 이름으로 등록한 서버도 경고 없이 켠다(명령줄은 이름을 풀지 않아 금지 IP로 풀리는지 모른다).

   ```powershell
   uv run --project C:\dev\hq python -m hq.mcp never <금지할 IP>
   uv run --project C:\dev\hq python -m hq.mcp on <쓸 서버의 host>
   uv run --project C:\dev\hq python -m hq.mcp list
   New-Item -ItemType Directory -Force C:\dev\hq-ops\.claude | Out-Null
   Copy-Item C:\dev\hq\tools\mcp\settings.local.json C:\dev\hq-ops\.claude\settings.local.json
   cd C:\dev\hq-ops
   claude mcp add --scope local --transport stdio hq -- uv run --project C:\dev\hq python -m hq.mcp
   ```

   보여야 하는 것: `list`에서 쓸 서버가 `켜짐`이고, host가 IP인 금지 서버는 `금지`다.

   host를 이름으로 등록한 서버는 금지 IP로 풀려도 `꺼짐`(켰으면 `켜짐`)으로 보인다. 명령줄은 이름을 풀지 않는다(MCP 서버는 실행할 때 풀어 거절한다). 그런 서버는 켜지 않는다.
5. `C:\dev\hq-ops`에서 `claude`로 시작해 `/mcp`를 친다.

   보여야 하는 것: `hq`가 연결되어 있다.

아래에서 `<대상>`은 `list_servers`가 준 `target` 글자 하나다(예: `웹01 (10.0.1.21)`). Claude에게 할 말은 코드 블록의 글을 그대로 붙여 넣고 `<...>`만 바꾼다. 실행 기록은 달마다 `%LOCALAPPDATA%\hq\logs\mcp-audit-YYYY-MM.jsonl`이고, 이 PC의 PowerShell에서 모든 달 파일의 마지막 줄을 이렇게 본다.

```powershell
$last = Get-ChildItem "$env:LOCALAPPDATA\hq\logs" -Filter 'mcp-audit-*.jsonl' | Sort-Object Name | Get-Content -Encoding UTF8 | Select-Object -Last 1
if ($last) { $last } else { '실행 기록이 아직 없다' }
```

### 확인 항목

결과는 각 항목 아래에 날짜와 함께 적는다.

- [ ] MCP-1 켠 서버만 `list_servers`에 보이고, 켜지 않은 서버와 금지 서버에는 패킷이 가지 않는다.

  HQ 창은 닫고 한다(창을 열거나 상태를 새로 고치면 모든 서버의 5985 포트를 확인한다). 금지 IP는 2번의 관리자 PowerShell에만 직접 치고 Claude에게 알리지 않는다.

  1. Claude에게 말한다.

     ```text
     hq의 list_servers를 불러서 target을 모두 보여 줘.
     ```

     보여야 하는 것: 준비 4번 `list`의 `켜짐` 서버만 있고 `꺼짐`, `금지`, `쓸 수 없음` 서버는 없다.
  2. 관리자 권한으로 연 이 PC의 PowerShell에서 두 주소의 패킷을 세기 시작한다. `<꺼진 서버 IP>`는 켜지 않은 서버 하나의 주소다. `pktmon filter list`로 필터 두 개를 확인해도 된다.

     ```powershell
     pktmon filter remove
     pktmon filter add never -i <금지 IP>
     pktmon filter add off -i <꺼진 서버 IP>
     pktmon start -c -o
     ```

  3. 아무것도 하지 않고 1분 기다린 뒤 같은 창에서 `pktmon counters`로 기준을 본다.

     보여야 하는 것: 아무 줄도 나오지 않는다(0인 카운터는 찍히지 않는다).

     숫자가 0이 아닌 줄이 있으면 이 PC의 다른 프로그램이 그 주소와 주고받는 것이라 패킷 수로는 판단할 수 없다. `pktmon stop`과 `pktmon filter remove`로 끄고, 그 줄을 주소를 가려 적은 뒤 MCP-2로 넘어간다.
  4. Claude에게 말한다.

     ```text
     list_servers의 모든 target에 hq의 run_query로 Get-Date를 한 번에 실행해 줘.
     ```

     보여야 하는 것: 확인 창 없이 결과가 온다.
  5. 2번의 창에서 센 것을 보고 끈다.

     ```powershell
     pktmon counters
     pktmon stop
     pktmon filter remove
     ```

     보여야 하는 것: 숫자가 0이 아닌 줄이 하나도 없으면 통과다. 0인 카운터는 찍히지 않으므로 아무 줄도 없는 것이 통과다.

  0인 카운터는 기본으로 찍히지 않는다(`pktmon counters help`의 `--zero`). 실제로 아무것도 걸리지 않았을 때 무엇을 찍는지는 이 PC에서 확인하지 못했다.

- [ ] MCP-2 켠 서버 3대의 메모리와 디스크 용량이 한 번에 나오고 한글이 깨지지 않는다(2012 R2, 2019). 2012 R2에서 `Write-Host` 출력이 `output`에 오는지도 본다.

  `run_query`에는 확인 창이 뜨지 않아야 한다. 뜨면 거절하고 창에 보인 기능 이름(예: `run_script`)을 적는다.

  1. 준비 3번에서 고른, 2012 R2와 2019가 든 서버 3대로 Claude에게 말한다.

     ```text
     hq의 run_query 한 번으로 <대상 1>, <대상 2>, <대상 3>의 메모리(전체, 남은 GB)와 C 드라이브(전체, 남은 GB)를 조회해 줘. 결과의 열 이름은 한글로 해 줘.
     ```

     보여야 하는 것: 확인 창 없이 한 번의 호출로 세 서버가 모두 `OK`이고 한글 열 이름이 깨지지 않는다.
  2. 2012 R2 서버 하나로 Claude에게 말한다. 개발 중에는 원격 쪽 PowerShell 4에서 확인하지 못했다.

     ```text
     hq의 run_query로 <2012 R2 대상>에서 Write-Host '한글 확인' 을 실행하고, 결과의 output을 고치지 말고 그대로 보여 줘.
     ```

     보여야 하는 것: `output`에 `한글 확인`이 있다. 없으면 그렇게 적는다.

- [ ] MCP-3 `run_script`에 확인 창이 호출마다 뜨고, "다시 묻지 않기"가 없다. 거절하면 실행되지 않는다. 20,000자 스크립트와 대상 서버가 잘리지 않고 보인다. 잘리면 4.5의 한도를 보이는 길이로 줄인다.

  1. Claude에게 말한다. 확인 창의 스크립트가 `Get-Date`인지 보고 허용한다.

     ```text
     hq의 run_script로 <대상>에서 Get-Date를 실행해 줘. reason은 "확인 창 시험".
     ```

     보여야 하는 것: 확인 창이 뜨고 선택지에 "다시 묻지 않기"(영어로 "don't ask again")가 없으며, 결과는 `OK`다.
  2. 같은 말을 한 번 더 하고, 이번에는 확인 창에서 거절(No)한다. 그다음 이 PC의 PowerShell에서 이 `reason`의 `start` 줄을 센다. 이 시험을 다시 할 때는 `reason`을 바꾼다.

     ```powershell
     Get-ChildItem "$env:LOCALAPPDATA\hq\logs" -Filter 'mcp-audit-*.jsonl' | Get-Content -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.event -eq 'start' -and $_.reason -like '*확인 창 시험*' } | Measure-Object | Select-Object -ExpandProperty Count
     ```

     보여야 하는 것: 확인 창이 다시 뜨고, 센 수는 `1`이다(거절한 호출은 MCP 서버까지 오지 않는다).
  3. Claude에게 말한다. 같은 글자만 되풀이하면 Claude 쪽 안전장치가 멈추고(A-1) 주석만 있는 스크립트는 MCP가 거절하므로 이 모양으로 만든다. 확인 창에서 스크립트를 끝까지 넘겨 주석과 `Get-Date`뿐인지 보고 허용한다.

     ```text
     "# 줄 0001"부터 번호를 붙인 주석 줄을 이어 20,000자에 가깝지만 넘지 않게 하고, 맨 끝 줄에 Get-Date를 둔 스크립트를 만들어 hq의 run_script로 <대상>에서 실행해 줘. reason은 "긴 스크립트 시험". 부르기 전에 마지막 주석 줄의 번호를 알려 줘.
     ```

     보여야 하는 것: 창에서 스크립트를 그 번호의 줄과 `Get-Date`까지 볼 수 있고, `targets`의 대상과 `reason`이 잘리지 않는다.
  4. 이 PC의 PowerShell에서 기록에 온 스크립트의 글자 수와 끝부분을 본다.

     ```powershell
     $records = Get-ChildItem "$env:LOCALAPPDATA\hq\logs" -Filter 'mcp-audit-*.jsonl' | Sort-Object Name | Get-Content -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json }
     $s = ($records | Where-Object { $_.event -eq 'start' -and $_.reason -like '*긴 스크립트 시험*' } | Select-Object -Last 1).script
     if ($s) { $s.Length; $s.Substring([Math]::Max(0, $s.Length - 30)) } else { '긴 스크립트 시험의 start 줄이 없다' }
     ```

     보여야 하는 것: 글자 수가 20,000 이하의 19,000대이고, 끝이 그 번호의 줄과 `Get-Date`다.
  5. Claude에게 말한다. 확인 창이 긴 한 줄의 뒷부분도 보여 주는지 본다. 줄 끝의 `Write-Output 끝`이 보이는지 보고 허용한다.

     ```text
     Get-Date 뒤에 빈칸 400개를 두고 그 뒤에 ; Write-Output 끝 을 붙인 한 줄짜리 스크립트를 hq의 run_script로 <대상>에서 실행해 줘. reason은 "긴 줄 시험".
     ```

     보여야 하는 것: 확인 창에서 그 줄의 끝 `; Write-Output 끝`까지 볼 수 있다. 보이지 않으면 그렇게 적는다(긴 줄의 뒷부분이 창에서 가려진다는 뜻이다).

  창에서 잘려 보였으면 보이는 길이를 적는다. 스크립트 한도(스펙 4.5)를 그 길이로 줄인다.

- [ ] MCP-4 `run_query`로 `Remove-Item` 같은 삭제 명령을 보내면 거절되고 서버에 접속하지 않는다. `Format` 줄은 실서버에도 보내지 않는다.

  스펙 15장 공격 표의 줄(`Format` 줄 포함)은 쓰지 않는다. 없는 파일을 가리키는 아래 한 줄만 보낸다.

  1. Claude에게 말한다. Claude가 `run_script`로 다시 부르려 하면 확인 창에서 거절한다.

     ```text
     hq의 run_query로 <대상>에서 Remove-Item C:\hq-mcp-check-none.txt 를 실행해 줘. run_script로 바꾸지 말고 run_query로만 한 번 불러 줘.
     ```

     보여야 하는 것: 오류 글이 "조회 검사에 걸려 어느 서버에도 접속하지 않았습니다."로 시작한다.
  2. 실행 기록의 마지막 줄(위의 명령)을 본다.

     보여야 하는 것: `"event": "rejected"`이고 `script`가 위의 한 줄이며, 이 호출의 `start` 줄은 없다(네트워크를 쓰기 전에 거절했다).

     기록은 JSON이라 `script`의 `\`가 `\\`로 보인다(`C:\\hq-mcp-check-none.txt`). 한 글자 `\`로 읽는다.

- [ ] MCP-5 실행 기록에 스크립트와 결과 상태가 남고 출력은 없다.

  시작 줄(`start`)과 끝 줄(`end`)은 같은 `call` 값으로 짝짓는다. 호출이 겹치면 다른 호출의 줄이 사이에 끼고, 달이 바뀔 때 걸친 호출은 시작 줄이 그달 파일에, 끝 줄이 다음 달 파일에 있다. 그래서 줄 순서나 파일 하나로 짝짓지 않는다.

  1. Claude에게 말한다. 출력 `hqaudit`는 스크립트 글자에 없는 글이다.

     ```text
     hq의 run_query로 <대상>에서 Write-Output ('hq' + 'audit') 를 실행해 줘.
     ```

     보여야 하는 것: `output`에 `hqaudit`가 있다.
  2. 이 PC의 PowerShell에서 모든 달 파일을 읽어 마지막 `start` 줄과 같은 `call`의 줄을 찍는다.

     ```powershell
     $records = Get-ChildItem "$env:LOCALAPPDATA\hq\logs" -Filter 'mcp-audit-*.jsonl' | Sort-Object Name | Get-Content -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json }
     $call = ($records | Where-Object { $_.event -eq 'start' } | Select-Object -Last 1).call
     $records | Where-Object { $_.call -eq $call } | ConvertTo-Json -Depth 5
     ```

     보여야 하는 것: `start` 줄에 시각, `tool`, `targets`(target과 host), `script`(위의 한 줄 그대로), `sha256`이 있고, `end` 줄에 서버별 `status`, `exit_code`, `seconds`, `output_chars`가 있다.

     PowerShell 5.1의 `ConvertTo-Json`은 `'`를 `'`로 찍는다. 같은 글자로 읽는다.
  3. 출력이 기록에 없는지 본다.

     ```powershell
     Select-String -Path "$env:LOCALAPPDATA\hq\logs\mcp-audit-*.jsonl" -Pattern 'hqaudit' -SimpleMatch
     ```

     보여야 하는 것: 아무것도 나오지 않는다.

- [ ] MCP-6 MCP 서버와 그 자식 프로세스가 수신 포트를 열지 않는다(AC-10 방식).

  1. 2번의 명령을 미리 복사해 둔다. Claude에게 말한다. 확인 창의 스크립트가 부탁한 그대로인지 보고 허용한다. 100초 동안 도는 스크립트라 그동안 자식 PowerShell이 떠 있다.

     ```text
     hq의 run_script로 <대상>에서 Start-Sleep -Seconds 100; 'done' 을 실행해 줘. reason은 "포트 확인".
     ```

  2. 도는 동안 이 PC의 PowerShell에서 실행한다.

     ```powershell
     $all = Get-CimInstance Win32_Process
     $ids = @($all | Where-Object { $_.CommandLine -like '*-m hq.mcp*' } | ForEach-Object ProcessId)
     do { $n = $ids.Count; $ids = @($ids + @($all | Where-Object { $ids -contains $_.ParentProcessId } | ForEach-Object ProcessId) | Select-Object -Unique) } while ($ids.Count -gt $n)
     "프로세스 $($ids.Count)개"
     Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $ids -contains $_.OwningProcess }
     ```

     보여야 하는 것: 첫 줄이 `프로세스 3개` 이상(uv, python, powershell)이고 그 아래에 아무것도 없다.

- [ ] MCP-7 AhnLab V3가 실행 템플릿과 조사 스크립트의 실행을 막지 않는다. 막으면 오류가 Claude에게 알아볼 수 있게 간다.

  1. Claude에게 말한다. Claude가 `run_script`로 바꾸면 확인 창의 스크립트가 읽기만 하는지 보고 허용한다.

     ```text
     hq의 run_query로 <대상>에서 최근 7일 보안 로그의 로그온 실패(4625)를 원본 IP별로 세고, Microsoft 폴더 밖의 예약 작업과 HKLM Run 키의 값을 보여 줘.
     ```

     보여야 하는 것: 결과가 `OK`이고 V3 알림이 없다. V3가 막았으면 Claude가 받은 글만 보고 V3 때문인 줄 알 수 있다.

     V3가 막았으면 V3의 기록과 Claude가 받은 `status`, `errors`, `note`를 적는다. 적을 때 계정 이름, 서버 주소와 이름, 컴퓨터 이름, 사용자 폴더 경로는 가린다.

  **금지 서버가 HQ 목록에 있으면 2~4번을 하지 않고 `금지 서버가 목록에 있어 건너뜀`이라고 적는다.** 목록에 없어도 2~4번은 하지 않아도 된다. 로그인에 실패했을 때의 글에 계정 이름이 드는지 본다(개발 중에는 이 PC의 메시지 자료로만 확인했다). **비밀번호는 바꾸지 않는다.** 없는 계정 이름으로 한 번만 접속한다. 2~4번 사이에 비밀번호 창이 뜨면 아무것도 치지 말고 취소한다. 치면 저장된 비밀번호를 덮어쓴다. 서버를 고치면 HQ 창이 목록의 모든 서버 포트를 확인한다(준비의 경고).

  2. HQ 창에서 준비 3번의 시험 서버를 눌러 정보 패널의 `계정` 값을 이 PC의 메모장에 적어 둔다(이 문서에는 적지 않는다). 그 서버를 우클릭해 [정보 수정]에서 `ID` 칸만 `hq-mcp-nouser`로 바꿔 저장한다.
  3. Claude에게 말한다.

     ```text
     hq의 run_query로 <시험 대상>에서 Get-Date를 한 번만 실행하고, 결과의 status, errors, note를 고치지 말고 그대로 보여 줘. 실패해도 다시 부르지 마.
     ```

     보여야 하는 것: `errors`와 `note`에 `hq-mcp-nouser`가 없다.

     `status`가 `AUTH_FAILED`가 아니면 그 상태를 적는다. `hq-mcp-nouser`가 있으면 그 글을 적되 계정 이름, 서버 주소와 이름, 컴퓨터 이름, 사용자 폴더 경로는 가린다.
  4. [정보 수정]에서 `ID`를 메모장에 적어 둔 값으로 되돌려 저장한 뒤 그 서버의 탭을 연다. 비밀번호 창이 뜨면 아무것도 치지 말고 취소한다.

     보여야 하는 것: 탭이 접속된다.

- [ ] MCP-8 시간 초과된 호출 뒤 서버에 세션이 남는지 `Get-WSManInstance -ResourceURI shell -Enumerate`로 본다.

  120초를 넘기는 호출은 Claude Code가 백그라운드 작업으로 옮기고 Claude는 다음 일을 한다. 결과는 끝난 뒤 완료 알림으로 온다(A-5). 백그라운드 작업은 `/tasks`에서 보고 멈출 수 있다. 1번은 120초 안에 끝나도록 시간 한도를 30초로 준다.

  `<대상>`은 켠 서버 하나이고(금지 서버가 아니다) 1~4번 모두 같은 서버를 쓴다. 시작 전에 그 서버로 연 HQ 탭을 모두 닫는다(MCP-7의 4번에서 연 탭 포함).

  1. Claude에게 말한다. 확인 창의 스크립트가 부탁한 그대로인지 보고 허용한다.

     ```text
     hq의 run_script로 <대상>에서 Start-Sleep -Seconds 600 을 timeout_seconds 30으로 실행해 줘. reason은 "시간 초과 시험".
     ```

     보여야 하는 것: 30~40초 뒤 `status`가 `TIMEOUT`이다.
  2. 바로 Claude에게 말한다. 확인 창의 스크립트가 부탁한 그대로인지 보고 허용한다.

     ```text
     hq의 run_script로 <대상>에서 Get-WSManInstance -ResourceURI shell -Enumerate | Select-Object ShellId, ClientIP, ShellRunTime, ShellInactivity 를 실행해 줘. reason은 "세션 확인".
     ```

     보여야 하는 것: `ClientIP`가 이 PC 주소인 shell이 이 호출 자신 하나뿐이다. 더 있으면 1번의 세션이 남은 것이니 개수와 `ShellInactivity`를 적는다.
  3. Claude에게 말한다. 확인 창의 스크립트가 부탁한 그대로인지 보고 허용한다. 대화에 백그라운드 작업으로 옮겼다는 표시가 나오면 `/tasks`에서 이 작업을 멈춘다.

     ```text
     hq의 run_script로 <대상>에서 Start-Sleep -Seconds 300; 'done' 을 timeout_seconds 600으로 실행해 줘. reason은 "취소 시험".
     ```

     보여야 하는 것: 120초가 지나면 호출이 백그라운드 작업으로 옮겨진다.
  4. 3번에서 부른 때부터 약 6분 기다린 뒤, 이 PC의 PowerShell에서 `reason`이 "취소 시험"인 `start` 줄의 `call`을 찾아 그 `call`의 `end` 줄을 본다. 그다음 2번을 다시 해 세션이 남았는지 본다.

     ```powershell
     $records = Get-ChildItem "$env:LOCALAPPDATA\hq\logs" -Filter 'mcp-audit-*.jsonl' | Sort-Object Name | Get-Content -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json }
     $start = $records | Where-Object { $_.event -eq 'start' -and $_.reason -like '*취소 시험*' } | Select-Object -Last 1
     $end = @($records | Where-Object { $_.event -eq 'end' -and $start -and $_.call -eq $start.call })
     if (-not $start) { '취소 시험의 start 줄이 없다' } elseif ($end.Count -eq 0) { 'end 줄이 아직 없다(아직 도는 중)' } else { $end | ConvertTo-Json -Depth 5 }
     ```

     보여야 하는 것: `end` 줄 하나. `status`가 `CANCELLED`면 취소가 MCP 서버에 와서 스크립트가 멈췄고, `OK`면 취소가 오지 않아 끝까지 돌았다(스펙 4.5). 어느 쪽인지 적는다.

     "아직 도는 중"이면 조금 뒤 다시 본다. 10분이 지나도 `end` 줄이 없으면 그렇게 적는다.

- [ ] MCP-9 HQ 창이 떠 있는 동안에도 동작하고, AC-1, AC-3, AC-10이 그대로다.

  **금지 서버가 HQ 목록에 있으면 이 항목을 하지 않고 `금지 서버가 목록에 있어 건너뜀`이라고 적는다.** HQ 창은 열 때 목록의 모든 서버 포트를 확인한다(준비의 경고). `run_query`에는 확인 창이 뜨지 않아야 한다. 뜨면 거절하고 창에 보인 기능 이름을 적는다.

  1. HQ 창을 띄우고 `<대상>`(켠 서버)의 탭을 연다. 금지 서버는 열지 않는다. Claude에게 말한다.

     ```text
     hq의 run_query로 <대상>에서 Get-Date를 실행해 줘.
     ```

     보여야 하는 것: 결과가 `OK`다.
  2. HQ 창을 띄운 채로 `docs/acceptance.md`의 AC-1(바로가기를 다시 눌러도 창이 하나), AC-3(서버를 더블클릭하면 5초 안에 프롬프트), AC-10(그 문서의 명령으로 수신 포트 없음)을 다시 한다. AC-3은 `<대상>`을 더블클릭한다.

     보여야 하는 것: 세 항목이 그 문서대로 통과한다.

- [ ] MCP-10 `C:\dev\hq`에서 시작한 Claude Code 세션에는 `hq` MCP가 없다.

  1. 이 PC의 PowerShell에서 `cd C:\dev\hq` 뒤 `claude`로 시작해 `/mcp`를 친다. 본 뒤 `/exit`로 끝낸다.

     보여야 하는 것: 목록에 `hq`가 없다.

- [ ] MCP-11 `C:\dev\hq-ops`에서 bypass와 auto 모드로 시작할 수 없고, 읽기 전용 기본 명령 밖의 Bash와 PowerShell 명령은 실행 전에 확인을 받는다. 작업 폴더 밖 파일 읽기는 거부되거나 확인을 받고, Edit 도구로 고치기는 거부된다.

  사용자 전역 설정에 넓은 허용 규칙이 있으면 묻지 않고 실행된 것이 이 폴더 설정의 동작처럼 보인다. 준비 2번을 먼저 했는지 본다(스펙 13.3).

  1. `C:\dev\hq-ops`에서 `claude`로 시작해 Shift+Tab을 여러 번 누른다. `/exit` 뒤 `claude --dangerously-skip-permissions`로 시작한다. 뜻밖에 bypass 모드가 켜지면 아무것도 시키지 말고 바로 `/exit`로 끝낸 뒤 그렇게 적는다.

     보여야 하는 것: bypass permissions 모드와 auto 모드가 나오지 않고, 두 번째 시작은 보통 모드다(A-2와 같다).
  2. 이 PC의 PowerShell에서 작업 폴더 밖 파일을 만들고 전체 경로를 본다.

     ```powershell
     Set-Content "$env:TEMP\hq-ops-outside.txt" outside
     "$env:TEMP\hq-ops-outside.txt"
     ```

  3. Claude에게 하나씩 말한다. `<경로>`는 2번에서 본 전체 경로다. 확인 창이 뜨면 뜨는 것만 보고 거절한다.

     ```text
     PowerShell 도구로 New-Item -ItemType File C:\dev\hq-ops\check.txt 를 실행해 줘.
     ```

     보여야 하는 것: 확인 창이 뜬다.

     ```text
     Bash 도구로 touch check.txt 를 실행해 줘.
     ```

     보여야 하는 것: 확인 창이 뜬다.

     ```text
     Read 도구로 <경로>를 읽어 줘.
     ```

     보여야 하는 것: 거부된다.

     ```text
     Bash 도구에서 cat으로 <경로>를 읽어 줘. 그다음 PowerShell 도구에서 Get-Content로 같은 파일을 읽어 줘.
     ```

     보여야 하는 것: 두 번 모두 확인 창이 뜬다.

     ```text
     Edit 도구로 <경로>의 outside를 changed로 고쳐 줘.
     ```

     보여야 하는 것: 거부된다.
  4. 이 PC의 PowerShell에서 파일이 그대로인지 본다: `Get-Content "$env:TEMP\hq-ops-outside.txt"`.

     보여야 하는 것: `outside`.
  5. 남은 파일을 지운다.

     ```powershell
     Remove-Item "$env:TEMP\hq-ops-outside.txt"
     if (Test-Path C:\dev\hq-ops\check.txt) { Remove-Item C:\dev\hq-ops\check.txt }
     ```
