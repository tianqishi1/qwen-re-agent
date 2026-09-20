/* Qwencode Agent 前端交互脚本 */
(function () {
  'use strict';

  var API = '';
  var currentTab = 'chat';
  var currentSessionId = null;
  var chatBusy = false;
  var wfRunning = false;
  var wfAbort = null;
  var fileRoot = '';
  var fileCache = {}; // path -> {isDir, entries}

  /* ================= 基础工具 ================= */

  function $(sel) { return document.querySelector(sel); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  async function postJSON(url, body) {
    var r = await fetch(API + url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    return r.json();
  }

  /** POST + SSE 流式解析（fetch ReadableStream）。 */
  async function postSSE(url, body, onEvent) {
    var r = await fetch(API + url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    if (!r.body) {
      var text = await r.text();
      onEvent('message', text);
      return;
    }
    var reader = r.body.getReader();
    var decoder = new TextDecoder();
    var buf = '';
    while (true) {
      var res = await reader.read();
      if (res.done) break;
      buf += decoder.decode(res.value, { stream: true });
      var idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        var frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        parseFrame(frame, onEvent);
      }
    }
    if (buf.trim()) parseFrame(buf, onEvent);
  }

  function parseFrame(frame, onEvent) {
    var event = 'message';
    var data = '';
    frame.split('\n').forEach(function (line) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data += line.slice(5).replace(/^\s/, '');
    });
    if (event && data !== '') onEvent(event, data);
  }

  function fmtTime(ts) {
    var d = new Date(ts);
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function fmtSize(n) {
    if (n == null) return '';
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  /* ================= 标签切换 ================= */

  document.querySelectorAll('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('.tab').forEach(function (x) { x.classList.remove('active'); });
      document.querySelectorAll('.panel').forEach(function (x) { x.classList.remove('active'); });
      t.classList.add('active');
      currentTab = t.dataset.tab;
      $('#panel-' + currentTab).classList.add('active');
      if (currentTab === 'files') loadFileRoot();
      if (currentTab === 'workflow') loadStages();
    });
  });

  /* ================= 服务状态 ================= */

  async function refreshStatus() {
    try {
      var r = await fetch(API + '/api/health');
      var h = await r.json();
      setBadge('#svc-status', '服务 ' + (h.status === 'UP' ? '正常' : '降级'), h.status === 'UP');
      setBadge('#exec-status', '执行层 ' + (h.executorOk ? '在线' : '离线'), h.executorOk);
    } catch (e) {
      setBadge('#svc-status', '服务离线', false);
      setBadge('#exec-status', '执行层未知', false);
    }
    try {
      var c = await postJSON('/api/config', {});
      if (c.ok) {
        $('#model-label').textContent = c.model + ' · ' + c.workspace;
        fileRoot = c.workspace;
        $('#ws-root').textContent = c.workspace;
      }
    } catch (e) { /* ignore */ }
  }

  function setBadge(sel, text, ok) {
    var b = $(sel);
    b.textContent = text;
    b.className = 'badge ' + (ok ? 'badge-ok' : 'badge-err');
  }

  /* ================= 聊天面板 ================= */

  function addMsg(role, text, head) {
    var box = $('#chat-messages');
    var m = el('div', 'msg ' + role);
    if (head) m.appendChild(el('div', 'msg-head', head));
    m.appendChild(document.createTextNode(text));
    box.appendChild(m);
    box.scrollTop = box.scrollHeight;
    return m;
  }

  function addToolLine(kind, text) {
    var box = $('#chat-messages');
    var m = el('div', 'tool-line ' + kind, text);
    box.appendChild(m);
    box.scrollTop = box.scrollHeight;
  }

  function renderSessionList() {
    // 服务端未提供历史列表 API，本地维护已用过的会话
    var list = JSON.parse(localStorage.getItem('qwencode_sessions') || '[]');
    var ul = $('#session-items');
    ul.innerHTML = '';
    list.forEach(function (id) {
      var li = el('li', id === currentSessionId ? 'active' : '', '会话 ' + id.slice(0, 8));
      li.addEventListener('click', function () { currentSessionId = id; renderSessionList(); });
      ul.appendChild(li);
    });
  }

  function rememberSession(id) {
    var list = JSON.parse(localStorage.getItem('qwencode_sessions') || '[]');
    if (list.indexOf(id) < 0) { list.unshift(id); list = list.slice(0, 20); }
    localStorage.setItem('qwencode_sessions', JSON.stringify(list));
  }

  async function sendChat() {
    var input = $('#chat-input');
    var text = input.value.trim();
    if (!text || chatBusy) return;
    addMsg('user', text, '我 · ' + fmtTime(Date.now()));
    input.value = '';
    chatBusy = true;
    $('#chat-send').disabled = true;
    var sessionId = currentSessionId;
    var assistantStarted = false;
    var assistantEl = null;
    try {
      await postSSE('/api/chat/stream', { message: text, sessionId: sessionId }, function (event, data) {
        if (event === 'tool_call') {
          var c = JSON.parse(data);
          addToolLine('', '▶ ' + c.tool + '  ' + (c.args || '').slice(0, 300));
        } else if (event === 'tool_result') {
          var r = JSON.parse(data);
          addToolLine(r.success ? 'ok' : 'err',
            (r.success ? '✓ ' : '✗ ') + r.tool + ' (' + r.costMs + 'ms)  ' + (r.preview || '').slice(0, 200));
        } else if (event === 'text') {
          if (!assistantStarted) {
            assistantEl = addMsg('assistant', '', 'Agent · ' + fmtTime(Date.now()));
            assistantStarted = true;
          }
          assistantEl.textContent += data;
          $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight;
        } else if (event === 'done') {
          var d = JSON.parse(data);
          if (!assistantStarted) {
            addMsg('assistant', d.reply || '(无文本回复)', 'Agent · ' + fmtTime(Date.now()));
          }
          if (d.sessionId) {
            currentSessionId = d.sessionId;
            rememberSession(d.sessionId);
            renderSessionList();
          }
        } else if (event === 'error') {
          var e = JSON.parse(data);
          addMsg('system', '错误：' + (e.error || data));
        }
      });
    } catch (err) {
      addMsg('system', '请求失败：' + err.message);
    } finally {
      chatBusy = false;
      $('#chat-send').disabled = false;
      $('#chat-input').focus();
    }
  }

  $('#chat-send').addEventListener('click', sendChat);
  $('#chat-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  $('#new-session').addEventListener('click', function () {
    currentSessionId = null;
    renderSessionList();
    $('#chat-messages').innerHTML = '';
    $('#chat-messages').appendChild(
      el('div', 'msg system', '新会话已就绪，输入想法开始。'));
  });

  /* ================= 工作流面板 ================= */

  async function loadStages() {
    try {
      var r = await postJSON('/api/workflow/stages', {});
      if (!r.ok) return;
      var ul = $('#stage-desc');
      ul.innerHTML = '';
      (r.stages || []).forEach(function (s) {
        var li = el('li');
        li.appendChild(el('b', s.name + '：'));
        li.appendChild(document.createTextNode(s.description));
        ul.appendChild(li);
      });
    } catch (e) { /* ignore */ }
  }

  function stagePill(name, status) {
    var p = el('span', 'stage-pill ' + status, name);
    return p;
  }

  function logLine(cls, text) {
    var box = $('#wf-log');
    box.appendChild(el('div', 'line ' + cls, text));
    box.scrollTop = box.scrollHeight;
  }

  function resetWorkflowUI() {
    $('#wf-stages').innerHTML = '';
    $('#wf-log').innerHTML = '';
    $('#wf-artifacts').innerHTML = '';
    $('#wf-progress').textContent = '';
  }

  async function runWorkflow() {
    var idea = $('#wf-idea').value.trim();
    if (!idea || wfRunning) return;
    var stages = [];
    document.querySelectorAll('[data-stage]').forEach(function (cb) {
      if (cb.checked) stages.push(cb.dataset.stage);
    });
    if (!stages.length) { alert('请至少选择一个阶段'); return; }

    resetWorkflowUI();
    wfRunning = true;
    $('#wf-run').disabled = true;
    $('#wf-stop').disabled = false;

    // 预置阶段占位
    stages.forEach(function (s) {
      $('#wf-stages').appendChild(stagePill(s, ''));
    });

    var artifactSet = {};
    var done = false;
    try {
      await postSSE('/api/workflow/stream', { idea: idea, stages: stages }, function (event, data) {
        if (event === 'stage') {
          var st = JSON.parse(data);
          var pills = $('#wf-stages').children;
          for (var i = 0; i < pills.length; i++) {
            if (pills[i].textContent === st.stage) {
              var status = st.status === 'stage_start' ? 'running'
                : st.status === 'stage_end' ? 'done' : 'failed';
              pills[i].className = 'stage-pill ' + status;
            }
          }
          logLine('stage', '═══ 阶段「' + st.stage + '」 ' + (st.status === 'stage_start' ? '开始' : '结束') + ' ═══');
          if (st.status !== 'stage_start' && st.detail) {
            logLine('', (st.detail || '').slice(0, 400));
          }
        } else if (event === 'tool_call') {
          var c = JSON.parse(data);
          logLine('tool', '▶ ' + c.tool + '  ' + (c.args || '').slice(0, 250));
        } else if (event === 'tool_result') {
          var r = JSON.parse(data);
          logLine(r.success ? 'ok' : 'err',
            (r.success ? '✓ ' : '✗ ') + r.tool + ' (' + r.costMs + 'ms)');
        } else if (event === 'done') {
          var d = JSON.parse(data);
          done = true;
          (d.stages || []).forEach(function (sr) {
            var pills2 = $('#wf-stages').children;
            for (var j = 0; j < pills2.length; j++) {
              if (pills2[j].textContent === sr.stage) {
                pills2[j].className = 'stage-pill ' + (sr.success ? 'done' : 'failed');
              }
            }
            logLine(sr.success ? 'ok' : 'err', '阶段「' + sr.stage + '」' + (sr.success ? '成功' : '失败')
              + ' (' + sr.costSeconds + 's)' + (sr.loopProtected ? ' [循环防护触发]' : ''));
            (sr.artifacts || []).forEach(function (a) {
              if (!artifactSet[a]) {
                artifactSet[a] = true;
                var li = el('li', '', a);
                li.title = '点击在文件面板查看';
                li.addEventListener('click', function () { openFileInPanel(a); });
                $('#wf-artifacts').appendChild(li);
              }
            });
          });
          $('#wf-progress').textContent = d.ok ? '✅ 全部完成' : '⚠ 部分失败';
          $('#wf-progress').style.color = d.ok ? 'var(--ok)' : 'var(--err)';
        } else if (event === 'error') {
          var e = JSON.parse(data);
          logLine('err', '错误：' + (e.error || data));
          done = true;
        }
      });
      if (!done) logLine('err', '连接中断');
    } catch (err) {
      logLine('err', '请求失败：' + err.message);
    } finally {
      wfRunning = false;
      $('#wf-run').disabled = false;
      $('#wf-stop').disabled = true;
    }
  }

  function openFileInPanel(path) {
    // 切到文件面板并打开路径（只支持相对路径）
    document.querySelectorAll('.tab').forEach(function (x) { x.classList.remove('active'); });
    document.querySelectorAll('.panel').forEach(function (x) { x.classList.remove('active'); });
    var t = document.querySelector('[data-tab="files"]');
    t.classList.add('active');
    $('#panel-files').classList.add('active');
    currentTab = 'files';
    navigateTo(path);
  }

  $('#wf-run').addEventListener('click', runWorkflow);
  $('#wf-stop').addEventListener('click', function () {
    if (wfAbort) wfAbort.abort();
  });

  /* ================= 文件面板 ================= */

  function loadFileRoot() {
    navigateTo('');
  }

  async function navigateTo(relPath) {
    relPath = relPath || '';
    try {
      var r = await postJSON('/api/files?path=' + encodeURIComponent(relPath), {});
      renderFileBrowser(r);
    } catch (e) {
      $('#file-browser').innerHTML = '';
      $('#file-browser').appendChild(el('div', 'fb-item', '加载失败：' + e.message));
    }
  }

  function renderFileBrowser(data) {
    var box = $('#file-browser');
    box.innerHTML = '';
    if (!data.ok) {
      box.appendChild(el('div', 'fb-item', data.error || '错误'));
      return;
    }
    if (data.isDir) {
      (data.entries || []).forEach(function (e) {
        var row = el('div', 'fb-item');
        row.appendChild(el('span', 'icon', e.type === 'dir' ? '📁' : '📄'));
        row.appendChild(el('span', '', e.name));
        row.appendChild(el('span', 'fsize', e.type === 'dir' ? '' : fmtSize(e.size)));
        row.addEventListener('click', function () {
          if (e.type === 'dir') {
            navigateTo(data.path + '/' + e.name);
          } else {
            openFile(data.path + '/' + e.name);
          }
        });
        box.appendChild(row);
      });
    } else {
      openFile(data.path);
    }
  }

  async function openFile(relPath) {
    $('#file-meta').textContent = relPath + ' · 读取中…';
    try {
      var r = await postJSON('/api/files/content?path=' + encodeURIComponent(relPath), {});
      if (!r.ok) {
        $('#file-meta').textContent = r.error || '读取失败';
        $('#file-content').textContent = '';
        return;
      }
      $('#file-meta').textContent = relPath + ' · ' + fmtSize(r.size) + (r.truncated ? '（已截断，超过 200KB）' : '');
      $('#file-content').textContent = r.content;
    } catch (e) {
      $('#file-meta').textContent = '读取失败：' + e.message;
      $('#file-content').textContent = '';
    }
  }

  /* ================= 初始化 ================= */

  refreshStatus();
  setInterval(refreshStatus, 15000);
  renderSessionList();
  $('#chat-messages').appendChild(
    el('div', 'msg system', '会话就绪。输入想法，Agent 会在工作区内自主探索、调用工具并完成任务。'));
  loadStages();
})();
