/* 화면의 순수 로직. DOM을 건드리지 않는다.
   브라우저에서는 window.HQ 로, node 테스트에서는 require() 로 쓴다(file:// 에서는 ES 모듈을 쓸 수 없다). */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HQ = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const UNGROUPED = '미분류';

  function displayName(server) {
    return server.nickname || server.name;
  }

  function filterServers(servers, query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) return servers.slice();
    return servers.filter((s) => [s.name, s.nickname, s.host, s.memo].some((v) => (v || '').toLowerCase().includes(q)));
  }

  function groupServers(servers) {
    const groups = new Map();
    for (const server of servers) {
      const name = server.group || UNGROUPED;
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(server);
    }
    const names = [...groups.keys()].sort((a, b) => {
      if (a === UNGROUPED) return 1;
      if (b === UNGROUPED) return -1;
      return a.localeCompare(b, 'ko');
    });
    return names.map((name) => ({
      name,
      servers: groups.get(name).slice().sort((a, b) => displayName(a).localeCompare(displayName(b), 'ko')),
    }));
  }

  function nextOrdinal(used) {
    let ordinal = 1;
    while (used.includes(ordinal)) ordinal += 1;
    return ordinal;
  }

  function tabTitle(server, ordinal) {
    return ordinal <= 1 ? displayName(server) : `${displayName(server)} (${ordinal})`;
  }

  function pastedLineCount(text) {
    return (text || '').split(/\r\n|\r|\n/).filter((line) => line.trim() !== '').length;
  }

  function needsPasteConfirm(text) {
    return pastedLineCount(text) > 1;
  }

  function shortcutFor(e) {
    const key = (e.key || '').toLowerCase();
    if (e.key === 'F5' || (e.ctrlKey && !e.altKey && key === 'r')) return { type: 'block' };
    if (e.ctrlKey && e.key === 'Tab') return { type: e.shiftKey ? 'prev' : 'next' };
    if (e.ctrlKey && !e.shiftKey && !e.altKey && key === 'k') return { type: 'search' };
    if (e.altKey && !e.ctrlKey && /^Digit[1-9]$/.test(e.code || '')) return { type: 'index', index: Number(e.code.slice(5)) - 1 };
    return null;
  }

  function statusView(online) {
    if (online === true) return { label: '온라인', cls: 'on' };
    if (online === false) return { label: '응답 없음', cls: 'off' };
    return { label: '확인 전', cls: 'unknown' };
  }

  const ACTION_LABELS = {
    reconnect: '다시 연결',
    retry: '다시 시도',
    password: '비밀번호 입력',
    setup: '서버 설정 스크립트',
    trust: '설정하기',
  };
  const ENDED = {
    USER_EXIT: { short: '세션 종료', text: '세션을 종료했습니다.', actions: ['reconnect'] },
    AUTH_FAILED: {
      short: '로그인 거부',
      text: '로그인 거부: 비밀번호가 틀렸거나, 도메인 계정을 도메인 없이 적었을 수 있습니다(도메인\\계정으로 적습니다). '
        + 'Administrator가 아닌 로컬 계정이면 서버에 원격 관리자 설정이 필요합니다.',
      actions: ['password', 'setup'],
    },
    UNREACHABLE: {
      short: '응답 없음',
      text: '서버 응답 없음: WinRM이 꺼져 있거나 5985가 막혀 있을 수 있습니다.',
      actions: ['retry', 'setup'],
    },
    NOT_TRUSTED: { short: '초기 설정 필요', text: '초기 설정 필요: 이 PC의 TrustedHosts에 서버가 없습니다.', actions: ['trust'] },
    QUOTA: {
      short: '세션 한도 초과',
      text: '서버의 동시 세션 한도 초과: 다른 탭을 닫거나 서버 설정 스크립트로 한도를 올리세요.',
      actions: ['retry', 'setup'],
    },
    SECRET_UNREADABLE: {
      short: '비밀번호 읽기 실패',
      text: '저장된 비밀번호를 읽을 수 없습니다(PC나 계정이 바뀜). 다시 입력하세요.',
      actions: ['password'],
    },
    DISCONNECTED: { short: '연결 끊김', text: '연결이 끊겼습니다.', actions: ['reconnect'] },
    NOT_READY: {
      short: '시작 안 됨',
      text: '접속 스크립트가 시작되지 않았습니다. 백신이나 보안 정책이 막았을 수 있습니다.',
      actions: ['reconnect'],
    },
    UNKNOWN: { short: '알 수 없는 오류', text: '알 수 없는 오류입니다. 위 터미널의 오류 내용을 확인하세요.', actions: ['reconnect'] },
  };

  function endedView(reason) {
    const view = ENDED[reason] || ENDED.UNKNOWN;
    return { short: view.short, text: view.text, actions: view.actions.map((id) => ({ id, label: ACTION_LABELS[id] })) };
  }

  /* 터미널(xterm.js)이 ConPTY의 질의에 답하는 응답: DA1, DA2, 커서 위치, 장치 상태, 포커스 들어옴/나감.
     src/hq/sessions.py의 _TERMINAL_REPLIES와 같은 규칙이다(덩어리 전체가 응답이어야 한다). */
  const TERMINAL_REPLIES = /^(?:\x1b\[\?[0-9;]*c|\x1b\[>[0-9;]*c|\x1b\[[0-9]+;[0-9]+R|\x1b\[0n|\x1b\[[IO])+$/;

  function isTerminalReply(data) {
    return typeof data === 'string' && TERMINAL_REPLIES.test(data);
  }

  /* pywebview는 화면이 부른 함수마다 스레드를 따로 띄워 순서가 바뀔 수 있다.
     그래서 앞 호출이 끝난 뒤에 다음을 보내고, 그사이 들어온 입력은 묶어서 보낸다.
     터미널 응답은 다른 입력과 묶지 않고 따로 보낸다. 다른 글자와 섞이면 준비 신호 전의 탭이 그 덩어리를
     통째로 쥐고 있어, ConPTY가 DA1 답을 기다리느라 탭 시작이 약 3초 늦어진다. */
  function createInputQueue(send) {
    const waiting = []; // 보낼 차례를 기다리는 덩어리 [{data, reply}]. 터미널 응답은 늘 혼자 한 덩어리다
    let busy = false;
    function flush() {
      if (busy || waiting.length === 0) return;
      const { data } = waiting.shift();
      busy = true;
      Promise.resolve()
        .then(() => send(data))
        .catch((error) => console.warn('입력을 보내지 못했습니다:', error))
        .finally(() => {
          busy = false;
          flush();
        });
    }
    return {
      push(data) {
        if (!data) return;
        const reply = isTerminalReply(data);
        const last = waiting[waiting.length - 1];
        if (!reply && last && !last.reply) last.data += data;
        else waiting.push({ data, reply });
        flush();
      },
      pending() {
        return waiting.map((chunk) => chunk.data).join('');
      },
    };
  }

  /* 탭의 입력(xterm onData)을 hq로 보내도 되는지. 끝난 탭의 입력은 막는다. 다만 다시 연결하는 동안(starting)에는
     새 ConPTY가 시작하며 묻는 질의(DA1 등)의 답, 곧 터미널 응답만 보낸다. 막으면 ConPTY가 답을 기다리느라 탭 시작이
     약 3초 늦어진다. 끝나지 않은 탭(여는 중 포함)은 모두 보낸다. 준비 신호 전의 입력은 hq가 쥐고 있다. */
  function shouldSendInput(tab, data) {
    if (!tab.ended) return true;
    return Boolean(tab.starting) && isTerminalReply(data);
  }

  /* 붙여넣기를 어떻게 할지. 끝난 탭은 입력이 잠겨 있으니 묻지 않고 버린다('ignore'). 여러 줄은 먼저 묻고('confirm'),
     한 줄은 xterm에 맡긴다('pass'). */
  function pasteAction(text, ended) {
    if (ended) return 'ignore';
    return needsPasteConfirm(text) ? 'confirm' : 'pass';
  }

  /* hq 함수를 부르고 그 결과를 돌려준다. 호출이 결과({ok:false, ...}) 대신 예외로 끝나면 기록하고 실패 결과로 바꾼다.
     그래야 프로세스 없는 탭이 '실행 중'으로 남지 않고 평소의 실패 처리(탭 닫기, 안내)를 탄다. */
  async function callHq(call) {
    try {
      return await call();
    } catch (error) {
      console.warn('hq 호출이 실패했습니다:', error);
      const detail = error && error.message ? error.message : String(error);
      return { ok: false, error: `hq가 요청을 처리하지 못했습니다: ${detail}` };
    }
  }

  function newId() {
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  }

  return {
    UNGROUPED,
    displayName,
    filterServers,
    groupServers,
    nextOrdinal,
    tabTitle,
    pastedLineCount,
    needsPasteConfirm,
    shortcutFor,
    statusView,
    endedView,
    isTerminalReply,
    createInputQueue,
    shouldSendInput,
    pasteAction,
    callHq,
    newId,
  };
});
