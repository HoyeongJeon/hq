import pytest
from ps import run_ps

from hq.secret_store import SecretStore

pytestmark = pytest.mark.integration

SERVER_ID = "0123456789abcdef0123456789abcdef"
PASSWORD = "P@ss 한글'\"$x`;끝"


def test_powershell_reads_the_secret_file_from_a_korean_path(tmp_path):
    store = SecretStore(tmp_path / "한글 경로" / "secrets")
    store.save(SERVER_ID, PASSWORD)
    script = (
        "$s = ConvertTo-SecureString -String ((Get-Content -LiteralPath $env:HQ_FILE -Raw).Trim())\n"
        "$b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)\n"
        "$t = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)\n"
        "Write-Output ($t -ceq $env:HQ_EXPECTED)\n"
    )
    result = run_ps(script, env={"HQ_FILE": str(store.path_for(SERVER_ID)), "HQ_EXPECTED": PASSWORD})
    assert result.stdout.strip() == "True", result.stderr
