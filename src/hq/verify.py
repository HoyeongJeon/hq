"""실서버 한 대로 검증 항목(V1, V2, V5 일부, V6, V7)을 확인하는 도구. 사용자가 직접 실행한다.

    uv run python -m hq.verify --host 10.0.1.21 --user Administrator --nick 웹01
    uv run python -m hq.verify --host 10.0.1.21 --user Administrator --try-users .\\Administrator WEB01\\Administrator

비밀번호는 사용자가 입력하고, %LOCALAPPDATA%\\hq 아래 임시 폴더의 DPAPI 파일에만 잠시 있다가 끝나면 지워진다.
TMPDIR는 쓰지 않는다. 다른 프로그램이 모든 사용자가 쓸 수 있는 폴더로 바꿔 두었을 수 있다.
"""

import argparse
import getpass
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from hq.bootstrap import BootstrapParams, build_command, encoded_argv, ps_json_literal, reason_for_exit
from hq.paths import data_dir
from hq.secret_store import SecretStore
from hq.validation import ValidationError, validate_host, validate_username

STEPS = """\
새 PowerShell 창이 두 번 열립니다.

[1회차] 열린 창에서 차례로 확인하세요.
  1. 프롬프트가 [{host}]: [{nick}] PS ...> 모양인지 (V1, V2)
  2. ipconfig 와 Get-Service | Select-Object -First 5 의 한글이 깨지지 않는지 (V5 일부)
  3. exit 를 입력하면 창이 닫히고, 여기에 '종료 코드 0' 이 찍히는지 (V7 정상 종료)

[2회차]
  4. Stop-Process -Id $PID -Force 를 입력하세요. 그 세션의 서버 쪽 프로세스만 끝냅니다.
     창이 저절로 닫히고, 여기에 '종료 코드 20' 이 찍히는지 (V7 끊김)
"""


def username_check_script(host: str, usernames: list[str], secret_path: str) -> str:
    payload = {"host": host, "users": usernames, "secret": secret_path}
    return (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
        f"$p = {ps_json_literal(payload)} | ConvertFrom-Json\n"
        "$secure = ConvertTo-SecureString -String ((Get-Content -LiteralPath $p.secret -Raw).Trim())\n"
        "foreach ($u in $p.users) {\n"
        "    try {\n"
        "        $cred = New-Object System.Management.Automation.PSCredential($u, $secure)\n"
        "        $s = New-PSSession -ComputerName $p.host -Credential $cred"
        " -Authentication Negotiate -ErrorAction Stop\n"
        "        Remove-PSSession $s\n"
        '        Write-Output "[성공] $u"\n'
        "    } catch {\n"
        '        Write-Output "[실패] $u : $($_.FullyQualifiedErrorId)"\n'
        "    }\n"
        "}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hq.verify", description="HQ 실서버 검증")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--nick", default="검증")
    parser.add_argument("--try-users", nargs="+", metavar="ID", help="V6: 차례로 시험할 ID 표기")
    args = parser.parse_args(argv)
    try:
        host = validate_host(args.host)
        user = validate_username(args.user)
        tries = [validate_username(u) for u in (args.try_users or [])]
    except ValidationError as exc:
        parser.error(str(exc))

    work = Path(tempfile.mkdtemp(prefix="hq-verify-", dir=data_dir()))
    try:
        store = SecretStore(work)
        server_id = uuid.uuid4().hex
        password = getpass.getpass(f"{user}@{host} 비밀번호(화면에 보이지 않음): ")
        if not password:
            print("비밀번호가 비어 있습니다.", file=sys.stderr)
            return 2
        store.save(server_id, password)
        secret = str(store.path_for(server_id))
        if tries:
            subprocess.run(encoded_argv(username_check_script(host, tries, secret), no_exit=False), check=False)
            return 0
        print(STEPS.format(host=host, nick=args.nick))
        command = build_command(BootstrapParams(host=host, username=user, nickname=args.nick, secret_path=secret))
        for round_no in (1, 2):
            input(f"[{round_no}회차] Enter를 누르면 창을 엽니다...")
            code = subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE).wait()
            print(f"종료 코드 {code} → {reason_for_exit(code).value}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
