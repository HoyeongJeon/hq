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
        if _pattern(ip).search(script):
            return ip
    return None


def _pattern(ip: str) -> re.Pattern[str]:
    if ":" in ip:  # IPv6
        return re.compile(re.escape(ip), re.IGNORECASE)
    # 더 긴 숫자의 일부만 뺀다. \\10.0.1.21\c$와 10.0.1.21:445는 맞고 10.0.1.210, 110.0.1.21, 10.0.1.21.5는 아니다.
    # \d가 아니라 [0-9]로 쓴다. \d는 다른 문자의 숫자도 맞으므로 그런 글자가 붙은 주소를 놓친다
    return re.compile(rf"(?<![0-9.]){re.escape(ip)}(?![0-9]|\.[0-9])")
