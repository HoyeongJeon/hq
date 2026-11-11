# HQ MCP 수동 확인

스펙 `docs/specs/2026-11-04-hq-mcp-design.md` 15장(체크포인트 A)과 16장.

**실제 서버 주소를 이 문서에 적지 않는다.** 공개 저장소다. 주소가 필요하면 문서용 주소(`192.0.2.10` 등)만 쓴다.

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

- [ ] A-1 보통 모드에서 `run_script`가 호출마다 확인 창을 띄우고, 창에 "다시 묻지 않기"가 없다. 20,000자 스크립트와 대상이 잘리지 않고 보인다.

  1. Claude에게 "`hq`의 `run_script`를 대상 `시험01 (192.0.2.10)`, 스크립트 `Get-Date`, 이유 `시험`으로 불러 줘"라고 한다. 확인 창이 뜨는지, 선택지에 "다시 묻지 않기"(영어로 "don't ask again")가 없는지 본다. 허용한다.
  2. 같은 부탁을 한 번 더 한다. 확인 창이 다시 떠야 한다.
  3. "스크립트를 `#` 20,000자로 채워서 `run_script`를 한 번 더 불러 줘"라고 한다. 확인 창에서 스크립트가 끝까지, 대상 `시험01 (192.0.2.10)`이 잘리지 않고 보이는지 본다. 허용한 뒤 `C:\dev\hq-mcp-probe\probe-log.txt`의 마지막 줄이 `"script": 20000`인지 본다(가짜 서버에 다 왔는지).

  잘려 보이면 보이는 길이를 적는다. 스크립트 한도(스펙 4.5)를 그 길이로 줄인다.

- [ ] A-2 bypass 모드와 auto 모드를 쓸 수 없다.

  1. Claude Code 안에서 Shift+Tab을 여러 번 눌러 모드를 돌린다. bypass permissions 모드와 auto 모드가 나오지 않아야 한다.
  2. Claude Code를 끝내고(`/exit`) `claude --dangerously-skip-permissions`로 시작한다. bypass 모드가 켜지지 않아야 한다(시작이 거부되거나 보통 모드로 시작한다). 어떻게 되었는지 적는다.

- [ ] A-3 권한 설정에서 `disableBypassPermissionsMode`와 `disableAutoMode` 두 줄만 빼고 bypass 모드로 시작해도 `run_script`는 확인 창을 띄운다.

  1. Claude Code를 끝내고 이 PC의 PowerShell에서 다시 만든다.

     ```powershell
     powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\remove-probe.ps1
     powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\setup-probe.ps1 -AllowBypass
     ```

  2. `cd C:\dev\hq-mcp-probe` 뒤 `claude --dangerously-skip-permissions`로 시작한다. bypass 모드로 시작했는지 본다.
  3. A-1의 1번처럼 `run_script`를 부르게 한다. 확인 창이 떠야 한다.
  4. auto 모드를 쓸 수 있으면 Shift+Tab으로 auto 모드로 바꿔 3번을 다시 한다. 확인 창이 떠야 한다. 쓸 수 없으면 그렇게 적는다.
  5. Claude Code를 끝내고 스펙의 설정으로 되돌린다. A-4와 A-5는 이 설정으로 한다.

     ```powershell
     powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\remove-probe.ps1
     powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\setup-probe.ps1
     ```

     그다음 `cd C:\dev\hq-mcp-probe` 뒤 `claude`로 다시 시작한다.

- [ ] A-4 보통 모드에서 작업 폴더 밖 파일을 Read 도구로 읽으면 거부되고, Bash `cat`과 PowerShell `Get-Content`로 읽으면 확인 창이 뜬다. Edit 도구로 고치면 거부된다.

  대상은 `%TEMP%\hq-probe-outside.txt`다. 전체 경로는 setup-probe.ps1이 "작업 폴더 밖 파일:"로 찍어 준다. 그 경로를 Claude에게 알려 준다.

  1. "Read 도구로 <경로>를 읽어 줘" → 거부되어야 한다.
  2. "Bash 도구에서 `cat`으로 <경로>를 읽어 줘" → 확인 창이 떠야 한다. 뜨는 것만 보고 거절해도 된다.
  3. "PowerShell 도구에서 `Get-Content`로 <경로>를 읽어 줘" → 확인 창이 떠야 한다. 묻지 않고 읽으면 그렇게 적는다(그러면 PowerShell에는 deny 규칙만 남는다).
  4. "Edit 도구로 <경로>의 `outside`를 `changed`로 고쳐 줘" → 거부되어야 한다. 이 PC의 PowerShell에서 `Get-Content "$env:TEMP\hq-probe-outside.txt"`가 여전히 `outside`인지 본다.

- [ ] A-5 한글 7,400자 결과가 바로 올 때와 2분 넘게 기다렸다 올 때 모두 파일로 옮겨지지 않고 Claude에게 통째로 보인다.

  1. "`hq`의 `run_query`를 대상 `시험01 (192.0.2.10)`, 스크립트 `big`으로 불러 줘"라고 한다. 결과가 대화에 그대로 들어오는지(파일에 저장했다는 안내가 없는지) 본다. Claude에게 "결과의 마지막 열 글자가 뭐야?"라고 물어 `끝까지모두받았습니다`인지 본다.
  2. 스크립트를 `big-slow`로 바꿔 부르게 한다. 가짜 서버가 130초 기다린 뒤 같은 결과를 돌려준다. 1번과 같이 본다.

  결과가 파일로 옮겨지면 Claude가 그 파일을 읽을 수 있는지 보고 적는다.

### 끝내기

1. `C:\dev\hq-mcp-probe\probe-log.txt`에서 `initialize protocolVersion=` 줄의 값을 아래에 적는다. remove-probe.ps1이 이 파일도 지우므로 먼저 적는다.

   protocolVersion:

2. Claude Code를 끝내고 이 PC의 PowerShell에서 실행한다. 등록, `C:\dev\hq-mcp-probe`, `%TEMP%\hq-probe-outside.txt`가 지워진다.

   ```powershell
   powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\remove-probe.ps1
   ```
