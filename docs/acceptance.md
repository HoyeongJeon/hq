# HQ 완료 기준 확인

스펙 `docs/specs/2026-10-15-hq-design.md` 12장. 설치한 exe로 확인한다. 바탕화면 `09_앱_바로가기\HQ` 바로가기가 `%LOCALAPPDATA%\Programs\hq\hq.exe`를 실행한다(스펙 9장). 다시 빌드했다면 먼저 `scripts\make-shortcut.ps1`을 실행해 새 exe를 그곳에 복사한다.

**건드리지 않기로 정한 운영 서버는 어떤 확인에도 쓰지 않는다.**

확인한 날짜: (YYYY-MM-DD)

체크포인트에서 이미 확인한 항목은 그 아래 괄호에 적어 두었다. 칸은 이번에 확인하고 직접 체크한다.

- [ ] AC-1 바로가기를 더블클릭하면 HQ 창이 뜬다. 떠 있을 때 다시 누르면 새로 뜨지 않고 그 창이 앞으로 온다.
      (체크포인트 C에서 exe로 확인함, 2026-10-19)
- [ ] AC-2 엑셀로 서버를 등록하면 목록에 바로 보이고, 3초 안에 모든 상태 점이 채워진다(50대 기준).
- [ ] AC-3 네트워크가 정상일 때 서버를 더블클릭하면 5초 안에 `[host]: [별명] PS C:\...>` 프롬프트가 뜬다.
      (체크포인트 C에서 exe로 확인함, 2026-10-19. 체크포인트에서 쓴 서버 한 대에서만 확인했다)
- [ ] AC-4 2008 R2부터 2025까지 OS별로 한 대 이상에서 `hostname`, `Get-Service`, `ipconfig`가 정상 출력되고 한글이 깨지지 않는다.
      확인한 서버: 2008 R2 ( ) / 2012 R2 ( ) / 2016 ( ) / 2019 ( ) / 2022 ( ) / 2025 ( )
- [ ] AC-5 누군가 같은 서버에 Administrator로 RDP 접속해 있어도, hq로 들어갔을 때 그 RDP 세션이 끊기지 않는다.
- [ ] AC-6 탭 5개(서버 3대 + 같은 서버 2개)를 오가며 오래 걸리는 명령(`1..60 | % { $_; Start-Sleep 1 }`)을 돌려도 출력이 섞이거나 끊기지 않는다.
- [ ] AC-7 열린 탭이 있을 때 창을 닫으면 확인을 묻는다. 닫은 뒤 서버에서 `Get-WSManInstance -ResourceURI shell -Enumerate`로 봤을 때 hq 세션이 남아 있지 않다.
      (체크포인트 B에서 개발 실행으로 확인함, 2026-10-19)
- [ ] AC-8 `Stop-Process -Id $PID -Force`로 연결을 끊기 직전에 명령을 미리 쳐 둬도 내 PC에서 실행되지 않는다.
      (체크포인트 B에서 개발 실행으로 확인함, 2026-10-19)
- [ ] AC-9 `%LOCALAPPDATA%\hq` 아래 어떤 파일(로그 포함)에도 비밀번호가 평문으로 없다. 다른 Windows 계정으로는 비밀번호 파일을 풀 수 없다.

  평문 검사: HQ에 저장해 둔 서버 비밀번호 하나로, HQ를 띄운 채로 한 번, 닫은 뒤 한 번 내 PowerShell에서 실행한다. 비밀번호는 `Read-Host`가 묻는 칸에만 치므로 명령줄과 PSReadLine 기록에 남지 않는다. 파일 수 아래에 아무 경로도 나오지 않으면 통과. 경로가 나오면 그 파일에 비밀번호가 평문으로 있다는 뜻이다. 빨간 오류는 HQ가 쓰고 있어 읽지 못한 파일이다.

  ```powershell
  $pw = Read-Host -AsSecureString '찾을 비밀번호'
  $plain = (New-Object System.Management.Automation.PSCredential 'x', $pw).GetNetworkCredential().Password
  $files = Get-ChildItem -LiteralPath "$env:LOCALAPPDATA\hq" -Recurse -File -Force
  $hits = foreach ($enc in 'UTF8', 'Unicode') { $files | Select-String -SimpleMatch -List -CaseSensitive -Encoding $enc -Pattern $plain | ForEach-Object Path }
  Remove-Variable pw, plain
  "검사한 파일: $($files.Count)개"
  $hits | Sort-Object -Unique
  ```

  다른 계정 검사: 이 PC에 다른 Windows 계정이 없으면 건너뛰고 이 줄에 `건너뜀(다른 계정 없음)`이라고 적는다. 있으면 먼저 내 PowerShell에서 비밀번호 파일 하나를 공용 폴더에 복사하고, 다른 계정으로 PowerShell을 띄운다(`<다른 계정>`을 그 계정 이름으로 바꾼다).

  ```powershell
  Copy-Item -LiteralPath (Get-ChildItem "$env:LOCALAPPDATA\hq\secrets\*.dpapi" | Select-Object -First 1).FullName -Destination C:\Users\Public\hq-dpapi-test.dpapi
  runas /user:<다른 계정> powershell
  ```

  새로 뜬 다른 계정의 PowerShell 창에서 그 파일을 풀어 본다. 오류(보통 `키가 지정된 상태에서 사용하기에 유효하지 않습니다`)가 나면 통과, `System.Security.SecureString`이 나오면 실패다.

  ```powershell
  ConvertTo-SecureString (Get-Content C:\Users\Public\hq-dpapi-test.dpapi)
  ```

  끝나면 내 PowerShell에서 복사본을 지운다.

  ```powershell
  Remove-Item C:\Users\Public\hq-dpapi-test.dpapi
  ```

- [ ] AC-10 HQ가 떠 있을 때 HQ와 HQ가 띄운 프로세스(WebView2 등) 어느 것도 수신 포트를 열지 않는다. HQ를 띄우고 탭을 하나 연 채로 내 PowerShell에서 아래를 실행해 아무것도 나오지 않으면 통과.
      (체크포인트 C에서 exe로 확인함, 2026-10-19)

  ```powershell
  $ids = @((Get-Process hq).Id); $all = Get-CimInstance Win32_Process
  do { $n = $ids.Count; $ids = @($ids + @($all | Where-Object { $ids -contains $_.ParentProcessId } | ForEach-Object ProcessId) | Select-Object -Unique) } while ($ids.Count -gt $n)
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $ids -contains $_.OwningProcess }
  ```

- [ ] AC-11 `hq.exe`를 동료 PC에 복사해 실행하면(초기 설정 한 번 뒤) AC-1~AC-10을 똑같이 통과한다.

  동료 계정의 `%LOCALAPPDATA%\Programs\hq\`에 `hq.exe`를 복사하고 바탕화면에 그 exe의 바로가기를 만든다. 다운로드 폴더나 공유 폴더에서 바로 실행하는 것은 지원하는 방법이 아니다(스펙 9장: 다른 계정이 바꿔 넣을 수 있는 곳의 exe를 실행하지 않는다). 동료 PC의 PowerShell에서 첫 줄을 받은 `hq.exe`가 있는 경로로 바꿔 실행한다.

  ```powershell
  $source = "$env:USERPROFILE\Downloads\hq.exe"
  $folder = Join-Path $env:LOCALAPPDATA 'Programs\hq'
  New-Item -ItemType Directory -Force -Path $folder | Out-Null
  Copy-Item -LiteralPath $source -Destination (Join-Path $folder 'hq.exe')
  $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'HQ.lnk'))
  $link.TargetPath = Join-Path $folder 'hq.exe'
  $link.WorkingDirectory = $folder
  $link.Save()
  ```

  그 바로가기로 HQ를 띄우고 초기 설정(TrustedHosts 추가, UAC 확인 한 번)을 한 뒤 AC-1~AC-10을 다시 확인한다.
