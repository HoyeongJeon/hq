# HQ 서버 설정 스크립트
# 이 서버에서 "관리자 권한으로 실행"한 PowerShell에 전체를 붙여넣고 Enter를 누르세요.
# Windows Server 2008 R2(PowerShell 2.0)부터 동작합니다. 이미 된 설정은 건너뜁니다.
& {
    $Account = '__HQ_ACCOUNT__'
    $AllowFrom = @(__HQ_ALLOW_FROM__)
    $done = @()
    $winrmChanged = $false  # WinRM 설정을 바꿨으면 끝에 WinRM을 다시 시작한다(LocalAccountTokenFilterPolicy와 방화벽은 필요 없음).
    function Say([string]$text) { Write-Host "[HQ] $text" }

    if ((Get-Variable -Name PSVersionTable -ErrorAction SilentlyContinue) -eq $null -or (Get-Service -Name WinRM -ErrorAction SilentlyContinue) -eq $null) {
        Say 'PowerShell 2.0과 WinRM이 없습니다. Windows Server 2008/2003이면 Windows Management Framework 2.0(KB968930)을 먼저 설치하세요.'
        return
    }
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Say '관리자 권한으로 연 PowerShell에서 실행하세요.'
        return
    }

    # 바꾸는 명령에는 모두 -ErrorAction Stop을 붙인다. 실패하면 catch로 가서 "바꾼 것"에 넣지 않고 WinRM도 다시 시작하지 않는다.

    # 1. WinRM 켜기 (5985 리스너와 방화벽 예외)
    #    원격 세션 안이면 WinRM은 이미 켜져 있다. Enable-PSRemoting은 세션 구성을 다시 등록하면서
    #    WinRM을 스스로 다시 시작할 수 있으므로 원격 세션 안에서는 부르지 않는다.
    $listening = $false
    if ($PSSenderInfo -ne $null) {
        $listening = $true
    } elseif ((Get-Service -Name WinRM).Status -eq 'Running') {
        foreach ($listener in @(Get-ChildItem WSMan:\localhost\Listener -ErrorAction SilentlyContinue)) {
            if ($listener.Keys -contains 'Transport=HTTP') { $listening = $true }
        }
    }
    if (-not $listening) {
        try {
            Enable-PSRemoting -Force -ErrorAction Stop | Out-Null
            $done += 'WinRM 켜기 (Enable-PSRemoting)'
            $winrmChanged = $true
        } catch {
            Say "WinRM을 켜지 못했습니다: $($_.Exception.Message)"
            return
        }
    }

    # 2. 동시 세션과 메모리 한도 (2008 R2 기본값은 계정당 5개, 셸당 150MB)
    $shellLimit = 'WSMan:\localhost\Shell\MaxShellsPerUser'
    $current = [int](Get-Item $shellLimit).Value
    if ($current -lt 30) {
        try {
            Set-Item $shellLimit 30 -Force -ErrorAction Stop
            $done += "계정당 동시 세션 $current -> 30"
            $winrmChanged = $true
        } catch {
            Say "계정당 동시 세션 한도를 바꾸지 못했습니다: $($_.Exception.Message)"
        }
    }
    $memoryLimit = 'WSMan:\localhost\Shell\MaxMemoryPerShellMB'
    $current = [int](Get-Item $memoryLimit).Value
    if ($current -lt 1024) {
        try {
            Set-Item $memoryLimit 1024 -Force -ErrorAction Stop
            $done += "셸당 메모리 ${current}MB -> 1024MB"
            $winrmChanged = $true
        } catch {
            Say "셸당 메모리 한도를 바꾸지 못했습니다: $($_.Exception.Message)"
        }
    }

    # 3. 내장 Administrator가 아닌 로컬 관리자 계정이 원격에서 관리자 권한을 갖게 하기
    $name = $Account
    $prefix = ''
    if ($Account.Contains('\')) {
        $parts = $Account.Split([char]'\')
        $prefix = $parts[0]
        $name = $parts[1]
    }
    if ($prefix -eq '' -or $prefix -eq '.' -or $prefix -eq $env:COMPUTERNAME) {
        $sid = $null
        try {
            $sid = (New-Object Security.Principal.NTAccount("$env:COMPUTERNAME\$name")).Translate([Security.Principal.SecurityIdentifier]).Value
        } catch { }
        if ($sid -eq $null) {
            Say "이 서버에 로컬 계정 '$name'이 없습니다. 계정 이름을 확인하세요."
        } elseif (-not $sid.EndsWith('-500')) {
            $policy = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
            $value = (Get-ItemProperty -Path $policy -Name LocalAccountTokenFilterPolicy -ErrorAction SilentlyContinue).LocalAccountTokenFilterPolicy
            if ($value -ne 1) {
                try {
                    New-ItemProperty -Path $policy -Name LocalAccountTokenFilterPolicy -PropertyType DWord -Value 1 -Force -ErrorAction Stop | Out-Null
                    $done += "LocalAccountTokenFilterPolicy = 1 ($name)"
                    Say "주의: '$name' 같은 로컬 관리자 계정이 원격에서도 관리자 권한을 갖습니다. 같은 비밀번호를 쓰는 계정이 털리면 다른 서버로 번지기 쉬우니 서버마다 다른 긴 비밀번호를 쓰세요."
                } catch {
                    Say "LocalAccountTokenFilterPolicy를 바꾸지 못했습니다: $($_.Exception.Message)"
                }
            }
        }
    }

    # 4. (선택) 5985를 관리자 PC IP에서만 받기. 규칙 이름은 언어마다 달라서 포트로 찾는다.
    if ($AllowFrom.Count -gt 0) {
        try {
            $firewall = New-Object -ComObject HNetCfg.FwPolicy2
            $rules = @($firewall.Rules | Where-Object { $_.Direction -eq 1 -and $_.Protocol -eq 6 -and $_.LocalPorts -eq '5985' -and $_.Action -eq 1 -and $_.Enabled })
            if ($rules.Count -eq 0) { Say '5985 수신 허용 규칙을 찾지 못했습니다. 방화벽 설정을 확인하세요.' }
            foreach ($rule in $rules) {
                $rule.RemoteAddresses = ($AllowFrom -join ',')
                $done += "방화벽 규칙 '$($rule.Name)'의 원격 주소 -> $($AllowFrom -join ', ')"
            }
        } catch {
            Say "방화벽을 바꾸지 못했습니다(Windows Server 2003이면 직접 설정하세요): $($_.Exception.Message)"
        }
    }

    # 5. 한 일 요약. 스스로 확인(Test-WSMan)이 맨 끝에 오도록 WinRM 다시 시작보다 먼저 보여 준다.
    if ($done.Count -eq 0) {
        Say '바꾼 것 없음. 이미 설정되어 있습니다.'
    } else {
        Say '바꾼 것:'
        foreach ($item in $done) { Say "  - $item" }
    }

    # 6. WinRM 설정을 바꿨으면 WinRM을 다시 시작하고, 끝으로 스스로 확인.
    #    원격 세션 안($PSSenderInfo가 있음)에서는 다시 시작하지 않는다. WinRM을 멈춘 뒤 시작하기 전에
    #    이 세션이 끝나 버리면 WinRM이 멈춘 채로 남아 서버에 원격으로 들어갈 수 없게 된다.
    if ($winrmChanged -and $PSSenderInfo -ne $null) {
        Say '원격 세션 안에서 실행해서 WinRM을 다시 시작하지 않았습니다. 바뀐 한도는 새 세션부터 적용됩니다. 적용되지 않으면 서버 콘솔(RDP)에서 Restart-Service WinRM 을 실행하세요.'
    } elseif ($winrmChanged) {
        Say '바꾼 WinRM 설정을 적용하려고 WinRM을 다시 시작합니다. 지금 이 서버에 열린 원격 세션(hq 탭 포함)은 끊깁니다. 끊긴 탭은 [다시 연결]을 누르세요.'
        try {
            Restart-Service -Name WinRM -Force -ErrorAction Stop
            Say 'WinRM 다시 시작: 완료'
        } catch {
            Say "WinRM을 다시 시작하지 못했습니다: $($_.Exception.Message)"
        }
    }
    try {
        Test-WSMan -ComputerName localhost -ErrorAction Stop | Out-Null
        Say 'WinRM 응답 확인: 정상'
    } catch {
        Say "WinRM 응답 확인 실패: $($_.Exception.Message)"
    }
}
