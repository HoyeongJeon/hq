/* HQ 화면. hq 본체는 window.pywebview.api 로 부르고, hq가 보내는 출력과 상태는 window.hq 로 받는다.
   사용자 자료(서버 이름, 별명, 메모 등)는 항상 textContent나 텍스트 노드로만 넣는다.
   터미널의 제목 바꾸기(부트스트랩의 준비 신호 등)는 받지 않는다. 탭 제목과 창 제목은 hq가 정한다. */
(function () {
  'use strict';

  const L = window.HQ;
  const $ = (selector) => document.querySelector(selector);
  const USERNAME_HINT = '서버의 로컬 계정. 예) Administrator, opsadmin';
  const TERM_THEME = {
    background: '#0a0d12',
    foreground: '#d6dde8',
    cursor: '#e8eef7',
    cursorAccent: '#0a0d12',
    selectionBackground: '#2a3d5a',
  };

  const state = { servers: [], status: {}, query: '', selected: null, view: 'servers', tabs: [], active: null };
  let api = null;
  let closeCurrentModal = null;

  // ---------- 도우미 ----------
  function h(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child !== null && child !== undefined && child !== false) node.append(child);
    }
    return node;
  }

  function serverById(id) {
    return state.servers.find((s) => s.id === id);
  }

  function tabById(sid) {
    return state.tabs.find((t) => t.sid === sid);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      console.warn('클립보드에 복사하지 못했습니다:', error);
      return false;
    }
  }

  // ---------- 대화 상자 ----------
  function openModal({ title, body, buttons, wide }) {
    if (closeCurrentModal) closeCurrentModal(undefined);
    return new Promise((resolve) => {
      const overlay = $('#modal');
      let primary = null;
      function close(value) {
        overlay.hidden = true;
        overlay.replaceChildren();
        document.removeEventListener('keydown', onKey, true);
        closeCurrentModal = null;
        resolve(value);
      }
      function onKey(event) {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          close(undefined);
        } else if (event.key === 'Enter' && event.target.tagName === 'INPUT' && event.target.type !== 'checkbox' && primary) {
          event.preventDefault();
          primary.click();
        }
      }
      const footer = h('div', { class: 'modal-actions' }, buttons.map((b) => {
        const button = h('button', { class: 'btn' + (b.primary ? ' primary' : '') + (b.danger ? ' danger' : ''), type: 'button', text: b.label });
        button.addEventListener('click', async () => {
          let value = b.value;
          if (b.run) {
            button.disabled = true;
            try {
              value = await b.run();
            } finally {
              button.disabled = false;
            }
            if (value === false) return;
          }
          close(value);
          if (b.after) b.after();
        });
        if (b.primary) primary = button;
        return button;
      }));
      overlay.replaceChildren(h('div', { class: 'modal-card' + (wide ? ' wide' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
        h('h2', { text: title }), body, footer));
      overlay.hidden = false;
      closeCurrentModal = close;
      document.addEventListener('keydown', onKey, true);
      const focusTarget = overlay.querySelector('input:not([type=checkbox]), textarea') || primary;
      if (focusTarget) focusTarget.focus();
    });
  }

  function confirmDialog({ title, message, ok, danger }) {
    return openModal({
      title,
      body: h('p', { class: 'msg', text: message }),
      buttons: [{ label: '취소', value: false }, { label: ok, value: true, primary: true, danger }],
    }).then((value) => value === true);
  }

  function messageDialog(title, message, extra = []) {
    return openModal({
      title,
      body: h('p', { class: 'msg', text: message }),
      buttons: [...extra.map((b) => ({ label: b.label, after: b.run })), { label: '닫기', primary: true }],
    });
  }

  function field(id, label, input, hint) {
    return h('label', { class: 'field', for: id }, h('span', { class: 'field-label', text: label }), input,
      hint ? h('span', { class: 'hint', text: hint }) : null);
  }

  // ---------- hq → 화면 ----------
  // 닫은 탭이나 모르는 세션 id로 온 출력·끝남 알림은 버린다(hq는 닫힌 탭의 끝남도 알릴 수 있다)
  window.hq = {
    onOutput(batch) {
      for (const sid of Object.keys(batch)) {
        const tab = tabById(sid);
        if (!tab || !tab.term) continue;
        tab.term.write(batch[sid]);
        if (sid !== state.active && !tab.unread) {
          tab.unread = true;
          renderTabs();
        }
      }
    },
    onSessionEnded(sid, reason) {
      const tab = tabById(sid);
      if (tab) {
        tab.ends += 1;
        setEnded(tab, reason);
      }
    },
    onCloseRequested(count) {
      confirmDialog({ title: 'HQ 끝내기', message: `열린 탭 ${count}개를 닫고 끝낼까요?`, ok: '끝내기', danger: true })
        .then((yes) => { if (yes) api.quit(); });
    },
  };

  // ---------- 서버 목록 ----------
  async function loadServers() {
    const result = await api.list_servers();
    const box = $('#load-error');
    if (result.ok) {
      box.hidden = true;
      state.servers = result.servers;
    } else {
      box.textContent = result.error;
      box.hidden = false;
      state.servers = [];
    }
    renderServers();
    for (const tab of state.tabs) renderTabInfo(tab);
  }

  async function refreshStatus() {
    const button = $('#refresh');
    button.disabled = true;
    try {
      const result = await api.probe();
      if (result.ok) state.status = result.status;
    } finally {
      button.disabled = false;
    }
    renderServers();
  }

  async function refreshTrust() {
    const result = await api.missing_trusted_hosts();
    const banner = $('#banner');
    const missing = result.ok && result.readable ? result.missing : [];
    if (!missing.length) {
      banner.hidden = true;
      banner.replaceChildren();
      return;
    }
    banner.replaceChildren(
      h('span', { text: `초기 설정 필요: 이 PC의 TrustedHosts에 서버 ${missing.length}대가 없습니다.` }),
      h('button', { class: 'btn', type: 'button', text: '설정하기', onclick: trustNow }));
    banner.hidden = false;
  }

  async function trustNow() {
    const result = await api.add_trusted_hosts();
    await refreshTrust();
    if (state.view === 'settings') renderSettings();
    messageDialog('초기 설정', result.ok ? 'TrustedHosts에 서버를 추가했습니다.' : result.error);
  }

  function renderServers() {
    const list = $('#server-list');
    list.replaceChildren();
    const groups = L.groupServers(L.filterServers(state.servers, state.query));
    if (!groups.length) {
      const text = state.servers.length ? '검색 결과가 없습니다.' : '등록된 서버가 없습니다. [+ 서버 추가]나 [엑셀 등록]으로 시작하세요.';
      list.append(h('p', { class: 'muted', style: 'padding: 12px 8px', text }));
      return;
    }
    for (const group of groups) {
      list.append(h('div', { class: 'group', text: group.name }));
      for (const server of group.servers) {
        const status = L.statusView(state.status[server.id]);
        list.append(h('div', {
          class: 'srv' + (state.selected === server.id ? ' selected' : ''),
          role: 'listitem',
          tabindex: '0',
          onclick: () => { state.selected = server.id; renderServers(); showInfo(server.id); },
          ondblclick: () => openTab(server),
          oncontextmenu: (event) => { event.preventDefault(); contextMenu(event, server); },
          onkeydown: (event) => { if (event.key === 'Enter') openTab(server); },
        },
        h('span', { class: 'srv-icon', 'aria-hidden': 'true' }),
        h('span', { class: 'srv-text' }, h('span', { class: 'srv-name', text: L.displayName(server) }), h('span', { class: 'srv-host', text: server.host })),
        h('span', { class: 'srv-status ' + status.cls }, h('span', { class: 'dot' }), status.label)));
      }
    }
  }

  function showInfo(id) {
    const server = serverById(id);
    if (!server) return;
    const rows = [
      ['이름', server.name],
      ['별명', server.nickname || '—'],
      ['IP/호스트', server.host],
      ['그룹', server.group || L.UNGROUPED],
      ['계정', server.username],
      ['비밀번호', server.has_password ? '저장됨 (다시 볼 수 없음)' : '없음'],
      ['상태', L.statusView(state.status[server.id]).label],
    ];
    const panel = $('#info');
    panel.replaceChildren(
      h('header', { class: 'info-head' }, h('h2', { text: L.displayName(server) }),
        h('button', { class: 'icon-btn', type: 'button', title: '닫기 (Esc)', 'aria-label': '닫기', text: '×', onclick: closeInfo })),
      h('dl', { class: 'info-list' }, rows.map(([key, value]) => [h('dt', { text: key }), h('dd', { text: value })])),
      h('h3', { text: '메모' }),
      h('pre', { class: 'memo-box', text: server.memo || '메모 없음' }),
      h('div', { class: 'info-actions' },
        h('button', { class: 'btn primary', type: 'button', text: '접속', onclick: () => openTab(server) }),
        h('button', { class: 'btn', type: 'button', text: '정보 수정', onclick: () => serverDialog(server) }),
        h('button', { class: 'btn', type: 'button', text: '비밀번호 변경', onclick: () => passwordDialog(server, '새 비밀번호로 덮어씁니다.') }),
        h('button', { class: 'btn', type: 'button', text: '서버 설정 스크립트', onclick: () => setupScriptDialog(server) }),
        h('button', { class: 'btn danger', type: 'button', text: '삭제', onclick: () => deleteServer(server) })));
    panel.hidden = false;
  }

  function closeInfo() {
    $('#info').hidden = true;
  }

  function contextMenu(event, server) {
    const menu = $('#menu');
    const item = (label, run, danger) => h('button', {
      class: 'menu-item' + (danger ? ' danger' : ''), type: 'button', role: 'menuitem', text: label,
      onclick: () => { hideMenu(); run(); },
    });
    menu.replaceChildren(
      item('접속', () => openTab(server)),
      item('정보 수정', () => serverDialog(server)),
      item('비밀번호 변경', () => passwordDialog(server, '새 비밀번호로 덮어씁니다.')),
      item('삭제', () => deleteServer(server), true));
    menu.style.left = Math.min(event.clientX, window.innerWidth - 180) + 'px';
    menu.style.top = Math.min(event.clientY, window.innerHeight - 170) + 'px';
    menu.hidden = false;
  }

  function hideMenu() {
    $('#menu').hidden = true;
  }

  async function serverDialog(server) {
    const editing = Boolean(server);
    const s = server || {};
    const inputs = {
      name: h('input', { id: 'f-name', value: s.name || '', maxlength: '100' }),
      nickname: h('input', { id: 'f-nickname', value: s.nickname || '', maxlength: '50' }),
      host: h('input', { id: 'f-host', value: s.host || '', maxlength: '253', placeholder: '10.0.1.21' }),
      group: h('input', { id: 'f-group', value: s.group || '', maxlength: '50' }),
      username: h('input', { id: 'f-username', value: s.username || '', maxlength: '130', placeholder: 'Administrator' }),
      memo: h('textarea', { id: 'f-memo', rows: '4', maxlength: '4000' }),
    };
    inputs.memo.value = s.memo || '';
    const password = editing ? null : h('input', { id: 'f-password', type: 'password', autocomplete: 'off' });
    const error = h('p', { class: 'form-error', role: 'alert' });
    const body = h('div', { class: 'form' },
      field('f-name', '이름 *', inputs.name),
      field('f-nickname', '별명', inputs.nickname, '비우면 이름을 보여 줍니다.'),
      field('f-host', 'IP/호스트 *', inputs.host),
      field('f-group', '그룹', inputs.group),
      field('f-username', 'ID *', inputs.username, USERNAME_HINT),
      password ? field('f-password', 'PW *', password, '저장한 뒤에는 다시 볼 수 없습니다.') : null,
      field('f-memo', '메모', inputs.memo),
      error);
    const saved = await openModal({
      title: editing ? '정보 수정' : '서버 추가',
      body,
      buttons: [{ label: '취소' }, {
        label: '저장', primary: true, run: async () => {
          const info = Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value]));
          const result = editing ? await api.update_server(s.id, info) : await api.add_server(info, password.value);
          if (!result.ok) {
            error.textContent = result.error;
            return false;
          }
          return result.server;
        },
      }],
    });
    if (!saved) return;
    await loadServers();
    refreshStatus();
    refreshTrust();
    state.selected = saved.id;
    showInfo(saved.id);
  }

  async function passwordDialog(server, message) {
    const input = h('input', { id: 'f-pw', type: 'password', autocomplete: 'off' });
    const error = h('p', { class: 'form-error', role: 'alert' });
    const body = h('div', { class: 'form' },
      h('p', { class: 'msg', text: `${L.displayName(server)} (${server.username}@${server.host})` }),
      h('p', { class: 'muted', style: 'margin: 0', text: message }),
      field('f-pw', '비밀번호', input),
      error);
    const ok = await openModal({
      title: '비밀번호',
      body,
      buttons: [{ label: '취소' }, {
        label: '저장', primary: true, run: async () => {
          const result = await api.set_password(server.id, input.value);
          if (!result.ok) {
            error.textContent = result.error;
            return false;
          }
          return true;
        },
      }],
    });
    if (ok === true) await loadServers();
    return ok === true;
  }

  async function deleteServer(server) {
    const open = state.tabs.filter((t) => t.serverId === server.id).length;
    const message = `${L.displayName(server)}(${server.host})을(를) 지웁니다. 저장된 비밀번호도 지워집니다.`
      + (open ? `\n열린 탭 ${open}개도 닫힙니다.` : '');
    if (!(await confirmDialog({ title: '서버 삭제', message, ok: '삭제', danger: true }))) return;
    const result = await api.delete_server(server.id);
    if (!result.ok) {
      messageDialog('삭제하지 못했습니다', result.error);
      return;
    }
    for (const tab of state.tabs.filter((t) => t.serverId === server.id)) removeTab(tab.sid);
    closeInfo();
    state.selected = null;
    await loadServers();
  }

  async function excelStart() {
    const choice = await openModal({
      title: '엑셀 등록',
      body: h('p', { class: 'msg', text: '양식에 서버 정보를 채운 뒤 파일을 고르면, 등록하기 전에 미리보기를 보여 줍니다.' }),
      buttons: [{ label: '취소' }, { label: '양식 저장', value: 'template' }, { label: '파일 고르기', value: 'pick', primary: true }],
    });
    if (choice === 'template') {
      const result = await api.save_excel_template();
      if (result.ok) messageDialog('양식을 저장했습니다', result.path);
      else if (!result.cancelled) messageDialog('저장하지 못했습니다', result.error);
    } else if (choice === 'pick') {
      excelPreview();
    }
  }

  async function excelPreview() {
    const preview = await api.preview_excel();
    if (!preview.ok) {
      if (!preview.cancelled) messageDialog('엑셀을 읽을 수 없습니다', preview.error);
      return;
    }
    const overwrite = h('input', { id: 'x-overwrite', type: 'checkbox' });
    const statusLabel = { new: '신규', existing: '이미 등록됨', error: '오류' };
    const table = h('table', { class: 'grid' },
      h('thead', {}, h('tr', {}, ['행', '상태', '이름', 'IP/호스트', '그룹', 'ID', '비고'].map((t) => h('th', { text: t })))),
      h('tbody', {}, preview.rows.map((row) => h('tr', { class: 'st-' + row.status },
        h('td', { text: String(row.row) }), h('td', { text: statusLabel[row.status] }), h('td', { text: row.name }),
        h('td', { text: row.host }), h('td', { text: row.group }), h('td', { text: row.username }), h('td', { text: row.error })))));
    const counts = preview.counts;
    const body = h('div', { class: 'form' },
      h('p', { class: 'msg', text: `신규 ${counts.new} · 이미 등록됨 ${counts.existing} · 오류 ${counts.error} (오류 행은 등록하지 않습니다)` }),
      h('div', { class: 'grid-wrap' }, table),
      h('label', { class: 'check', for: 'x-overwrite' }, overwrite, h('span', { text: '이미 등록된 서버는 이 파일의 값으로 덮어쓰기 (비밀번호 포함)' })));
    const result = await openModal({
      title: '엑셀 등록 미리보기', wide: true, body,
      buttons: [{ label: '취소' }, { label: '등록', primary: true, run: () => api.apply_excel(preview.preview_id, overwrite.checked) }],
    });
    if (!result) return;
    if (!result.ok) {
      messageDialog('등록하지 못했습니다', result.error);
      return;
    }
    await loadServers();
    refreshStatus();
    refreshTrust();
    messageDialog('등록했습니다', `추가 ${result.added} · 덮어씀 ${result.updated} · 건너뜀 ${result.skipped} · 오류 ${result.errors}`
      + '\n\n엑셀 파일에 비밀번호가 평문으로 들어 있습니다. 지금 지우세요.');
  }

  async function setupScriptDialog(server) {
    const restrict = h('input', { id: 's-restrict', type: 'checkbox' });
    const pre = h('pre', { class: 'script' });
    const note = h('p', { class: 'form-error', role: 'status' });
    async function load() {
      const result = await api.server_setup_script(server.id, restrict.checked);
      if (result.ok) {
        pre.textContent = result.script;
        note.className = 'form-error';
        note.textContent = '';
      } else {
        note.className = 'form-error';
        note.textContent = result.error;
        restrict.checked = false;
      }
    }
    restrict.addEventListener('change', load);
    await load();
    const settings = await api.get_settings();
    const ips = settings.ok ? settings.admin_ips : [];
    const body = h('div', { class: 'form' },
      h('p', { class: 'msg', text: `${L.displayName(server)} (${server.host})에서 관리자 권한 PowerShell을 열고 아래 내용을 붙여넣으세요. `
        + '이미 접속되는 서버라면 hq 탭에서 실행해도 됩니다. 이때 스크립트는 WinRM을 다시 시작하지 않습니다. '
        + '다시 시작이 필요하면 서버 콘솔(RDP)에서 하세요.' }),
      h('div', { class: 'field' },
        h('label', { class: 'check', for: 's-restrict' }, restrict,
          h('span', { text: '5985를 관리자 PC IP에서만 받기 (WinRM을 쓰는 다른 시스템이 없는지 먼저 확인하세요)' })),
        h('div', { class: 'check-hints' },
          h('span', { class: 'hint', text: ips.length ? `허용할 IP: ${ips.join(', ')}` : '허용할 IP: 없음 (설정 화면에서 관리자 PC IP를 먼저 저장하세요)' }),
          h('span', { class: 'hint warn', text: '이 PC의 IP가 목록에 없으면 이 PC에서도 접속이 막힙니다.' }))),
      note,
      pre);
    await openModal({
      title: '서버 설정 스크립트', wide: true, body,
      buttons: [{ label: '닫기' }, {
        label: '복사', primary: true, run: async () => {
          const copied = await copyText(pre.textContent);
          note.className = copied ? 'form-ok' : 'form-error';
          note.textContent = copied ? '복사했습니다.' : '복사하지 못했습니다. 내용을 직접 선택해 복사하세요.';
          return false;
        },
      }],
    });
  }

  // ---------- 설정 화면 ----------
  function setView(view) {
    state.view = view;
    $('#servers-view').hidden = view !== 'servers';
    $('#settings-view').hidden = view !== 'settings';
    $('#rail-servers').classList.toggle('active', view === 'servers');
    $('#rail-settings').classList.toggle('active', view === 'settings');
    if (view === 'settings') renderSettings();
  }

  async function renderSettings() {
    const settings = await api.get_settings();
    const trust = await api.missing_trusted_hosts();
    const ips = h('textarea', { id: 'set-ips', rows: '3', placeholder: '10.0.0.50\n10.0.0.51', 'aria-label': '관리자 PC IP' });
    ips.value = (settings.admin_ips || []).join('\n');
    const message = h('p', { class: 'form-error', role: 'status' });
    let trustText = '등록된 서버가 모두 TrustedHosts에 있습니다.';
    if (!trust.readable) trustText = '이 PC의 TrustedHosts를 읽을 수 없습니다. 접속할 때 확인합니다.';
    else if (trust.missing.length) trustText = `TrustedHosts에 없는 서버 ${trust.missing.length}대: ${trust.missing.join(', ')}`;
    $('#settings-body').replaceChildren(
      h('h3', { text: '관리자 PC IP' }),
      h('p', { class: 'muted', style: 'margin: 0', text: '서버 설정 스크립트에서 5985를 이 IP에서만 받게 할 때 씁니다. 한 줄에 하나씩 적습니다.' }),
      ips,
      h('div', { class: 'row' }, h('button', {
        class: 'btn', type: 'button', text: '저장', onclick: async () => {
          const result = await api.save_settings(ips.value.split(/\s+/).filter(Boolean));
          message.className = result.ok ? 'form-ok' : 'form-error';
          message.textContent = result.ok ? '저장했습니다.' : result.error;
        },
      })),
      message,
      h('h3', { text: '내 PC 초기 설정 (TrustedHosts)' }),
      h('p', { class: 'muted', style: 'margin: 0', text: trustText }),
      !trust.readable || trust.missing.length
        ? h('div', { class: 'row' }, h('button', { class: 'btn primary', type: 'button', text: '설정하기 (관리자 권한)', onclick: trustNow }))
        : null);
  }

  // ---------- 탭과 터미널 ----------
  function openTab(server) {
    closeInfo();
    const used = state.tabs.filter((t) => t.serverId === server.id).map((t) => t.ordinal);
    const ordinal = L.nextOrdinal(used);
    const tab = {
      sid: L.newId(), serverId: server.id, ordinal, title: L.tabTitle(server, ordinal),
      ended: null, ends: 0, starting: false, unread: false, memoOpen: false, size: null, term: null,
    };
    buildPane(tab);
    state.tabs.push(tab);
    activate(tab.sid);
    createTerminal(tab); // 보이는 상태에서 열어야 글자 크기를 제대로 잰다
    startSession(tab, 'open');
  }

  function buildPane(tab) {
    tab.dot = h('span', { class: 'dot run' });
    tab.nameText = h('span', { class: 'status-name' });
    tab.hostText = h('span');
    tab.stateText = h('span', { text: '실행 중' });
    tab.memoLine = h('button', { class: 'memo-line', type: 'button', onclick: () => { tab.memoOpen = !tab.memoOpen; renderTabInfo(tab); } });
    tab.memoFull = h('pre', { class: 'memo-full', hidden: true });
    tab.termHost = h('div', { class: 'term-host' });
    tab.overlay = h('div', { class: 'ended', hidden: true });
    tab.pane = h('section', { class: 'pane', role: 'tabpanel', hidden: true },
      h('div', { class: 'status-line' }, tab.dot, tab.nameText, h('span', { text: '·' }), tab.hostText, h('span', { text: '·' }), tab.stateText),
      tab.memoLine,
      tab.memoFull,
      h('div', { class: 'term-wrap' }, tab.termHost, tab.overlay));
    $('#panes').append(tab.pane);
    renderTabInfo(tab);
  }

  function renderTabInfo(tab) {
    const server = serverById(tab.serverId);
    if (!server) return;
    const memo = server.memo || '';
    tab.nameText.textContent = L.displayName(server);
    tab.hostText.textContent = server.host;
    tab.memoLine.hidden = !memo;
    tab.memoLine.textContent = (tab.memoOpen ? '▾ ' : '▸ ') + memo.split('\n')[0];
    tab.memoFull.textContent = memo;
    tab.memoFull.hidden = !(tab.memoOpen && memo);
  }

  function createTerminal(tab) {
    tab.term = new Terminal({
      fontFamily: '"Cascadia Mono", Consolas, "Malgun Gothic", monospace',
      fontSize: 14,
      lineHeight: 1.15,
      cursorBlink: true,
      scrollback: 10000,
      theme: TERM_THEME,
    });
    tab.fit = new FitAddon.FitAddon();
    tab.term.loadAddon(tab.fit);
    tab.term.open(tab.termHost);
    // 키 입력, 터미널 응답, 확인을 받은 붙여넣기(term.paste)가 모두 onData로 와서 이 한 줄을 탄다
    tab.input = L.createInputQueue((data) => api.send_input(tab.sid, data));
    tab.term.onData((data) => { if (!tab.ended) tab.input.push(data); });
    tab.term.attachCustomKeyEventHandler((event) => terminalKey(tab, event));
    tab.termHost.addEventListener('paste', (event) => onPaste(tab, event), true);
    tab.observer = new ResizeObserver(() => fitTab(tab));
    tab.observer.observe(tab.termHost);
    fitTab(tab);
  }

  function fitTab(tab) {
    if (!tab.term || tab.pane.hidden) return;
    try {
      tab.fit.fit();
    } catch (error) {
      console.warn('터미널 크기를 맞추지 못했습니다:', error);
      return;
    }
    const size = `${tab.term.cols}x${tab.term.rows}`;
    if (size !== tab.size) {
      tab.size = size;
      if (!tab.ended) api.resize(tab.sid, tab.term.cols, tab.term.rows);
    }
  }

  async function startSession(tab, mode) {
    if (tab.starting) return; // [다시 연결]을 거듭 눌러도 프로세스는 한 번만 띄운다
    tab.starting = true;
    const ends = tab.ends;
    let result;
    try {
      fitTab(tab);
      const cols = tab.term.cols;
      const rows = tab.term.rows;
      result = mode === 'open'
        ? await api.open_session(tab.sid, tab.serverId, cols, rows)
        : await api.reconnect(tab.sid, cols, rows);
    } finally {
      tab.starting = false;
    }
    // 기다리는 사이에 탭을 닫았다(여는 중에 닫혔으면 hq가 cancelled로 알린다). 알릴 것이 없다
    if (result.cancelled || !state.tabs.includes(tab)) return;
    if (result.ok) {
      // 기다리는 사이에 새 프로세스의 끝남이 먼저 왔으면 그 끝남을 '실행 중'으로 덮지 않는다
      if (tab.ends === ends) setEnded(tab, null);
      tab.term.focus();
      return;
    }
    const server = serverById(tab.serverId);
    if (mode === 'open') removeTab(tab.sid); // 프로세스를 띄우지 않았으므로 탭을 닫는다
    if (!server) {
      messageDialog('접속할 수 없음', result.error);
      return;
    }
    const retry = () => (mode === 'open' ? openTab(server) : startSession(tab, 'reconnect'));
    if (result.need === 'password') {
      if (await passwordDialog(server, '저장된 비밀번호가 없습니다. 입력하면 바로 접속합니다.')) retry();
    } else if (result.need === 'trustedhosts') {
      messageDialog('초기 설정 필요', `${result.error}\n관리자 권한으로 이 PC의 TrustedHosts에 서버를 추가합니다.`, [{ label: '설정하기', run: trustNow }]);
    } else if (result.need === 'unreachable') {
      messageDialog('서버 응답 없음', `${result.error}\nWinRM이 꺼져 있거나 방화벽이 5985를 막고 있을 수 있습니다.`,
        [{ label: '서버 설정 스크립트', run: () => setupScriptDialog(server) }]);
    } else {
      messageDialog('접속할 수 없음', result.error || '알 수 없는 오류');
    }
  }

  function setEnded(tab, reason) {
    tab.ended = reason;
    tab.term.options.disableStdin = Boolean(reason);
    if (!reason) {
      tab.dot.className = 'dot run';
      tab.stateText.textContent = '실행 중';
      tab.overlay.hidden = true;
      tab.overlay.replaceChildren();
      renderTabs();
      return;
    }
    const view = L.endedView(reason);
    tab.dot.className = 'dot end';
    tab.stateText.textContent = '끝남: ' + view.short;
    tab.overlay.replaceChildren(h('div', { class: 'ended-card', role: 'alert' },
      h('p', { text: view.text }),
      h('div', { class: 'row' }, view.actions.map((action) => h('button', {
        class: 'btn', type: 'button', text: action.label, onclick: () => endedAction(tab, action.id),
      })))));
    tab.overlay.hidden = false;
    renderTabs();
  }

  async function endedAction(tab, id) {
    const server = serverById(tab.serverId);
    if (!server) {
      messageDialog('서버 없음', '이 서버는 목록에서 지워졌습니다.');
      return;
    }
    if (id === 'reconnect' || id === 'retry') startSession(tab, 'reconnect');
    else if (id === 'password') {
      if (await passwordDialog(server, '새 비밀번호를 저장하고 다시 연결합니다.')) startSession(tab, 'reconnect');
    } else if (id === 'setup') setupScriptDialog(server);
    else if (id === 'trust') trustNow();
  }

  function activate(sid) {
    state.active = sid;
    for (const tab of state.tabs) tab.pane.hidden = tab.sid !== sid;
    const tab = tabById(sid);
    if (tab) {
      tab.unread = false;
      requestAnimationFrame(() => {
        fitTab(tab);
        if (tab.term) tab.term.focus();
      });
    }
    renderTabs();
  }

  function renderTabs() {
    const bar = $('#tabbar');
    bar.replaceChildren(...state.tabs.map((tab) => h('div', {
      class: 'tab' + (tab.sid === state.active ? ' active' : ''),
      role: 'tab',
      'aria-selected': tab.sid === state.active ? 'true' : 'false',
      onclick: () => activate(tab.sid),
    },
    h('span', { class: 'dot ' + (tab.ended ? 'end' : 'run') }),
    h('span', { class: 'tab-title', text: tab.title }),
    tab.unread ? h('span', { class: 'unread', title: '새 출력' }) : null,
    h('button', {
      class: 'tab-close', type: 'button', title: '탭 닫기', 'aria-label': '탭 닫기', text: '×',
      onclick: (event) => { event.stopPropagation(); closeTab(tab.sid); },
    }))));
    $('#empty').hidden = state.tabs.length > 0;
  }

  function removeTab(sid) {
    const index = state.tabs.findIndex((t) => t.sid === sid);
    if (index < 0) return;
    const [tab] = state.tabs.splice(index, 1);
    if (tab.observer) tab.observer.disconnect();
    if (tab.term) disposeLater(tab.term);
    tab.pane.remove();
    if (state.active === sid) {
      state.active = null;
      const next = state.tabs[index] || state.tabs[index - 1];
      if (next) activate(next.sid);
    }
    renderTabs();
  }

  function disposeLater(term) {
    // xterm 5.5.0은 open() 때 건 화면 갱신(requestAnimationFrame)을 dispose()에서 취소하지 않는다. 연 프레임 안에 버리면
    // (예: 비밀번호가 없어 바로 실패한 탭) 그 갱신이 버린 렌더러를 읽다 TypeError를 낸다. 갱신은 두 프레임 안에 끝난다
    requestAnimationFrame(() => requestAnimationFrame(() => term.dispose()));
  }

  function closeTab(sid) {
    removeTab(sid);
    api.close_session(sid);
  }

  function cycleTab(step) {
    if (!state.tabs.length) return;
    const index = state.tabs.findIndex((t) => t.sid === state.active);
    activate(state.tabs[(index + step + state.tabs.length) % state.tabs.length].sid);
  }

  function terminalKey(tab, event) {
    if (event.type !== 'keydown') return true;
    const key = (event.key || '').toLowerCase();
    if (event.ctrlKey && !event.altKey && !event.shiftKey && key === 'c' && tab.term.hasSelection()) {
      copyText(tab.term.getSelection());
      tab.term.clearSelection();
      event.preventDefault();
      return false;
    }
    if (event.ctrlKey && !event.altKey && key === 'v') return false; // 브라우저 붙여넣기로 넘겨 paste 이벤트에서 처리한다
    return true;
  }

  function onPaste(tab, event) {
    const text = event.clipboardData ? event.clipboardData.getData('text') : '';
    if (!L.needsPasteConfirm(text)) return; // 한 줄은 xterm이 그대로 붙여넣는다
    event.preventDefault();
    event.stopImmediatePropagation();
    const server = serverById(tab.serverId) || { name: '?', host: '?' };
    confirmDialog({
      title: '여러 줄 붙여넣기',
      message: `${L.displayName(server)}(${server.host})에 ${L.pastedLineCount(text)}줄을 실행합니다.`,
      ok: '실행',
    }).then((yes) => {
      // term.paste는 onData로 이어져 키 입력과 같은 입력 줄(tab.input)을 탄다. 따로 보내면 순서가 뒤섞인다
      if (yes && !tab.ended) tab.term.paste(text);
      tab.term.focus();
    });
  }

  // ---------- 전체 단축키와 시작 ----------
  document.addEventListener('keydown', (event) => {
    const shortcut = L.shortcutFor(event);
    if (shortcut && shortcut.type === 'block') {
      event.preventDefault(); // 새로고침하면 화면이 초기화되므로 막는다
      return;
    }
    if (!$('#modal').hidden) return;
    if (!shortcut) {
      if (event.key === 'Escape') {
        closeInfo();
        hideMenu();
      }
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (shortcut.type === 'next') cycleTab(1);
    else if (shortcut.type === 'prev') cycleTab(-1);
    else if (shortcut.type === 'index') {
      const tab = state.tabs[shortcut.index];
      if (tab) activate(tab.sid);
    } else if (shortcut.type === 'search') {
      setView('servers');
      $('#search').focus();
      $('#search').select();
    }
  }, true);
  document.addEventListener('click', hideMenu);
  document.addEventListener('contextmenu', (event) => {
    if (!event.target.closest('.srv')) {
      event.preventDefault();
      hideMenu();
    }
  });

  function bindChrome() {
    $('#rail-servers').addEventListener('click', () => setView('servers'));
    $('#rail-settings').addEventListener('click', () => setView('settings'));
    $('#refresh').addEventListener('click', refreshStatus);
    $('#add-server').addEventListener('click', () => serverDialog(null));
    $('#excel').addEventListener('click', excelStart);
    $('#search').addEventListener('input', (event) => {
      state.query = event.target.value;
      renderServers();
    });
    window.addEventListener('resize', () => {
      const tab = tabById(state.active);
      if (tab) fitTab(tab);
    });
  }

  function ready(start) {
    if (window.pywebview && window.pywebview.api) start();
    else window.addEventListener('pywebviewready', start, { once: true });
  }

  ready(async () => {
    api = window.pywebview.api;
    bindChrome();
    await loadServers();
    refreshStatus();
    refreshTrust();
  });
})();
