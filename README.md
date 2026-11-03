# HQ

A local desktop app for reaching Windows servers over PowerShell remoting (WinRM) instead of RDP.

Double-click the shortcut and the HQ window opens. Double-click a server in the list and a tab opens; commands typed in that tab run on the server.

## Why

When several admins sign in over RDP with the same admin account, whoever signs in last takes over the session of whoever was already there. Most server work only needs PowerShell. Each WinRM connection gets its own process on the server, so it never touches an RDP session.

## Features

- **Server management:** add, edit, delete, change password, list by group, search, info panel
- **Excel bulk import:** download a template, preview each row (new, already registered, error), choose whether to overwrite
- **Status dots:** checks port 5985 on every server in parallel (1 second per server)
- **Terminal:** a tab per server, and several tabs per server if you want. Tab titles and prompts show the server's nickname
- **Disconnects:** when a connection drops, the tab switches to "disconnected" and offers a reconnect button. If sign-in is refused, HQ asks for the password again
- **Setup helpers:** adds missing servers to this PC's TrustedHosts (one UAC prompt) and shows a setup script for each server

The interface is in Korean.

## Security

- **Passwords stay on this PC.** They are encrypted with Windows DPAPI (current user) and stored under `%LOCALAPPDATA%\hq\`. There is no sharing or sync.
- **HQ opens no network port.** The window (WebView2) and the app talk only through function calls inside one process.
- **Input meant for a server never runs on this PC.** When a remote session drops, PowerShell normally falls back to this PC's prompt, and anything typed ahead could run locally at that moment. HQ closes that path by ending the tab's PowerShell as soon as the session drops. A tab receives no input until it signals that this safeguard is in place.
- **Logs never contain passwords or terminal input and output.**
- **Known limits:** authentication is NTLM. It does not verify the server's identity, so it cannot stop an attacker who is already in the middle of the internal network. Use a different long password on each server. Malware running under your own account can decrypt the DPAPI files.

The full threat model is in section 8 of the [design document](docs/specs/2026-10-15-hq-design.md).

## Requirements

- **This PC:** Windows 11 with Windows PowerShell 5.1. The window is drawn by the WebView2 runtime that ships with Windows 11. Not tested on Windows 10
- **Servers:** Windows Server with WinRM over HTTP on port 5985. Tested on Windows Server 2012 R2 and 2019
- **Accounts:** write a server's local account as `user` or `.\user`, and a domain account as `DOMAIN\user`. A domain account written without the domain is checked against the server's local accounts, and sign-in is refused

Linux servers and SSH are not supported.

## Install

```powershell
uv sync
.\scripts\build.ps1          # fast tests, then dist\hq.exe
.\scripts\make-shortcut.ps1  # copies it to %LOCALAPPDATA%\Programs\hq\ and creates the shortcut
```

`make-shortcut.ps1` copies the exe into a folder only your account can write to, so you never run, with your own rights, an exe another account swapped in. Do not run `hq.exe` straight from a downloads or shared folder. Installing on another PC is described under AC-11 in the [acceptance document](docs/acceptance.md).

After a rebuild, close HQ and run `make-shortcut.ps1` again.

## First run

1. **This PC:** after you add a server, a banner says some servers are missing from TrustedHosts. Press **설정하기** (Set up) to add them once with admin rights.
2. **Servers:** for any server listed as not responding, copy its **서버 설정 스크립트** (server setup script) and run it once in an admin PowerShell on that server. The script turns on WinRM, raises the concurrent session limits, and sets remote admin rights if needed.

## Development

You need Python 3.12, [uv](https://docs.astral.sh/uv/), and Node.js.

```powershell
uv run python -m hq                    # run from source
uv run pytest                          # all Python tests, including integration
uv run pytest -m "not integration"     # fast tests only
node --test "tests/js/*.test.cjs"      # UI logic tests
```

Integration tests start a real `powershell.exe` under ConPTY. They never connect to a real server.

Built with pywebview (WebView2), pywinpty (ConPTY), xterm.js, openpyxl, and PyInstaller.

## Documents

These are written in Korean.

- [Design](docs/specs/2026-10-15-hq-design.md)
- [Acceptance criteria and results](docs/acceptance.md)
- [Verification against a real server](docs/verification.md)

## License

MIT License. See [LICENSE](LICENSE).

xterm.js in `src/hq/static/vendor/xterm/` is under the MIT License.
