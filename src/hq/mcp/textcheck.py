"""스크립트를 다른 무엇보다 먼저 거르는 글자 검사. 스펙 6장(금지 IP)과 7.1(파서 앞의 글자 검사).

글자만 본다. 스크립트를 실행하지도 파싱하지도 않는다. 둘 다 안전 장치이므로 애매하면 걸리는 쪽으로 만든다.
"""

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


# 숫자는 ASCII만 본다. \d는 다른 문자의 숫자도 맞으므로 그런 글자가 붙은 주소를 놓친다
_DIGITS = frozenset("0123456789")
# 숫자 묶음 N 바로 앞에 오면 N이 이름의 일부라는 글자: 영문, _, -, 그리고 빈 조각을 만드는 점
_BEFORE_LABEL = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-.")


def _ipv4_in(script: str, ip: str) -> bool:
    r"""ip가 더 긴 점 숫자의 일부가 아닌 자리에 글자로 있으면 참. 스크립트 길이에 비례하는 시간에 끝난다.

    뒤에 숫자나 ".숫자"가 오거나(10.0.1.210, 10.0.1.21.5) 바로 앞이 숫자면(110.0.1.21) 다른 주소의 일부다. 그 밖에는
    바로 앞의 숫자 묶음 N((?:[0-9]+\.)+: 숫자들 뒤에 점 하나, 이것이 한 번 이상)으로 판단한다. N이 없으면 찾았다
    (\\10.0.1.21\c$, 10.0.1.21:445, x.10.0.1.21, ..10.0.1.21, 5.10.0.1.21..10.0.1.21). N 앞이 영문, _, -, 점이면 N은
    이름의 일부이고 이름은 그 주소로 풀리므로 찾았다(a1.10.0.1.21.nip.io, x.5.10.0.1.21, x..5.10.0.1.21). 그 밖(맨 앞,
    공백, 따옴표 등)이면 더 긴 점 숫자의 꼬리다(5.10.0.1.21, 1.2.10.0.1.21).
    """
    # 겹치지 않게 찾아도 놓치지 않는다: 뒤 조건을 지난 자리 뒤에는 숫자도 ".숫자"도 없으므로, 그 자리와 겹치며
    # 더 뒤까지 이어지는 다른 자리는 있을 수 없다. 같은 까닭으로 N은 앞에서 맞은 자리 안까지 거슬러 가지 않으므로
    # 자리마다 거슬러 가는 길은 서로 겹치지 않는다
    for match in re.finditer(rf"{re.escape(ip)}(?![0-9]|\.[0-9])", script):
        at = match.start()
        if at > 0 and script[at - 1] in _DIGITS:
            continue
        start = _digit_groups_start(script, at)
        if start == at or (start > 0 and script[start - 1] in _BEFORE_LABEL):
            return True
    return False


def _digit_groups_start(script: str, end: int) -> int:
    r"""end 바로 앞에서 (?:[0-9]+\.)+에 맞는 가장 긴 글자의 시작. 맞는 글자가 없으면 end."""
    start = end
    while start >= 2 and script[start - 1] == "." and script[start - 2] in _DIGITS:
        start -= 2
        while start > 0 and script[start - 1] in _DIGITS:
            start -= 1
    return start
