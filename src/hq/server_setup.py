"""서버 설정 스크립트를 만든다. 사용자가 서버의 관리자 PowerShell에 붙여넣어 한 번 실행한다."""

from collections.abc import Sequence

from hq.paths import resource
from hq.validation import validate_ipv4, validate_username


def build_server_setup_script(account: str, allow_from: Sequence[str] = ()) -> str:
    user = validate_username(account)
    listed = ",".join(f"'{validate_ipv4(ip)}'" for ip in allow_from)
    template = resource("ps/server-setup.ps1").read_text(encoding="utf-8-sig")
    # IP 목록을 먼저 넣는다. ID에는 밑줄을 쓸 수 있어서, 먼저 넣은 ID가 다른 자리표시자로 다시 바뀌면 따옴표가 깨진다.
    return template.replace("__HQ_ALLOW_FROM__", listed).replace("__HQ_ACCOUNT__", user)
