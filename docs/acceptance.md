# HQ 완료 기준 확인

스펙 `docs/specs/2026-10-15-hq-design.md` 12장. 설치한 exe로 확인한다. 바탕화면 `09_앱_바로가기\HQ` 바로가기가 `%LOCALAPPDATA%\Programs\hq\hq.exe`를 실행한다(스펙 9장). 다시 빌드했다면 먼저 `scripts\make-shortcut.ps1`을 실행해 새 exe를 그곳에 복사한다.

**건드리지 않기로 정한 운영 서버는 어떤 확인에도 쓰지 않는다.**

**이 PC의 PowerShell과 HQ 탭을 구별한다.** 아래에서 "이 PC의 PowerShell"은 시작 메뉴에서 연 Windows PowerShell 창이고, 프롬프트가 `PS C:\...>`로 시작한다. HQ 탭의 프롬프트는 `[서버 주소]:`로 시작한다(AC-3의 `[host]: [별명] PS C:\...>`). HQ 탭에 친 명령은 서버에서 실행되므로, 이 PC의 PowerShell에서 돌리라는 명령을 HQ 탭에 붙여 넣으면 이 PC가 아니라 서버를 검사하게 된다.

확인한 날짜: 2026-10-30~2026-11-02 (체크포인트 D)

체크포인트에서 이미 확인한 항목은 그 아래 괄호에 적어 두었다. 칸은 이번에 확인하고 직접 체크한다.

- [x] AC-1 바로가기를 더블클릭하면 HQ 창이 뜬다. 떠 있을 때 다시 누르면 새로 뜨지 않고 그 창이 앞으로 온다.
      (체크포인트 C에서 exe로 확인함, 2026-10-19. 체크포인트 D에서 다시 확인함, 2026-10-30)
- [x] AC-2 엑셀로 서버를 등록하면 목록에 바로 보이고, 3초 안에 모든 상태 점이 채워진다(50대 기준).
      (체크포인트 D에서 40대로 확인함, 2026-10-30. 새로고침 뒤 거의 바로 채워졌다)
- [x] AC-3 네트워크가 정상일 때 서버를 더블클릭하면 5초 안에 `[host]: [별명] PS C:\...>` 프롬프트가 뜬다.
      (체크포인트 C에서 exe로 확인함, 2026-10-19. 체크포인트에서 쓴 서버 한 대에서만 확인했다. 체크포인트 D에서 여러 서버로 확인함, 2026-10-30, 수정한 exe로 다시 확인함, 2026-11-02)
- [ ] AC-4 2008 R2부터 2025까지 OS별로 한 대 이상에서 `hostname`, `Get-Service`, `ipconfig`가 정상 출력되고 한글이 깨지지 않는다.
      확인한 서버: 2008 R2 (없음) / 2012 R2 (○) / 2016 (미확인) / 2019 (○) / 2022 (없음) / 2025 (없음)
      (체크포인트 D, 2026-10-30. 2012 R2와 2019에서만 확인했다. 나머지 OS는 확인하지 못해 칸을 비워 둔다)
- [x] AC-5 누군가 같은 서버에 RDP로 접속해 있을 때 hq로 그 RDP 세션과 같은 계정(예: Administrator)으로 들어가도, 그 RDP 세션이 끊기지 않는다.
      (체크포인트 D에서 평소 쓰는 도메인 계정으로 확인함, 2026-10-30. HQ 탭이 열린 동안 같은 계정의 RDP 세션과 다른 계정의 RDP 세션이 모두 활성으로 남았다)
- [x] AC-6 탭 5개(서버 3대 + 같은 서버 2개)를 오가며 오래 걸리는 명령(`1..60 | % { $_; Start-Sleep 1 }`)을 돌려도 출력이 섞이거나 끊기지 않는다.
      (체크포인트 D에서 확인함, 2026-10-30. 서버 3대, 한 대에 탭 3개)
- [x] AC-7 열린 탭이 있을 때 창을 닫으면 확인을 묻는다. 닫은 뒤 서버에서 `Get-WSManInstance -ResourceURI shell -Enumerate`로 봤을 때 hq 세션이 남아 있지 않다.
      (체크포인트 B에서 개발 실행으로 확인함, 2026-10-19. 체크포인트 D에서 exe로 다시 확인함, 2026-10-30. 탭 5개를 연 채 닫은 뒤 이 PC의 셸은 새 탭 하나만 남았다)
- [x] AC-8 `Stop-Process -Id $PID -Force`로 연결을 끊기 직전에 명령을 미리 쳐 둬도 내 PC에서 실행되지 않는다.
      (체크포인트 B에서 개발 실행으로 `Stop-Process` → "연결 끊김" → 다시 연결까지만 확인함, 2026-10-19. 미리 쳐 둔 입력은 아직 확인하지 않았다)
      (체크포인트 D, 2026-10-30: 끊기는 순간에 계속 치면 맨 `PS>`가 뜨고 그 줄이 이 PC에서 실행되는 경쟁을 찾았다. 고친 exe로 다시 확인함, 2026-11-02: 끊기기 전후로 계속 쳐도 이 PC에서 아무것도 실행되지 않았고, 아무것도 치지 않아도 서버 쪽 세션이 끝나면 탭이 스스로 "연결 끊김"으로 바뀌고, `exit`는 "세션 종료"로 끝났다)

  미리 쳐 둔 입력 확인:

  1. HQ 탭에서 `Start-Sleep 5; Stop-Process -Id $PID -Force`를 치고 Enter를 누른다. 5초 뒤에 연결이 끊긴다.
  2. 그 5초 안에 `New-Item -ItemType File $HOME\hq-ac8.txt`를 치고 Enter를 누른다. 이 입력은 서버가 아직 바쁠 때 미리 쳐 둔 입력이 된다. 치는 글자가 화면에 보이지 않을 수 있는데 정상이다.
  3. "연결 끊김"이 뜬 뒤 이 PC의 PowerShell(맨 위 참고)에서 `Test-Path $HOME\hq-ac8.txt`를 실행한다. `False`여야 한다. `True`면 미리 친 명령이 내 PC에서 실행된 것이므로 실패다.

  2번의 Enter가 "연결 끊김"보다 늦었으면 그 회는 미리 쳐 둔 입력을 시험한 것이 아니므로 세지 않고 다시 한다. 파일이 생긴 적이 있으면 이 PC의 PowerShell에서 `Remove-Item $HOME\hq-ac8.txt`로 지운다.

- [x] AC-9 `%LOCALAPPDATA%\hq` 아래 어떤 파일(로그 포함)에도 비밀번호가 평문으로 없다. 다른 Windows 계정으로는 비밀번호 파일을 풀 수 없다.
      (체크포인트 D에서 확인함, 2026-10-30. HQ를 띄운 채 277개, 닫은 뒤 43개 파일에서 비밀번호가 나오지 않았다. 다른 계정 검사: 건너뜀(다른 계정 없음))

  평문 검사: HQ에 저장해 둔 서버 비밀번호 하나로, HQ를 띄운 채로 한 번, HQ를 닫고 몇 초 기다린 뒤 한 번 이 PC의 PowerShell(맨 위 참고)에서 실행한다(창이 닫힌 뒤에도 WebView2가 잠깐 파일을 쥐고 있을 수 있다). 아래 블록을 `& {`부터 `}`까지 한 번에 붙여 넣고, `찾을 비밀번호` 칸이 뜨면 비밀번호를 친다. 비밀번호는 그 칸에만 치므로 명령줄과 PSReadLine 기록에 남지 않고, 블록 안의 변수는 블록이 끝나면 함께 사라진다.

  판정: `검사한 파일: N개`(N은 1 이상) 아래에 아무 경로도 나오지 않으면 통과. 경로가 나오면 그 파일에 비밀번호가 평문으로 있다는 뜻이므로 실패다. 빨간 오류는 문장 전체를 맞춰 보지 말고 들어 있는 낱말만 본다. 오류 문장에 `사용 중`이나 `잠갔`이 들어 있거나, 영어로 `being used by another process`나 `locked a portion of the file`이 들어 있으면 HQ가 열어 두고 있는 파일(WebView2의 LOCK 파일 등)을 읽지 못했다는 뜻이다. Windows 표시 언어가 영어이면 이 오류는 영어로 나온다. HQ를 띄운 채로 돌릴 때는 이런 오류만 나와도 되고, HQ를 닫은 뒤에 돌린 결과에는 이런 오류도 없어야 통과다. 경고가 나오거나, `검사한 파일` 줄이 없거나 0개이거나, `'Pattern' 매개 변수가 null`(영어 `parameter 'Pattern' because it is null`) 같은 오류가 나오면 아무것도 검사하지 않은 것이므로 다시 실행한다.

  ```powershell
  & {
    $pw = Read-Host -AsSecureString '찾을 비밀번호'
    $plain = (New-Object System.Management.Automation.PSCredential 'x', $pw).GetNetworkCredential().Password
    if (-not $plain) { Write-Warning '비밀번호를 입력하지 않아 검사하지 않았다. 다시 실행한다.'; return }
    $files = Get-ChildItem -LiteralPath "$env:LOCALAPPDATA\hq" -Recurse -File -Force
    $hits = foreach ($enc in 'UTF8', 'Unicode') { $files | Select-String -SimpleMatch -List -CaseSensitive -Encoding $enc -Pattern $plain | ForEach-Object Path }
    "검사한 파일: $($files.Count)개"
    $hits | Sort-Object -Unique
  }
  ```

  다른 계정 검사: 이 PC에 다른 Windows 계정이 없으면 건너뛰고 이 줄에 `건너뜀(다른 계정 없음)`이라고 적는다. 있으면 먼저 이 PC의 PowerShell(맨 위 참고)에서 비밀번호 파일 하나를 공용 폴더에 복사하고, 다른 계정으로 PowerShell을 띄운다(`<다른 계정>`을 그 계정 이름으로 바꾼다).

  ```powershell
  Copy-Item -LiteralPath (Get-ChildItem "$env:LOCALAPPDATA\hq\secrets\*.dpapi" | Select-Object -First 1).FullName -Destination C:\Users\Public\hq-dpapi-test.dpapi
  runas /user:<다른 계정> powershell
  ```

  새로 뜬 다른 계정의 PowerShell 창에서 그 파일을 풀어 본다. DPAPI 오류, 곧 `키를 지정된 상태에서 사용하기에 부적합합니다`(영어 Windows는 `Key not valid for use in specified state`)이고 오류 아래 `CategoryInfo`에 `CryptographicException`이 보일 때만 통과다. `System.Security.SecureString`이 나오면 실패다. 경로를 찾을 수 없다는 오류나 `'String' 매개 변수가 null`(영어 `parameter 'String' because it is null`)이라는 오류는 검사가 되지 않은 것이므로 복사부터 다시 한다.

  ```powershell
  ConvertTo-SecureString (Get-Content C:\Users\Public\hq-dpapi-test.dpapi)
  ```

  끝나면 다른 계정의 창을 닫고, 처음에 쓰던 이 PC의 PowerShell 창(맨 위 참고)에서 복사본을 지운다.

  ```powershell
  Remove-Item C:\Users\Public\hq-dpapi-test.dpapi
  ```

- [x] AC-10 HQ가 떠 있을 때 HQ와 HQ가 띄운 프로세스(WebView2 등) 어느 것도 수신 포트를 열지 않는다. HQ를 띄우고 탭을 하나 연 채로 이 PC의 PowerShell(맨 위 참고)에서 아래를 실행해 아무것도 나오지 않으면 통과. `Get-Process`가 빨간 오류를 내면 HQ가 떠 있지 않은 것이므로 통과가 아니다.
      (체크포인트 C에서 exe로 확인함, 2026-10-19. 체크포인트 D에서 다시 확인함, 2026-10-30)

  ```powershell
  $ids = @((Get-Process hq).Id); $all = Get-CimInstance Win32_Process
  do { $n = $ids.Count; $ids = @($ids + @($all | Where-Object { $ids -contains $_.ParentProcessId } | ForEach-Object ProcessId) | Select-Object -Unique) } while ($ids.Count -gt $n)
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $ids -contains $_.OwningProcess }
  ```

- [x] AC-11 `hq.exe`를 동료 PC에 복사해 실행하면(초기 설정 한 번 뒤) AC-1~AC-10을 똑같이 통과한다.
      (체크포인트 D에서 동료 PC로 확인함, 2026-11-02. PC에 따라 달라지는 AC-1·2·3·6·7·8·9·10을 다시 봤고, 서버의 성질인 AC-4·AC-5는 이 PC의 결과로 갈음했다. 동료 PC에 쓴 exe는 AC-8 수정 전 판이다)

  동료 계정의 `%LOCALAPPDATA%\Programs\hq\`에 `hq.exe`를 복사하고 바탕화면에 그 exe의 바로가기를 만든다. 다운로드 폴더나 공유 폴더에서 바로 실행하는 것은 지원하는 방법이 아니다(스펙 9장: 다른 계정이 바꿔 넣을 수 있는 곳의 exe를 실행하지 않는다). 동료 PC의 PowerShell에서 `$source` 줄을 받은 `hq.exe`가 있는 경로로 바꾼 뒤, `& {`부터 `}`까지 한 번에 붙여 넣는다. 중간에 오류가 나면 그 자리에서 멈추고 바로가기를 만들지 않는다.

  ```powershell
  & {
    $ErrorActionPreference = 'Stop'
    $source = "$env:USERPROFILE\Downloads\hq.exe"
    $folder = Join-Path $env:LOCALAPPDATA 'Programs\hq'
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    Copy-Item -LiteralPath $source -Destination (Join-Path $folder 'hq.exe')
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'HQ.lnk'))
    $link.TargetPath = Join-Path $folder 'hq.exe'
    $link.WorkingDirectory = $folder
    $link.Save()
  }
  ```

  내려받은 exe는 처음 실행할 때 SmartScreen(Windows의 PC 보호) 창이 뜰 수 있다(복사해도 Zone.Identifier가 따라온다). 동료는 그 파일이 받기로 한 바로 그 파일인지(예: 보낸 사람과 `Get-FileHash` 값이 같은지) 확인한 뒤에 실행을 허용한다.

  그 바로가기로 HQ를 띄우고 초기 설정(TrustedHosts 추가, UAC 확인 한 번)을 한 뒤 AC-1~AC-10을 다시 확인한다.
