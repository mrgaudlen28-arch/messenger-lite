const authCard = document.getElementById('authCard');
const appShell = document.getElementById('appShell');
const nicknameInput = document.getElementById('nicknameInput');
const passwordInput = document.getElementById('passwordInput');
const registerBtn = document.getElementById('registerBtn');
const loginBtn = document.getElementById('loginBtn');
const authError = document.getElementById('authError');
const meLabel = document.getElementById('meLabel');
const logoutBtn = document.getElementById('logoutBtn');
const usersList = document.getElementById('usersList');
const dialogsList = document.getElementById('dialogsList');
const chatHeader = document.getElementById('chatHeader');
const messagesBox = document.getElementById('messagesBox');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const photoInput = document.getElementById('photoInput');
const sendPhotoBtn = document.getElementById('sendPhotoBtn');

const storageKey = 'messenger-lite-session';

let state = {
  sessionToken: localStorage.getItem(storageKey) || '',
  me: null,
  users: [],
  dialogs: [],
  activeDialogId: null,
  ws: null,
  renderedMessageIds: new Set(),
};

function escapeHtml(text) {
  const div = document.createElement('div');
  div.innerText = text ?? '';
  return div.innerHTML;
}

function headers() {
  return {
    'Content-Type': 'application/json',
    'X-Session-Token': state.sessionToken,
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    data = null;
  }

  if (!response.ok) {
    const detail = data?.detail || 'Ошибка запроса';
    throw new Error(detail);
  }

  return data;
}

function showAuth() {
  authCard.classList.remove('hidden');
  appShell.classList.add('hidden');
}

function showApp() {
  authCard.classList.add('hidden');
  appShell.classList.remove('hidden');
}

async function registerUser() {
  const nickname = nicknameInput.value.trim();
  const password = passwordInput.value.trim();
  authError.textContent = '';

  if (nickname.length < 2) {
    authError.textContent = 'Никнейм должен быть минимум 2 символа';
    return;
  }
  if (password.length < 6) {
    authError.textContent = 'Пароль должен быть минимум 6 символов';
    return;
  }

  try {
    const data = await api('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nickname, password }),
    });

    state.sessionToken = data.session_token;
    localStorage.setItem(storageKey, state.sessionToken);
    state.me = data.user;

    await bootstrapApp();
  } catch (error) {
    authError.textContent = error.message;
  }
}

async function loginUser() {
  const nickname = nicknameInput.value.trim();
  const password = passwordInput.value.trim();
  authError.textContent = '';

  if (nickname.length < 2) {
    authError.textContent = 'Введите никнейм';
    return;
  }
  if (password.length < 6) {
    authError.textContent = 'Введите пароль';
    return;
  }

  try {
    const data = await api('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nickname, password }),
    });

    state.sessionToken = data.session_token;
    localStorage.setItem(storageKey, state.sessionToken);
    state.me = data.user;

    await bootstrapApp();
  } catch (error) {
    authError.textContent = error.message;
  }
}

async function logout() {
  try {
    if (state.sessionToken) {
      await api('/api/logout', {
        method: 'POST',
        headers: headers(),
      });
    }
  } catch (_) {}

  if (state.ws) {
    state.ws.close();
    state.ws = null;
  }

  localStorage.removeItem(storageKey);
  state = {
    sessionToken: '',
    me: null,
    users: [],
    dialogs: [],
    activeDialogId: null,
    ws: null,
    renderedMessageIds: new Set(),
  };

  usersList.innerHTML = '';
  dialogsList.innerHTML = '';
  messagesBox.innerHTML = '';
  chatHeader.textContent = 'Выбери пользователя или диалог';
  nicknameInput.value = '';
  passwordInput.value = '';
  photoInput.value = '';
  showAuth();
}

async function checkSession() {
  if (!state.sessionToken) {
    showAuth();
    return;
  }

  try {
    state.me = await api('/api/me', { headers: headers() });
    await bootstrapApp();
  } catch (_) {
    await logout();
  }
}

async function bootstrapApp() {
  showApp();
  meLabel.textContent = state.me.nickname;
  await Promise.all([loadUsers(), loadDialogs()]);
  connectWebSocket();
}

async function loadUsers() {
  state.users = await api('/api/users', { headers: headers() });
  renderUsers();
}

async function loadDialogs() {
  state.dialogs = await api('/api/dialogs', { headers: headers() });
  renderDialogs();
}

function renderUsers() {
  usersList.innerHTML = '';
  if (state.users.length === 0) {
    usersList.innerHTML = '<div class="empty-state">Пока других пользователей нет</div>';
    return;
  }

  state.users.forEach((user) => {
    const el = document.createElement('div');
    el.className = 'list-item';
    el.innerHTML = `
      <div class="item-title">${escapeHtml(user.nickname)}</div>
      <div class="item-subtitle">Нажми, чтобы открыть личный диалог</div>
    `;
    el.addEventListener('click', async () => {
      try {
        const dialog = await api('/api/dialogs/direct', {
          method: 'POST',
          headers: headers(),
          body: JSON.stringify({ target_user_id: user.id }),
        });
        await loadDialogs();
        await selectDialog(dialog.id);
      } catch (error) {
        alert(error.message);
      }
    });
    usersList.appendChild(el);
  });
}

function renderDialogs() {
  dialogsList.innerHTML = '';
  if (state.dialogs.length === 0) {
    dialogsList.innerHTML = '<div class="empty-state">Диалогов пока нет</div>';
    return;
  }

  state.dialogs.forEach((dialog) => {
    const el = document.createElement('div');
    el.className = 'list-item' + (dialog.id === state.activeDialogId ? ' active' : '');
    const preview = dialog.last_message_text ? escapeHtml(dialog.last_message_text) : 'Сообщений ещё нет';
    el.innerHTML = `
      <div class="item-title">${escapeHtml(dialog.partner_nickname)}</div>
      <div class="item-subtitle">${preview}</div>
    `;
    el.addEventListener('click', () => selectDialog(dialog.id));
    dialogsList.appendChild(el);
  });
}

async function selectDialog(dialogId) {
  state.activeDialogId = dialogId;
  state.renderedMessageIds = new Set();
  renderDialogs();

  const dialog = state.dialogs.find((item) => item.id === dialogId);
  chatHeader.textContent = dialog ? `Диалог с ${dialog.partner_nickname}` : 'Диалог';

  const messages = await api(`/api/dialogs/${dialogId}/messages`, { headers: headers() });
  messagesBox.innerHTML = '';
  if (messages.length === 0) {
    messagesBox.innerHTML = '<div class="empty-state">Пока сообщений нет</div>';
  }
  messages.forEach((message) => appendMessage(message, false));
  messagesBox.scrollTop = messagesBox.scrollHeight;
}

function appendMessage(message, scroll = true) {
  if (state.renderedMessageIds.has(message.id)) {
    return;
  }
  state.renderedMessageIds.add(message.id);

  const isMine = state.me && message.sender_id === state.me.id;
  const empty = messagesBox.querySelector('.empty-state');
  if (empty) {
    empty.remove();
  }

  const el = document.createElement('div');
  el.className = 'message' + (isMine ? ' mine' : '');

  let content = '';
  if (message.kind === 'image' && message.image_url) {
    content = `<img src="${message.image_url}" alt="photo" style="max-width: 260px; border-radius: 12px;" />`;
  } else {
    content = escapeHtml(message.text || '').replace(/\n/g, '<br>');
  }

  el.innerHTML = `
    <div class="message-meta">${escapeHtml(message.sender_nickname)} · ${new Date(message.created_at).toLocaleString()}</div>
    <div>${content}</div>
  `;
  messagesBox.appendChild(el);

  if (scroll) {
    messagesBox.scrollTop = messagesBox.scrollHeight;
  }
}

async function sendMessage() {
  const text = messageInput.value.trim();
  if (!state.activeDialogId) {
    alert('Сначала выбери диалог');
    return;
  }
  if (!text) {
    return;
  }

  try {
    const message = await api(`/api/dialogs/${state.activeDialogId}/messages`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ text }),
    });
    appendMessage(message);
    messageInput.value = '';
    await loadDialogs();
  } catch (error) {
    alert(error.message);
  }
}

async function sendPhoto() {
  if (!state.activeDialogId) {
    alert('Сначала выбери диалог');
    return;
  }

  const file = photoInput.files[0];
  if (!file) {
    alert('Сначала выбери фото');
    return;
  }

  const formData = new FormData();
  formData.append('photo', file);

  const response = await fetch(`/api/dialogs/${state.activeDialogId}/photo`, {
    method: 'POST',
    headers: {
      'X-Session-Token': state.sessionToken,
    },
    body: formData,
  });

  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    data = null;
  }

  if (!response.ok) {
    alert(data?.detail || 'Ошибка отправки фото');
    return;
  }

  appendMessage(data);
  photoInput.value = '';
  await loadDialogs();
}

function connectWebSocket() {
  if (!state.sessionToken) {
    return;
  }

  if (state.ws) {
    state.ws.close();
  }

  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl = `${protocol}://${location.host}/ws?token=${encodeURIComponent(state.sessionToken)}`;
  const ws = new WebSocket(wsUrl);
  state.ws = ws;

  ws.onmessage = async (event) => {
    const data = JSON.parse(event.data);

    if (data.type === 'new_message') {
      const message = data.message;
      if (message.dialog_id === state.activeDialogId) {
        appendMessage(message);
      }
      await loadDialogs();
      return;
    }

    if (data.type === 'dialogs_changed') {
      await loadDialogs();
      return;
    }
  };

  ws.onclose = () => {
    if (!state.sessionToken) {
      return;
    }
    setTimeout(connectWebSocket, 1500);
  };

  ws.onopen = () => {
    const pingInterval = setInterval(() => {
      if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
        clearInterval(pingInterval);
        return;
      }
      state.ws.send(JSON.stringify({ type: 'ping' }));
    }, 15000);
  };
}

registerBtn.addEventListener('click', registerUser);
loginBtn.addEventListener('click', loginUser);
logoutBtn.addEventListener('click', logout);
sendBtn.addEventListener('click', sendMessage);
sendPhotoBtn.addEventListener('click', sendPhoto);

passwordInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    loginUser();
  }
});

messageInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

checkSession();
