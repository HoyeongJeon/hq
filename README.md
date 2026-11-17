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

## Claude Code (MCP)

`python -m hq.mcp` is an MCP server for Claude Code. Claude Code starts it as a child process and talks to it over standard input and output. It uses HQ's server list and stored passwords directly, so the HQ window does not need to be open, and it opens no network port. It offers three tools:

- `list_servers`: the servers you turned on for it, with name, nickname, host, group, memo and whether a password is stored. Never the account name, the server id or the password file.
- `run_query`: runs a script on those servers without asking you, but only if a check on this PC parses it and finds nothing but allowed read commands and syntax. Anything else is refused.
- `run_script`: runs any script on those servers. Claude Code asks you before every call.

Every server is off until you turn it on by host. The MCP server does not connect to an address you forbid. A host written as a name is resolved and compared with the forbidden list just before each call, so a DNS change after that comparison can slip through. The forbidden list applies to the MCP server only: HQ's own window still checks the port of every server in its list, and a script running on another server can still reach the address.

```powershell
uv run --project C:\dev\hq python -m hq.mcp list               # every server and its state; no network
uv run --project C:\dev\hq python -m hq.mcp on <host>...       # or: on --group <group>, on --all
uv run --project C:\dev\hq python -m hq.mcp off <host>...
uv run --project C:\dev\hq python -m hq.mcp never <IP>...      # to undo, edit mcp-access.json by hand
```

**Register it only in `C:\dev\hq-ops`.** A Claude Code session in `C:\dev\hq` is for working on HQ itself and must not be able to reach real servers, so the server is registered with local scope in a separate folder, never with user scope. It runs from the current files in `C:\dev\hq`. Its Python code is loaded when a session in `C:\dev\hq-ops` starts, but the query rules and PowerShell templates (`query_rules.json`, `mcp-check.ps1`, `mcp-run.ps1`, `bootstrap.ps1`) are read again on every call, so an edit to them takes effect at once, even in a session that is already running; only the list of allowed commands in the tool description stays as it was read at start. That includes changes made by a development session in `C:\dev\hq`, which can change the query check itself. The paths below assume the repository is at `C:\dev\hq`.

Install (section 13.4 of the [MCP design](docs/specs/2026-11-04-hq-mcp-design.md)):

1. Forbid the servers it must never touch: `uv run --project C:\dev\hq python -m hq.mcp never <IP>`. Type the address yourself; do not give it to Claude.
2. Turn on the servers it may use: `uv run --project C:\dev\hq python -m hq.mcp on <host>...`
3. Check that `permissions.allow` in your user settings (`~/.claude/settings.json`) has no broad rule for Bash, PowerShell, web or connector tools. Allow rules from all settings files add up, so such a rule would let those tools run without asking in `C:\dev\hq-ops` too.
4. Register it with the permission settings of section 13.2:

   ```powershell
   New-Item -ItemType Directory -Force C:\dev\hq-ops\.claude | Out-Null
   Copy-Item C:\dev\hq\tools\mcp\settings.local.json C:\dev\hq-ops\.claude\settings.local.json
   cd C:\dev\hq-ops
   claude mcp add --scope local --transport stdio hq -- uv run --project C:\dev\hq python -m hq.mcp
   ```

5. Start Claude Code in `C:\dev\hq-ops`.

**What goes to Claude.** Server names, nicknames, hosts, groups and memos, every script, and what the servers print are sent to Claude (Anthropic) and kept according to your account's data settings. Remove passwords or account details from a server's memo before you turn it on.

**What stays on this PC.** The record is `%LOCALAPPDATA%\hq\logs\mcp-audit-YYYY-MM.jsonl`, one JSON line per event. Every script that is sent to a server is recorded in full before it runs, in the start line of its call (time, call id, targets, the whole script, its SHA-256, the reason given); if that line cannot be written, nothing runs. An end line follows with the status, exit code, time and output length per server. Scripts refused by the checks are recorded with the reason when the record can be written. Calls refused for wrong arguments, an unknown target or a problem with the settings or the server list are not recorded. Server output is not recorded, but a password written into a script is, in plain text. The file is never deleted automatically. The server's own log is `%LOCALAPPDATA%\hq\logs\hq-mcp.log`.

**This is not a security boundary.** Claude Code runs under your Windows account, the same one as HQ, so it can decrypt the password files and edit the list of allowed servers with its other tools. The query check and the confirmation prompt reduce accidents in the scripts Claude normally writes; a deliberately crafted script, such as one suggested by text planted in a server's output, can get past the check. The permission settings in `C:\dev\hq-ops` make Claude Code ask before most of its own shell commands and keep its file tools inside that folder, but they match text and live in files your account can change. Section 12 of the MCP design lists what is and is not covered.

## Security

- **Passwords stay on this PC.** They are encrypted with Windows DPAPI (current user) and stored under `%LOCALAPPDATA%\hq\`. There is no sharing or sync.
- **HQ opens no network port.** The window (WebView2) and the app talk only through function calls inside one process.
- **Input meant for a server never runs on this PC.** When a remote session drops, PowerShell normally falls back to this PC's prompt, and anything typed ahead could run locally at that moment. HQ closes that path by ending the tab's PowerShell as soon as the session drops. A tab receives no input until it signals that this safeguard is in place.
- **`hq.log` never holds what you type or what a server prints,** and never a password. The MCP server's script record is a separate file (see above).
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
- [MCP server design](docs/specs/2026-11-04-hq-mcp-design.md)
- [MCP manual checks and results](docs/mcp-acceptance.md)

## License

MIT License. See [LICENSE](LICENSE).

xterm.js in `src/hq/static/vendor/xterm/` is under the MIT License.
