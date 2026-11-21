// Injected into the page before any page script runs.
// Replaces every clock the page can see with one virtual clock that only moves
// when the renderer calls window.__vc.advance(ms).
// CSS transitions and CSS keyframe animations are paused and their currentTime
// is set from the same clock, so they move exactly with the page's own timers.
(function () {
  'use strict';
  var cfg = window.__VC_CONFIG || {};
  var now = 0;
  var EPOCH = cfg.epoch || Date.parse('2026-11-21T02:00:00Z');
  var timers = new Map();
  var nextId = 1;
  var seq = 0;
  var rafs = new Map();
  var rafId = 1;

  function addTimer(fn, delay, args, repeat) {
    var d = Math.max(0, Number(delay) || 0);
    if (repeat) d = Math.max(1, d);
    var id = nextId++;
    timers.set(id, { fn: fn, args: args, at: now + d, every: repeat ? d : 0, seq: seq++ });
    return id;
  }
  window.setTimeout = function (fn, delay) {
    return addTimer(fn, delay, [].slice.call(arguments, 2), false);
  };
  window.setInterval = function (fn, delay) {
    return addTimer(fn, delay, [].slice.call(arguments, 2), true);
  };
  window.clearTimeout = window.clearInterval = function (id) { timers.delete(id); };
  window.requestAnimationFrame = function (fn) { var id = rafId++; rafs.set(id, fn); return id; };
  window.cancelAnimationFrame = function (id) { rafs.delete(id); };

  performance.now = function () { return now; };
  var RealDate = Date;
  function VDate() {
    var a = [].slice.call(arguments);
    if (!(this instanceof VDate)) return new RealDate(EPOCH + now).toString();
    return a.length ? new (Function.prototype.bind.apply(RealDate, [null].concat(a)))() : new RealDate(EPOCH + now);
  }
  VDate.prototype = RealDate.prototype;
  VDate.now = function () { return EPOCH + now; };
  VDate.parse = RealDate.parse;
  VDate.UTC = RealDate.UTC;
  window.Date = VDate;

  // Every animation gets its start time on the virtual clock the first time we see it.
  // We look right after each timer callback, so the start time is the callback's time.
  var starts = new WeakMap();
  function syncAnimations(t) {
    var list = document.getAnimations();
    for (var i = 0; i < list.length; i++) {
      var a = list[i];
      if (!starts.has(a)) starts.set(a, t);
      if (a.playState !== 'paused') a.pause();
      a.currentTime = t - starts.get(a);
    }
  }

  function runCallback(fn, args) {
    try {
      if (typeof fn === 'function') fn.apply(window, args);
      else (0, eval)(String(fn));
    } catch (e) {
      console.error('[virtual-clock] callback error', e && e.stack || e);
    }
  }

  function advance(to) {
    for (;;) {
      var best = null, bestId = 0;
      timers.forEach(function (tm, id) {
        if (tm.at > to) return;
        if (!best || tm.at < best.at || (tm.at === best.at && tm.seq < best.seq)) { best = tm; bestId = id; }
      });
      if (!best) break;
      if (best.at > now) now = best.at;
      syncAnimations(now);
      if (best.every) { best.at += best.every; best.seq = seq++; } else timers.delete(bestId);
      runCallback(best.fn, best.args);
      syncAnimations(now);
    }
    now = Math.max(now, to);
    if (rafs.size) {
      var cbs = Array.from(rafs.values());
      rafs.clear();
      for (var i = 0; i < cbs.length; i++) runCallback(cbs[i], [now]);
    }
    syncAnimations(now);
    return now;
  }

  window.__vc = { advance: advance, now: function () { return now; } };

  // Render-time CSS and the auto-play start, added once the page's own script has run.
  document.addEventListener('DOMContentLoaded', function () {
    if (cfg.css) {
      var st = document.createElement('style');
      st.id = '__render_css';
      st.textContent = cfg.css;
      document.head.appendChild(st);
    }
    if (cfg.idleCaption) {
      var cap = document.querySelector('#caption span:last-child');
      if (cap && /Press Start/.test(cap.textContent)) cap.textContent = cfg.idleCaption;
    }
    // The page's first render toggles classes that start fade-in transitions at time 0
    // (start card, scrim). Settle them, so the first frame is the finished start card.
    if (cfg.settleInitial !== false) {
      document.getAnimations().forEach(function (a) { try { a.finish(); } catch (e) { /* infinite */ } });
    }
    setTimeout(function () {
      var btn = document.getElementById('bPlay');
      if (btn) btn.click();
    }, cfg.startHoldMs || 2500);
  });
})();
