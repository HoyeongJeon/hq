"""스크립트를 다른 무엇보다 먼저 거르는 글자 검사. 스펙 6장(금지 IP)과 7.1(파서 앞의 글자 검사).

글자만 본다. 스크립트를 실행하지도 파싱하지도 않는다. 둘 다 안전 장치이므로 애매하면 걸리는 쪽으로 만든다.
"""

import bisect
import re
from collections.abc import Sequence

# 5.1 파서는 파싱만 해도 using 문과 Import-DscResource에 적힌 경로를 찾아본다. 경로가 UNC면 검사만으로 그 컴퓨터에
# 접속한다. using과 module 사이의 줄 잇기(`)와 <# #> 주석도 건너뛴다. $using:x 같은 변수와 -using 같은 글자는 아니다.
# 사이의 반복은 되돌아가지 않게(++) 한다. +면 주석 하나가 뒤의 #>까지 늘어나는 모든 경우를 시험해 주석 하나마다
# 시간이 두 배가 되고, re는 GIL을 쥔 채 돌아 서버 전체가 멈춘다. PowerShell의 주석도 처음 만나는 #>에서 끝나므로
# 놓치는 using 문은 없다
_USING = re.compile(r"(?is)(?<![\w$:-])using(?:\s|`|<#.*?#>)++(?:module|assembly|namespace)(?![\w-])")
_DSC = re.compile(r"(?i)import-dscresource")
_NOT_PARSED = "검사기가 파싱만 해도 적힌 경로를 찾아보므로 검사하지 않습니다. 문자열이나 주석 속의 글자도 걸립니다."


def parser_hazard(script: str) -> str | None:
    """파서에 넘기면 안 되는 스크립트면 그 이유, 아니면 None."""
    if _USING.search(script):
        return f"using 문(using module, using assembly, using namespace)이 있습니다. {_NOT_PARSED}"
    if _DSC.search(script):
        return f"Import-DscResource가 있습니다. {_NOT_PARSED}"
    return None


def never_ip_in(script: str, never: Sequence[str]) -> str | None:
    """스크립트에 글자로 든 금지 IP. never 순서로 처음 찾은 것을 돌려준다. 없으면 None.

    never는 canonical_ip로 고친 글자다. 앞에 0을 붙인 숫자, 이어 붙여 만든 글자, 이름으로 적은 주소는 찾지 못한다.
    """
    for ip in never:
        if ":" in ip:  # IPv6
            if re.search(re.escape(ip), script, re.IGNORECASE):
                return ip
        elif _ipv4_in(script, ip):
            return ip
    return None


# 숫자와 점은 ASCII만 본다. \d는 다른 문자의 숫자도 맞으므로 그런 글자가 붙은 주소를 놓친다
_DIGITS = frozenset("0123456789")
_NUMBER = _DIGITS | {"."}
_LABEL = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-")  # 이름 조각에 드는 글자(숫자 말고)
_NUMBER_RUN = re.compile(r"[0-9.]+")


def _ipv4_in(script: str, ip: str) -> bool:
    r"""ip가 더 긴 점 숫자의 일부가 아닌 자리에 글자로 있으면 참. 스크립트 길이에 비례하는 시간에 끝난다.

    뒤에 숫자나 ".숫자"가 오면(10.0.1.210, 10.0.1.21.5) 다른 주소의 일부다. 앞은 주소 바로 앞의 점 숫자 줄 R로
    판단한다. R이 없으면 찾았다(\\10.0.1.21\c$, 10.0.1.21:445). R이 숫자로 끝나면 더 긴 숫자의 꼬리다(110.0.1.21).
    R이 점으로 끝나고 숫자가 없으면 찾았다(..10.0.1.21). R이 점으로 끝나고 숫자가 있으면 R 앞 글자로 가른다:
    영문, _, -이면 R은 이름의 일부이고 이름은 그 주소로 풀리므로 찾았다(a1.10.0.1.21.nip.io, x.5.10.0.1.21).
    그 밖(맨 앞, 공백, 따옴표 등)이면 더 긴 점 숫자의 꼬리다(5.10.0.1.21, 1.2.10.0.1.21).
    """
    run_starts: list[int] | None = None  # 점 숫자 줄의 시작 자리들. 처음 필요할 때 한 번만 만든다
    # 겹치지 않게 찾아도 놓치지 않는다: 뒤 조건을 지난 자리 뒤에는 숫자도 ".숫자"도 없으므로, 그 자리와 겹치며
    # 더 뒤까지 이어지는 다른 자리는 있을 수 없다
    for match in re.finditer(rf"{re.escape(ip)}(?![0-9]|\.[0-9])", script):
        at = match.start()
        if at == 0 or script[at - 1] not in _NUMBER:
            return True
        if script[at - 1] != ".":
            continue
        # R 끝의 점들을 거슬러 간다. 앞에서 찾은 자리는 숫자로 끝나므로 거슬러 가는 길은 서로 겹치지 않는다
        last = at - 1
        while last >= 0 and script[last] == ".":
            last -= 1
        if last < 0 or script[last] not in _DIGITS:
            return True
        if run_starts is None:
            run_starts = [run.start() for run in _NUMBER_RUN.finditer(script)]
        start = run_starts[bisect.bisect_right(run_starts, last) - 1]
        if start > 0 and script[start - 1] in _LABEL:
            return True
    return False
