"""Claude에게 보내는 결과 JSON을 만든다. 스펙 9장.

서버 출력의 제어 문자를 지우고, 완성된 JSON이 크기 RESULT_BUDGET 안에 들도록 서버마다 몫을 나눠 앞에서부터 남긴다.
크기는 ASCII 글자를 1, 그 밖의 글자를 4로 센다. 한글은 \\uXXXX로 바꾸지 않는다. 바꾸면 한 글자가 6자가 된다.
"""

import json
import re
from collections.abc import Sequence

from hq.mcp.runner import RunOutcome

RESULT_BUDGET = 30_000  # 이 크기면 글자 수는 50,000 아래이고, 한글만 있어도 7,500자다(스펙 9장)
# 줄바꿈(\x0a)과 탭(\x09)을 뺀 C0, DEL, C1. 침해된 서버의 출력에 터미널 제어 문자가 섞일 수 있다. 화면에 보이지 않는
# 유니코드 태그 문자(U+E0000~U+E007F)와 글자 순서를 바꿔 보이게 하는 방향 제어 문자(U+202A~U+202E, U+2066~U+2069)도
# 지운다. 사용자는 못 보고 Claude만 읽는 글을 심는 데 쓰일 수 있다(스펙 9장)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\U000e0000-\U000e007f]")


def clean_text(text: str) -> str:
    """\\r\\n을 \\n으로 바꾸고, 줄바꿈과 탭 밖의 제어 문자와 태그 문자, 방향 제어 문자를 지운다."""
    return _CONTROL.sub("", text.replace("\r\n", "\n"))


def weighted_size(text: str) -> int:
    """ASCII 글자는 1, 그 밖의 글자(한글, 짝 없는 서로게이트도)는 4로 센 크기."""
    ascii_count = len(text.encode("ascii", "ignore"))
    return ascii_count + 4 * (len(text) - ascii_count)


def build_result(outcomes: Sequence[RunOutcome], *, budget: int = RESULT_BUDGET) -> str:
    """서버별 결과를 받은 순서대로 담은 JSON 글자. 예외를 던지지 않는다.

    output과 errors를 비운 틀만으로 budget을 넘으면 모든 output과 errors가 비고, 그때만 결과가 budget을 넘는다.
    """
    texts = [(clean_text(outcome.output), clean_text(outcome.errors)) for outcome in outcomes]
    sizes = [(_json_size(output), _json_size(errors)) for output, errors in texts]
    # 틀의 truncated는 받은 값으로 센다. 자르면 false가 true로 바뀌어 한 글자 줄 뿐이다
    frame = _dumps([_row(outcome, "", "", outcome.truncated) for outcome in outcomes])
    shares = _shares([output + errors for output, errors in sizes], budget - weighted_size(frame))
    rows = []
    for outcome, (output, errors), (_, errors_size), share in zip(outcomes, texts, sizes, shares, strict=True):
        kept_output = _fit(output, share - min(errors_size, share // 4))  # errors가 몫의 1/4까지 먼저 갖는다
        kept_errors = _fit(errors, share - _json_size(kept_output))  # output이 남긴 몫도 errors가 쓴다
        cut = len(kept_output) < len(output) or len(kept_errors) < len(errors)
        rows.append(_row(outcome, kept_output, kept_errors, outcome.truncated or cut))
    return _dumps(rows)


def _dumps(rows: list[dict]) -> str:
    return json.dumps({"results": rows}, ensure_ascii=False, separators=(",", ":"))


def _row(outcome: RunOutcome, output: str, errors: str, truncated: bool) -> dict:
    row = {
        "target": outcome.target,
        "status": outcome.status,
        "output": output,
        "errors": errors,
        "truncated": truncated,
        "output_chars": outcome.output_chars,
        "seconds": round(outcome.seconds, 1),  # 틀과 행이 이 함수를 함께 쓰므로 크기 계산도 반올림한 값으로 한다
    }
    if outcome.note:
        row["note"] = outcome.note
    return row


def _json_size(text: str) -> int:
    """JSON 문자열에 넣은 뒤의 크기(따옴표 빼고). 줄바꿈, 탭, 역슬래시, 따옴표는 두 글자가 된다."""
    return weighted_size(json.dumps(text, ensure_ascii=False)[1:-1])


def _shares(needs: list[int], room: int) -> list[int]:
    """서버별 몫. 적게 필요한 서버부터 min(필요한 양, 남은 양 // 남은 서버 수)를 준다. 다 쓰지 않은 몫은 뒤로 간다."""
    shares = [0] * len(needs)
    room = max(room, 0)
    left = len(needs)
    for index in sorted(range(len(needs)), key=needs.__getitem__):
        shares[index] = min(needs[index], room // left)
        room -= shares[index]
        left -= 1
    return shares


def _fit(text: str, room: int) -> str:
    """JSON에 넣은 크기가 room 안에 드는 가장 긴 앞부분.

    글자마다 따로 이스케이프되므로 앞부분이 길수록 크기도 커서 이분 탐색으로 찾는다. 글자 하나의 크기는 1 이상이라
    room + 1자부터는 들지 않는다.
    """
    if _json_size(text) <= room:
        return text
    fits, too_long = 0, min(len(text), room + 1)
    while too_long - fits > 1:
        middle = (fits + too_long) // 2
        if _json_size(text[:middle]) <= room:
            fits = middle
        else:
            too_long = middle
    return text[:fits]
