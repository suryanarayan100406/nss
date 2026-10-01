/* ===== Inauguration control =====
   A thin client over the admin API. It holds no authority of its own: every button
   here corresponds to a server route that re-checks the session, and the destructive
   one re-checks the password too. Disabling a control in this file would change
   nothing about what the server permits. */

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  // ---------------------------------------------------------------- transport

  function readCookie(name) {
    var parts = ('; ' + document.cookie).split('; ' + name + '=');
    return parts.length === 2 ? parts.pop().split(';').shift() : '';
  }

  function csrf() { return readCookie('nss_admin_csrf'); }

  function api(path, options) {
    var opts = options || {};
    var headers = { 'Accept': 'application/json' };

    if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRF-Token'] = csrf();
    }
    Object.keys(opts.headers || {}).forEach(function (k) { headers[k] = opts.headers[k]; });

    return fetch(path, {
      method: opts.method || 'GET',
      headers: headers,
      credentials: 'same-origin',
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body)
    }).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        if (!response.ok) {
          var message = (data && (data.error || data.detail)) || ('request failed (' + response.status + ')');
          var error = new Error(message);
          error.status = response.status;
          throw error;
        }
        return data;
      });
    });
  }

  // ------------------------------------------------------------------ helpers

  function notice(el, kind, text) {
    if (!text) { el.hidden = true; el.textContent = ''; return; }
    el.hidden = false;
    el.dataset.kind = kind;
    el.textContent = text;
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  /* An instant -> the value a datetime-local input wants (browser-local, no offset). */
  function toLocalInput(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return '';
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  /* A datetime-local value -> an ISO string carrying this browser's UTC offset.

     The offset is the whole point: '2026-10-02T10:15' alone means a different instant
     to every visitor, and the countdown would then differ by country. The server
     rejects an offset-less timestamp outright. */
  function toOffsetIso(localValue) {
    if (!localValue) return null;
    var d = new Date(localValue);
    if (isNaN(d)) return null;
    var offsetMinutes = -d.getTimezoneOffset();
    var sign = offsetMinutes >= 0 ? '+' : '-';
    var abs = Math.abs(offsetMinutes);
    return localValue.length === 16 ? localValue + ':00' + sign + pad(Math.floor(abs / 60)) + ':' + pad(abs % 60)
                                    : localValue + sign + pad(Math.floor(abs / 60)) + ':' + pad(abs % 60);
  }

  function prettyStamp(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    try {
      return d.toLocaleString(undefined, {
        day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'
      });
    } catch (e) { return d.toString(); }
  }

  function describeMode(mode) {
    if (mode === 'coming_soon') return 'Coming soon';
    if (mode === 'inauguration') return 'Live inauguration';
    if (mode === 'permanent') return 'Permanent website';
    return mode;
  }

  // ----------------------------------------------------------------- rendering

  var current = null;

  function render(state) {
    current = state;

    $('whoami').textContent = state.admin;

    var pill = $('modePill');
    pill.dataset.mode = state.site_mode;
    pill.textContent = describeMode(state.site_mode);

    $('stMode').textContent = describeMode(state.site_mode);
    $('stCeremony').textContent = state.ceremony_enabled ? 'Open to the public' : 'Hidden';
    $('stScheduled').textContent = prettyStamp(state.scheduled_at);
    $('stCountdown').textContent = state.countdown_enabled ? 'Shown' : 'Hidden';
    $('stCompleted').textContent = state.inauguration_completed
      ? (state.completed_by ? 'Yes — by ' + state.completed_by + ' on ' + prettyStamp(state.completed_at) : 'Yes')
      : 'Not yet';

    // Once the ribbon is cut the mode is permanent and the ceremony controls are done.
    var done = state.inauguration_completed;
    $('enableBtn').disabled = done || state.site_mode === 'inauguration';
    $('disableBtn').disabled = done || state.site_mode === 'coming_soon';
    $('previewBtn').disabled = done;
    $('saveScheduleBtn').disabled = done;

    $('scheduleInput').value = toLocalInput(state.scheduled_at);
    $('countdownToggle').checked = !!state.countdown_enabled;
    updateSchedulePreview();

    $('phraseHint').textContent = state.cleanup_phrase;
    renderCleanup(state);
    renderActivity(state.activity || []);
  }

  function renderCleanup(state) {
    var ready = !!state.cleanup_available;
    var phraseOk = $('cleanupPhrase').value === state.cleanup_phrase;
    var passwordOk = $('cleanupPassword').value.length > 0;
    $('cleanupBtn').disabled = !ready || !phraseOk || !passwordOk;

    if (!ready && !state.inauguration_completed) {
      notice($('cleanupNotice'), 'error',
        'Locked: the inauguration has not been completed yet.');
      $('cleanupNotice').hidden = true;   // stated inline already; keep the panel calm
    }
    $('cleanupPanel').dataset.locked = ready ? 'false' : 'true';
  }

  function renderActivity(entries) {
    var list = $('activity');
    list.textContent = '';

    if (!entries.length) {
      var empty = document.createElement('li');
      empty.textContent = 'Nothing recorded yet.';
      list.appendChild(empty);
      return;
    }

    entries.forEach(function (entry) {
      var li = document.createElement('li');

      var time = document.createElement('time');
      time.dateTime = entry.at || '';
      time.textContent = prettyStamp(entry.at);

      var event = document.createElement('span');
      event.className = 'ap-event';
      event.textContent = entry.event;

      var who = document.createElement('span');
      who.className = 'ap-who';
      who.textContent = entry.actor || entry.ip || '';

      li.appendChild(time);
      li.appendChild(event);
      li.appendChild(who);
      list.appendChild(li);
    });
  }

  function updateSchedulePreview() {
    var iso = toOffsetIso($('scheduleInput').value);
    $('schedulePreview').textContent = iso
      ? 'Will be stored as ' + iso
      : 'Pick a date and time.';
  }

  // -------------------------------------------------------------------- flows

  function fail(el) {
    return function (error) {
      if (error && error.status === 401) { showLogin(); return; }
      notice(el, 'error', error && error.message ? error.message : 'Something went wrong.');
    };
  }

  function showLogin() {
    $('login').hidden = false;
    $('dashboard').hidden = true;
  }

  function showDashboard(state) {
    $('login').hidden = true;
    $('dashboard').hidden = false;
    render(state);
  }

  function load() {
    return api('/api/admin/state')
      .then(function (data) { showDashboard(data.state); })
      .catch(function () { showLogin(); });
  }

  // ---- login / logout

  $('loginForm').addEventListener('submit', function (event) {
    event.preventDefault();
    var button = $('loginBtn');
    button.disabled = true;
    notice($('loginNotice'), 'error', '');

    api('/api/admin/login', {
      method: 'POST',
      body: { username: $('username').value, password: $('password').value }
    }).then(function (data) {
      $('password').value = '';
      showDashboard(data.state);
    }).catch(function (error) {
      notice($('loginNotice'), 'error',
        error && error.message ? error.message : 'Could not sign in.');
    }).then(function () {
      button.disabled = false;
    });
  });

  $('logoutBtn').addEventListener('click', function () {
    api('/api/admin/logout', { method: 'POST' })
      .then(function () { window.location.reload(); })
      .catch(fail($('statusNotice')));
  });

  // ---- ceremony

  function move(path, target, label) {
    notice($(target), '', '');
    api(path, { method: 'POST' })
      .then(function (data) {
        render(data.state);
        notice($(target), 'ok', label);
      })
      .catch(fail($(target)));
  }

  $('enableBtn').addEventListener('click', function () {
    move('/api/admin/ceremony/enable', 'ceremonyNotice', 'The ceremony is now open to the public.');
  });

  $('disableBtn').addEventListener('click', function () {
    move('/api/admin/ceremony/disable', 'ceremonyNotice', 'The site is back to the holding page.');
  });

  $('previewBtn').addEventListener('click', function () {
    window.open('/preview', '_blank', 'noopener');
  });

  // ---- schedule

  $('scheduleInput').addEventListener('input', updateSchedulePreview);

  $('saveScheduleBtn').addEventListener('click', function () {
    var iso = toOffsetIso($('scheduleInput').value);
    if (!iso) {
      notice($('scheduleNotice'), 'error', 'Pick a valid date and time first.');
      return;
    }

    notice($('scheduleNotice'), '', '');
    api('/api/admin/schedule', {
      method: 'POST',
      body: { scheduled_at: iso, countdown_enabled: $('countdownToggle').checked }
    }).then(function (data) {
      render(data.state);
      notice($('scheduleNotice'), 'ok', 'Saved. Visitors see ' + iso + '.');
    }).catch(fail($('scheduleNotice')));
  });

  // ---- cleanup

  ['cleanupPhrase', 'cleanupPassword'].forEach(function (id) {
    $(id).addEventListener('input', function () { renderCleanup(current || {}); });
  });

  $('cleanupBtn').addEventListener('click', function () {
    var button = $('cleanupBtn');
    notice($('cleanupNotice'), '', '');

    var confirmed = window.confirm(
      'Remove the inauguration system and leave the permanent NSS website serving?\n\n' +
      'This deletes the ceremony page, the holding page, this portal and the backend.\n' +
      'The permanent site is verified before anything is removed. This cannot be undone.'
    );
    if (!confirmed) return;

    button.disabled = true;
    notice($('cleanupNotice'), '', 'Working — verifying the permanent site before removing anything…');

    api('/api/cleanup/run', {
      method: 'POST',
      body: { phrase: $('cleanupPhrase').value, password: $('cleanupPassword').value }
    }).then(function (data) {
      notice($('cleanupNotice'), 'ok', (data && data.message) || 'Done.');
      $('cleanupPassword').value = '';
      window.setTimeout(function () { window.location.href = '/'; }, 2500);
    }).catch(function (error) {
      button.disabled = false;
      notice($('cleanupNotice'), 'error',
        error && error.message ? error.message : 'The cleanup did not complete.');
    });
  });

  // ---- go

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})();
