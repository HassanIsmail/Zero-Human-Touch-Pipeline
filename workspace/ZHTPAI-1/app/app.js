(function () {
  'use strict';

  var STORAGE_KEY = 'zhtp_todos';

  function loadTodos() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (_) {
      return [];
    }
  }

  function saveTodos(todos) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(todos));
  }

  function generateId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  function addTodo(todos, text) {
    var trimmed = text.trim();
    if (!trimmed) return todos;
    return todos.concat({ id: generateId(), text: trimmed, completed: false });
  }

  function toggleTodo(todos, id) {
    return todos.map(function (todo) {
      return todo.id === id ? { id: todo.id, text: todo.text, completed: !todo.completed } : todo;
    });
  }

  function deleteTodo(todos, id) {
    return todos.filter(function (todo) {
      return todo.id !== id;
    });
  }

  function getRemainingCount(todos) {
    return todos.filter(function (todo) {
      return !todo.completed;
    }).length;
  }

  // ── DOM rendering ──────────────────────────────────────────────────────────

  function renderTodos(todos) {
    var list = document.getElementById('todo-list');
    var countEl = document.getElementById('count');
    var emptyEl = document.getElementById('empty-state');
    if (!list || !countEl || !emptyEl) return;

    list.innerHTML = '';

    var remaining = getRemainingCount(todos);
    countEl.textContent = remaining + (remaining === 1 ? ' item left' : ' items left');

    if (todos.length === 0) {
      emptyEl.style.display = 'block';
      return;
    }
    emptyEl.style.display = 'none';

    todos.forEach(function (todo) {
      var li = document.createElement('li');
      li.className = 'todo-item' + (todo.completed ? ' completed' : '');
      li.dataset.id = todo.id;

      var checkbox = document.createElement('button');
      checkbox.className = 'btn-check';
      checkbox.setAttribute('aria-label', todo.completed ? 'Mark incomplete' : 'Mark complete');
      checkbox.setAttribute('aria-pressed', String(todo.completed));
      checkbox.innerHTML =
        '<svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">' +
        '<rect x="0.5" y="0.5" width="15" height="15" rx="3.5"/>' +
        (todo.completed
          ? '<path d="M3.5 8L6.5 11L12.5 5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
          : '') +
        '</svg>';

      var span = document.createElement('span');
      span.className = 'todo-text';
      span.textContent = todo.text;

      var del = document.createElement('button');
      del.className = 'btn-delete';
      del.setAttribute('aria-label', 'Delete todo');
      del.innerHTML =
        '<svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">' +
        '<path d="M4 4L12 12M12 4L4 12" stroke-width="1.8" stroke-linecap="round"/>' +
        '</svg>';

      li.appendChild(checkbox);
      li.appendChild(span);
      li.appendChild(del);
      list.appendChild(li);
    });
  }

  // ── App state + event wiring ───────────────────────────────────────────────

  function init() {
    var state = loadTodos();
    renderTodos(state);

    var form = document.getElementById('add-form');
    var input = document.getElementById('todo-input');
    var list = document.getElementById('todo-list');

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var next = addTodo(state, input.value);
      if (next.length === state.length) return;
      state = next;
      saveTodos(state);
      renderTodos(state);
      input.value = '';
      input.focus();
    });

    list.addEventListener('click', function (e) {
      var checkBtn = e.target.closest('.btn-check');
      var delBtn = e.target.closest('.btn-delete');

      if (checkBtn) {
        var id = checkBtn.closest('.todo-item').dataset.id;
        state = toggleTodo(state, id);
        saveTodos(state);
        renderTodos(state);
      }

      if (delBtn) {
        var id = delBtn.closest('.todo-item').dataset.id;
        state = deleteTodo(state, id);
        saveTodos(state);
        renderTodos(state);
      }
    });
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  var api = { loadTodos, saveTodos, addTodo, toggleTodo, deleteTodo, getRemainingCount, generateId };

  if (typeof window !== 'undefined') {
    Object.assign(window, api);
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})();
