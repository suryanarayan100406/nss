/* ==========================================================================
   NSS · IIIT Naya Raipur — animation + interaction
   Libraries (all bundled in /vendor, no CDN needed):
     GSAP + ScrollTrigger + DrawSVGPlugin  → every animation
     Lenis                                 → smooth (inertial) scrolling
   Order of this file:
     helpers → loader → hero → nav/cursor → sections → events → modal → boot
   ========================================================================== */
(function () {
  'use strict';

  var doc = document.documentElement;
  function $(s, c) { return (c || document).querySelector(s); }
  function $$(s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function debounce(fn, ms) { var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); }; }

  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  var hasGsap = !!(window.gsap && window.ScrollTrigger);

  if (!hasGsap) {                       // libraries failed to load → plain, fully visible site
    doc.classList.add('no-gsap');
    doc.classList.remove('is-loading');
    clearTimeout(window.__plFailsafe);
    return;
  }

  var plugins = [ScrollTrigger];
  var hasDraw = !!window.DrawSVGPlugin;
  if (hasDraw) plugins.push(DrawSVGPlugin);
  gsap.registerPlugin.apply(gsap, plugins);
  ScrollTrigger.config({ ignoreMobileResize: true });

  var lenis = null;
  var nav = $('#siteNav');
  var navLinks = $('#navLinks');
  var navToggle = $('#navToggle');

  /* ---------------------------------------------------------------------
     SMOOTH SCROLL (Lenis) + anchor links
  --------------------------------------------------------------------- */
  function setupLenis() {
    if (reduce || !window.Lenis) return;
    lenis = new Lenis({ lerp: 0.085, wheelMultiplier: 0.95, smoothWheel: true });
    lenis.on('scroll', ScrollTrigger.update);
    gsap.ticker.add(function (t) { lenis.raf(t * 1000); });
    gsap.ticker.lagSmoothing(0);
    lenis.stop();                        // locked until the loader finishes
  }

  function scrollToTarget(target) {
    if (lenis) {
      lenis.scrollTo(target, { duration: 1.6, easing: function (t) { return 1 - Math.pow(1 - t, 4); } });
    } else if (typeof target === 'number') {
      window.scrollTo(0, target);
    } else {
      var el = $(target);
      if (el) el.scrollIntoView();
    }
  }

  function setupAnchors() {
    $$('a[href^="#"]').forEach(function (a) {
      a.addEventListener('click', function (e) {
        var id = a.getAttribute('href');
        if (!id || id.length < 2) return;
        var el = $(id);
        if (!el) return;
        e.preventDefault();
        closeMenu();
        scrollToTarget(id === '#top' ? 0 : id);
      });
    });
  }

  /* ---------------------------------------------------------------------
     MOBILE MENU
  --------------------------------------------------------------------- */
  function closeMenu() {
    if (!navLinks) return;
    navLinks.classList.remove('open');
    navToggle.setAttribute('aria-expanded', 'false');
    navToggle.setAttribute('aria-label', 'Open menu');
    if (lenis && !doc.classList.contains('is-loading')) lenis.start();
  }
  function setupMenu() {
    navToggle.addEventListener('click', function () {
      var open = !navLinks.classList.contains('open');
      navLinks.classList.toggle('open', open);
      navToggle.setAttribute('aria-expanded', String(open));
      navToggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
      nav.classList.remove('nav-hidden');
      if (lenis) { open ? lenis.stop() : lenis.start(); }
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeMenu(); });
  }

  /* ---------------------------------------------------------------------
     PRELOADER — the NSS wheel builds itself, then floods marigold and lifts.
     Everything is SVG/HTML (no image). Ring lettering = motto + unit name;
     24 ticks = hours of the day; 8 spokes = the wheel.
  --------------------------------------------------------------------- */
  function buildLoaderArt() {
    var NS = 'http://www.w3.org/2000/svg';
    var letters = $('.pl-letters'), ticks = $('.pl-ticks'), spokes = $('.pl-spokes');
    if (!letters) return;
    var str = 'NOT ME BUT YOU \u2022 NATIONAL SERVICE SCHEME \u2022 ';
    var n = str.length, i, el;
    for (i = 0; i < n; i++) {
      if (str.charAt(i) === ' ') continue;
      el = document.createElementNS(NS, 'text');
      el.setAttribute('x', '120'); el.setAttribute('y', '14');
      el.setAttribute('text-anchor', 'middle');
      el.setAttribute('transform', 'rotate(' + (i / n * 360) + ' 120 120)');
      el.textContent = str.charAt(i);
      letters.appendChild(el);
    }
    for (i = 0; i < 24; i++) {
      el = document.createElementNS(NS, 'line');
      el.setAttribute('class', 'pl-tick');
      el.setAttribute('x1', '120'); el.setAttribute('x2', '120');
      el.setAttribute('y1', '36'); el.setAttribute('y2', i % 3 === 0 ? '22' : '29');
      el.setAttribute('transform', 'rotate(' + (i * 15) + ' 120 120)');
      ticks.appendChild(el);
    }
    for (i = 0; i < 8; i++) {
      el = document.createElementNS(NS, 'line');
      el.setAttribute('class', 'pl-spoke');
      el.setAttribute('x1', '120'); el.setAttribute('x2', '120');
      el.setAttribute('y1', '105'); el.setAttribute('y2', '58');
      el.setAttribute('transform', 'rotate(' + (i * 45) + ' 120 120)');
      spokes.appendChild(el);
    }
  }

  function finishLoader(pre) {
    if (pre) pre.style.display = 'none';
    doc.classList.remove('is-loading');
    clearTimeout(window.__plFailsafe);
    if (lenis) lenis.start();
    ScrollTrigger.refresh();
  }

  function runLoader(onReveal) {
    var pre = $('#preloader');
    if (!pre || reduce) { finishLoader(pre); onReveal(); return; }

    buildLoaderArt();
    var O = '120 120';
    var rims = $$('.pl-rim, .pl-rim2', pre);
    var spokes = $$('.pl-spoke', pre);
    var ticks = $$('.pl-tick', pre);
    var letters = $$('.pl-letters text', pre);
    var count = $('#plCount');

    if (hasDraw) gsap.set(rims.concat(spokes, ticks), { drawSVG: '0%' });
    gsap.set('.pl-letters, .pl-spokes, .pl-ticks', { svgOrigin: O });
    gsap.set(rims, { rotation: -90, svgOrigin: O });
    gsap.set(letters, { opacity: 0 });
    gsap.set('.pl-hub, .pl-hubring', { scale: 0, svgOrigin: O });
    gsap.set('.pl-lockup .l1 span, .pl-lockup .l2 span', { yPercent: 115 });
    gsap.set('.pl-meta', { opacity: 0 });

    // Build sequence (≈2.7s)
    var build = gsap.timeline({
      onUpdate: function () { count.textContent = Math.round(build.progress() * 100); },
      onComplete: function () { count.textContent = '100'; ready.then(exit); }
    });
    build
      .to('.pl-meta', { opacity: 1, duration: 0.5 }, 0)
      .to('.pl-rim', { drawSVG: '100%', duration: 1.1, ease: 'power2.inOut' }, 0.1)
      .to('.pl-rim2', { drawSVG: '100%', duration: 1.0, ease: 'power2.inOut' }, 0.3)
      .to(spokes, { drawSVG: '100%', duration: 0.6, stagger: 0.08, ease: 'power2.out' }, 0.6)
      .to(ticks, { drawSVG: '100%', duration: 0.45, stagger: 0.025, ease: 'power2.out' }, 0.85)
      .to(letters, { opacity: 1, duration: 0.4, stagger: 0.035, ease: 'power1.out' }, 1.0)
      .to('.pl-hubring', { scale: 1, duration: 0.7, ease: 'back.out(2)' }, 1.35)
      .to('.pl-hub', { scale: 1, duration: 0.55, ease: 'back.out(3)' }, 1.45)
      .to('.pl-lockup .l1 span', { yPercent: 0, duration: 0.8, ease: 'power3.out' }, 1.75)
      .to('.pl-lockup .l2 span', { yPercent: 0, duration: 0.8, ease: 'power3.out' }, 1.9)
      .to({}, { duration: 0.25 });                // small hold

    // Constant motion while it builds: the layers turn against each other
    gsap.fromTo('.pl-spokes', { rotation: -70 }, { rotation: 0, duration: 2.9, ease: 'power2.out' });
    gsap.fromTo('.pl-ticks', { rotation: 40 }, { rotation: 0, duration: 2.9, ease: 'power2.out' });
    gsap.fromTo('.pl-letters', { rotation: 60 }, { rotation: 0, duration: 3.1, ease: 'power3.out' });

    // Wait for real page load (fonts + images), but never longer than ~5s
    var loaded = new Promise(function (res) {
      if (document.readyState === 'complete') res();
      else window.addEventListener('load', res, { once: true });
    });
    var fonts = (document.fonts && document.fonts.ready) ? document.fonts.ready : Promise.resolve();
    var ready = Promise.race([
      Promise.all([loaded, fonts]),
      new Promise(function (r) { setTimeout(r, 5000); })
    ]);

    function exit() {
      var tl = gsap.timeline({ onComplete: function () { finishLoader(pre); } });
      gsap.set(pre, { borderBottomLeftRadius: '0% 0vh', borderBottomRightRadius: '0% 0vh' });
      tl.to('.pl-lockup', { opacity: 0, y: -12, duration: 0.35, ease: 'power2.in' }, 0)
        .to('.pl-spokes', { rotation: 100, duration: 1, ease: 'power3.in' }, 0)
        .to('.pl-stage', { scale: 1.12, duration: 1, ease: 'power3.in' }, 0)
        .to('.pl-flood', { clipPath: 'circle(75% at 50% 50%)', duration: 0.9, ease: 'power3.inOut' }, 0.3)
        .to('.pl-stage, .pl-meta', { color: '#0F1712', duration: 0.5, ease: 'none' }, 0.55)
        .to(pre, {
          yPercent: -100, borderBottomLeftRadius: '50% 14vh', borderBottomRightRadius: '50% 14vh',
          duration: 1.05, ease: 'power4.inOut'
        }, 1.15)
        .add(onReveal, 1.45);
    }
  }

  /* ---------------------------------------------------------------------
     HERO
  --------------------------------------------------------------------- */
  function heroInitialState() {
    gsap.set('#heroMedia', { scale: 1.25 });
    gsap.set('#heroTitle .line > span', { yPercent: 115, skewY: 5 });
    gsap.set(['#heroSub', '#heroActions'], { opacity: 0, y: 26 });
    gsap.set('#heroScrollHint', { opacity: 0 });
    gsap.set('#heroWheel', { opacity: 0, scale: 0.5, rotation: -90 });
    gsap.set(nav, { opacity: 0 });
  }

  function heroIntro() {
    var tl = gsap.timeline({ defaults: { ease: 'power4.out' } });
    tl.to('#heroMedia', { scale: 1, duration: 2.8, ease: 'power3.out' }, 0)
      .to('#heroTitle .line > span', { yPercent: 0, skewY: 0, duration: 1.3, stagger: 0.14 }, 0.1)
      .add(function () { $$('#heroTitle .hl').forEach(function (h) { h.classList.add('on'); }); }, 0.25)
      .to('#heroSub', { opacity: 1, y: 0, duration: 1.1 }, 0.6)
      .to('#heroActions', { opacity: 1, y: 0, duration: 1.1 }, 0.75)
      .to('#heroWheel', { opacity: 0.85, scale: 1, rotation: 0, duration: 1.6 }, 0.5)
      .to('#heroScrollHint', { opacity: 1, duration: 0.8 }, 1.1)
      .to(nav, { opacity: 1, duration: 0.8, ease: 'power2.out' }, 0.9);
  }

  function initHeroScroll() {
    // The hero stays pinned while the page slides over it; it darkens and settles back as it is covered.
    var tl = gsap.timeline({
      defaults: { ease: 'none', duration: 1 },
      scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: true, pin: true, pinSpacing: false }
    });
    tl.to('#heroParallax', { yPercent: 10, scale: 1.04 }, 0)
      .to('#heroContent', { yPercent: -14, opacity: 0.2 }, 0)
      .to('#heroWheelSpin', { rotation: 240, svgOrigin: '60 60' }, 0)
      .to('#heroDim', { opacity: 0.65 }, 0)
      .to('#heroScrollHint', { opacity: 0, duration: 0.25 }, 0);
  }

  // Drifting fireflies (canvas, sprite-based so it stays cheap). Pauses when the hero is off-screen.
  function initFireflies() {
    var cv = $('#fireflies');
    if (!cv || reduce) return;
    var ctx = cv.getContext('2d');
    var w = 0, h = 0, running = true, ps = [];
    var N = window.innerWidth < 700 ? 16 : 30;

    // one pre-rendered glow sprite, reused for every particle
    var sprite = document.createElement('canvas');
    sprite.width = sprite.height = 64;
    var sc = sprite.getContext('2d');
    var g = sc.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, 'rgba(255,232,176,1)');
    g.addColorStop(0.16, 'rgba(255,205,115,0.85)');
    g.addColorStop(0.5, 'rgba(232,166,60,0.16)');
    g.addColorStop(1, 'rgba(232,166,60,0)');
    sc.fillStyle = g; sc.fillRect(0, 0, 64, 64);

    function resize() {
      var k = 0.5;                                   // half resolution: the glow is soft, nobody can tell
      w = cv.clientWidth; h = cv.clientHeight;
      cv.width = Math.round(w * k); cv.height = Math.round(h * k);
      ctx.setTransform(k, 0, 0, k, 0, 0);
    }
    function spawn(initial) {
      return {
        x: Math.random() * w,
        y: initial ? Math.random() * h : h + 12,
        s: 10 + Math.random() * 20,
        vy: 0.1 + Math.random() * 0.38,
        sway: 0.4 + Math.random() * 1.1,
        ph: Math.random() * 6.28,
        sp: 0.5 + Math.random() * 1.2
      };
    }
    resize();
    for (var i = 0; i < N; i++) ps.push(spawn(true));
    window.addEventListener('resize', debounce(resize, 200));

    var last = 0;
    function frame(ts) {
      requestAnimationFrame(frame);
      if (!running || ts - last < 32) return;          // ~30fps is plenty for slow drifting
      var dt = Math.min((ts - last) / 16.67, 4); last = ts;
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < ps.length; i++) {
        var p = ps[i];
        p.y -= p.vy * dt * 1.2;
        p.ph += 0.02 * p.sp * dt;
        p.x += Math.sin(p.ph) * p.sway * 0.35 * dt;
        if (p.y < -30) { ps[i] = spawn(false); continue; }
        ctx.globalAlpha = 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(p.ph * 2.2));
        ctx.drawImage(sprite, p.x - p.s / 2, p.y - p.s / 2, p.s, p.s);
      }
    }
    requestAnimationFrame(frame);
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (en) { running = en[0].isIntersecting; }, { threshold: 0 }).observe($('.hero'));
    }
  }

  /* ---------------------------------------------------------------------
     NAV — hides on scroll-down, returns on scroll-up; active section link;
     emblem turns as you scroll; slim progress bar on small screens.
  --------------------------------------------------------------------- */
  function initNav() {
    ScrollTrigger.create({
      start: 0, end: 'max',
      onUpdate: function (self) {
        var y = self.scroll();
        nav.classList.toggle('scrolled', y > 40);
        if (navLinks.classList.contains('open')) return;
        if (y > 360 && self.direction === 1) nav.classList.add('nav-hidden');
        else if (self.direction === -1 || y <= 360) nav.classList.remove('nav-hidden');
      }
    });

    $$('[data-section]').forEach(function (sec) {
      ScrollTrigger.create({
        trigger: sec, start: 'top 55%', end: 'bottom 55%',
        onToggle: function (self) {
          var link = $('nav.links a[href="#' + sec.id + '"]');
          if (link && self.isActive) {
            $$('nav.links a').forEach(function (a) { a.classList.remove('active'); });
            link.classList.add('active');
          } else if (link && !self.isActive) {
            link.classList.remove('active');
          }
        }
      });
    });

    gsap.to('#emblem .spin', {
      rotation: 1080, ease: 'none', transformOrigin: '50% 50%',
      scrollTrigger: { start: 0, end: 'max', scrub: 0.6 }
    });
    gsap.to('#scrollProgress', { scaleX: 1, ease: 'none', scrollTrigger: { start: 0, end: 'max', scrub: 0.2 } });
  }

  /* ---------------------------------------------------------------------
     CURSOR + SPOTLIGHT (mouse only). Add data-cursor="Text" to any element
     to make the ring turn into a labelled pill while hovering it.
  --------------------------------------------------------------------- */
  function initCursor() {
    if (!fine || reduce) return;
    doc.classList.add('has-cursor');
    var dot = $('#cursorDot'), ring = $('#cursorRing'), label = $('.cl', ring), spot = $('#spotlight');
    gsap.set([dot, ring, spot], { xPercent: -50, yPercent: -50, x: window.innerWidth / 2, y: window.innerHeight / 2 });
    var dx = gsap.quickTo(dot, 'x', { duration: 0.1, ease: 'power3' }), dy = gsap.quickTo(dot, 'y', { duration: 0.1, ease: 'power3' });
    var rx = gsap.quickTo(ring, 'x', { duration: 0.5, ease: 'power3' }), ry = gsap.quickTo(ring, 'y', { duration: 0.5, ease: 'power3' });
    var sx = gsap.quickTo(spot, 'x', { duration: 0.9, ease: 'power3' }), sy = gsap.quickTo(spot, 'y', { duration: 0.9, ease: 'power3' });
    window.addEventListener('mousemove', function (e) {
      dx(e.clientX); dy(e.clientY); rx(e.clientX); ry(e.clientY); sx(e.clientX); sy(e.clientY);
    }, { passive: true });

    document.addEventListener('mouseover', function (e) {
      var t = e.target.closest ? e.target.closest('[data-cursor]') : null;
      if (t) { label.textContent = t.getAttribute('data-cursor'); ring.classList.add('label'); ring.classList.remove('hover'); return; }
      ring.classList.remove('label');
      ring.classList.toggle('hover', !!(e.target.closest && e.target.closest('a, button, .event-card, .team-featured-card, .team-member-card, .team-leader-card, .quote-card')));
    });
    document.addEventListener('mouseleave', function () { ring.classList.remove('label', 'hover'); });

    $$('.hero, section.dark').forEach(function (sec) {
      sec.addEventListener('mouseenter', function () { spot.classList.add('show'); });
      sec.addEventListener('mouseleave', function () { spot.classList.remove('show'); });
    });

    // Magnetic buttons
    $$('.magnetic').forEach(function (el) {
      var qx = gsap.quickTo(el, 'x', { duration: 0.5, ease: 'power3' });
      var qy = gsap.quickTo(el, 'y', { duration: 0.5, ease: 'power3' });
      el.addEventListener('mousemove', function (e) {
        var r = el.getBoundingClientRect();
        qx((e.clientX - r.left - r.width / 2) * 0.3);
        qy((e.clientY - r.top - r.height / 2) * 0.4);
      });
      el.addEventListener('mouseleave', function () { qx(0); qy(0); });
    });
  }

  /* ---------------------------------------------------------------------
     SAPLING GROWTH METER — a sapling on the right edge grows with the page.
  --------------------------------------------------------------------- */
  function initGrowth(mm) {
    var host = $('#grow');
    if (!host || !hasDraw) return;
    mm.add('(min-width: 1100px)', function () {
      var STEM = 'M30 396 C 30 340 22 310 30 262 S 40 190 30 140 S 22 70 30 18';
      host.innerHTML =
        '<svg viewBox="0 0 60 400" fill="none" aria-hidden="true">' +
        '<path class="g-track" d="' + STEM + '"/>' +
        '<path class="g-stem" id="gStem" d="' + STEM + '"/>' +
        '<g id="gLeaves"></g><circle class="g-bud" id="gBud" cx="30" cy="18" r="5"/></svg>';
      var NS = 'http://www.w3.org/2000/svg';
      var stem = $('#gStem'), leaves = $('#gLeaves'), bud = $('#gBud');
      var len = stem.getTotalLength();
      var marks = [0.14, 0.29, 0.44, 0.59, 0.73, 0.86];
      var leafEls = marks.map(function (t, i) {
        var p = stem.getPointAtLength(len * t), p2 = stem.getPointAtLength(len * Math.min(1, t + 0.02));
        var ang = Math.atan2(p2.y - p.y, p2.x - p.x) * 180 / Math.PI;    // stem direction
        var side = i % 2 === 0 ? -1 : 1;
        var g = document.createElementNS(NS, 'g');
        g.setAttribute('transform', 'translate(' + p.x + ' ' + p.y + ') rotate(' + (ang + side * 55 + (side < 0 ? 180 : 0)) + ')');
        var path = document.createElementNS(NS, 'path');
        path.setAttribute('class', 'g-leaf');
        path.setAttribute('d', 'M0 0 Q 12 -13 32 -3 Q 15 9 0 0Z');
        g.appendChild(path); leaves.appendChild(g);
        return path;
      });
      gsap.set(leafEls, { scale: 0, transformOrigin: '0% 60%' });
      gsap.set(bud, { scale: 0, transformOrigin: '50% 50%' });

      var tl = gsap.timeline({ defaults: { ease: 'none' }, scrollTrigger: { start: 0, end: 'max', scrub: 0.7 } });
      tl.fromTo(stem, { drawSVG: '0%' }, { drawSVG: '100%', duration: 1 }, 0);
      leafEls.forEach(function (lf, i) { tl.to(lf, { scale: 1, duration: 0.07, ease: 'back.out(2)' }, marks[i] - 0.01); });
      tl.to(bud, { scale: 1, duration: 0.05, ease: 'back.out(3)' }, 0.95);

      // Steps aside while the events rail is pinned
      ScrollTrigger.create({
        trigger: '#events', start: 'top top', end: 'bottom bottom',
        onToggle: function (self) { gsap.to(host, { opacity: self.isActive ? 0 : 1, duration: 0.4, overwrite: true }); }
      });
      return function () { host.innerHTML = ''; gsap.set(host, { clearProps: 'opacity' }); };
    });
  }

  /* ---------------------------------------------------------------------
     MARQUEE — loops forever; speeds up and reverses with your scroll.
  --------------------------------------------------------------------- */
  function initMarquee() {
    var track = $('#marqueeTrack');
    if (!track) return;
    var tween = null, base = null, dir = 1, ts = 1;
    var setSkew = gsap.quickSetter(track, 'skewX', 'deg');
    var vst = ScrollTrigger.create({ start: 0, end: 'max' });   // used only for velocity

    function build() {
      if (tween) tween.kill();
      if (!base) base = track.children[0].cloneNode(true);
      while (track.children.length > 1) track.removeChild(track.lastChild);
      var unit = track.children[0].offsetWidth || 700;
      var need = Math.ceil((window.innerWidth + unit) / unit) + 1;
      for (var i = 1; i < need; i++) track.appendChild(base.cloneNode(true));
      gsap.set(track, { x: 0 });
      tween = gsap.to(track, { x: -unit, duration: unit / 70, ease: 'none', repeat: -1 });
    }
    build();
    window.addEventListener('resize', debounce(build, 250));
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(build);

    gsap.ticker.add(function () {
      var v = vst.getVelocity();
      if (Math.abs(v) > 30) dir = v > 0 ? 1 : -1;
      var target = dir * (1 + Math.min(Math.abs(v) / 300, 9));
      ts += (target - ts) * 0.08;
      if (tween) tween.timeScale(ts);
      setSkew(clamp(-v / 260, -7, 7));
    });
  }

  /* ---------------------------------------------------------------------
     TEXT + GENERIC REVEALS
  --------------------------------------------------------------------- */
  function splitWords(el) {
    var words = el.textContent.trim().split(/\s+/);
    el.textContent = '';
    return words.map(function (w, i) {
      var s = document.createElement('span');
      s.className = 'w'; s.textContent = w;
      el.appendChild(s);
      if (i < words.length - 1) el.appendChild(document.createTextNode(' '));
      return s;
    });
  }

  function initTextReveals() {
    // Section titles: each line slides up out of its mask
    $$('h2.section-title, h2.join-title').forEach(function (h) {
      var spans = $$('.line > span', h);
      var hls = $$('.hl[data-hl]', h);
      gsap.set(spans, { yPercent: 115, skewY: 4 });
      ScrollTrigger.create({
        trigger: h, start: 'top 88%', once: true,
        onEnter: function () {
          gsap.to(spans, { yPercent: 0, skewY: 0, duration: 1.15, ease: 'power4.out', stagger: 0.1 });
          if (hls.length) gsap.delayedCall(0.55, function () { hls.forEach(function (el) { el.classList.add('on'); }); });
        }
      });
    });

    // Eyebrow rules draw in
    $$('[data-eyebrow]').forEach(function (el) {
      ScrollTrigger.create({ trigger: el, start: 'top 92%', once: true, onEnter: function () { el.classList.add('in'); } });
    });

    // Scroll-filled paragraph: words light up as you read down
    $$('[data-fill]').forEach(function (p) {
      var words = splitWords(p);
      gsap.fromTo(words, { opacity: 0.16 }, {
        opacity: 1, ease: 'none', stagger: 0.12,
        scrollTrigger: { trigger: p, start: 'top 82%', end: 'bottom 48%', scrub: 0.5 }
      });
    });

    // Soft fade-ups for secondary copy
    $$('[data-fade]').forEach(function (el) {
      gsap.from(el, {
        y: 34, opacity: 0, duration: 1.2, ease: 'power3.out',
        scrollTrigger: { trigger: el, start: 'top 90%', once: true }
      });
    });
  }

  /* ---------------------------------------------------------------------
     ABOUT
  --------------------------------------------------------------------- */
  function initAbout(mm) {
    var duo = $('#aboutDuo');
    var img = duo ? $('img', duo) : null;
    if (duo) {
      gsap.set(duo, { clipPath: 'inset(100% 0% 0% 0%)' });
      ScrollTrigger.create({
        trigger: '.about-media', start: 'top 82%', once: true,
        onEnter: function () {
          gsap.to(duo, { clipPath: 'inset(0% 0% 0% 0%)', duration: 1.5, ease: 'power4.inOut' });
          if (img) gsap.from(img, { scale: 1.35, duration: 2, ease: 'power3.out' });
        }
      });
      if (img) gsap.fromTo(img, { yPercent: -5 }, {
        yPercent: 5, ease: 'none',
        scrollTrigger: { trigger: '.about-media', start: 'top bottom', end: 'bottom top', scrub: true }
      });
    }
    gsap.from('#aboutTag', { x: -50, opacity: 0, duration: 1.1, ease: 'power4.out', delay: 0.5,
      scrollTrigger: { trigger: '.about-media', start: 'top 75%', once: true } });
    gsap.from('#aboutChip', { scale: 0.5, rotate: -10, opacity: 0, duration: 1.1, ease: 'back.out(2)', delay: 0.8,
      scrollTrigger: { trigger: '.about-media', start: 'top 75%', once: true } });

    mm.add('(min-width: 901px)', function () {
      gsap.to('#tiltFrame', {
        rotationY: -5, rotationX: 2.5, ease: 'none', transformPerspective: 1400,
        scrollTrigger: { trigger: '.about', start: 'top 80%', end: 'bottom 30%', scrub: 0.6 }
      });
    });
  }

  /* ---------------------------------------------------------------------
     IMPACT + curved-edge sections
  --------------------------------------------------------------------- */
  function initImpact() {
    // Count-up + bar fill
    var items = $$('.impact-item');
    gsap.set(items, { y: 56, opacity: 0 });
    ScrollTrigger.batch(items, {
      start: 'top 90%', once: true,
      onEnter: function (batch) {
        gsap.to(batch, { y: 0, opacity: 1, duration: 1.1, ease: 'power3.out', stagger: 0.12 });
        batch.forEach(function (item, i) {
          var num = $('[data-count]', item), bar = $('[data-fill-bar]', item);
          var target = num ? parseInt(num.getAttribute('data-count'), 10) || 0 : 0;
          var o = { v: 0 };
          gsap.to(o, { v: target, duration: 2.2, delay: 0.15 + i * 0.12, ease: 'power3.out',
            onUpdate: function () { if (num) num.textContent = Math.round(o.v).toLocaleString('en-IN'); },
            onComplete: function () { if (num && num.parentElement) num.parentElement.classList.add('done'); } });
          if (bar) gsap.to(bar, { scaleX: parseFloat(bar.getAttribute('data-fill-bar')) || 0, duration: 1.9, delay: 0.3 + i * 0.12, ease: 'power3.out' });
        });
      }
    });

    // Dark sections arrive with rounded tops that flatten as they take over
    ['.impact', '.join'].forEach(function (sel) {
      gsap.to(sel, { borderTopLeftRadius: 0, borderTopRightRadius: 0, ease: 'none',
        scrollTrigger: { trigger: sel, start: 'top bottom', end: 'top 25%', scrub: true } });
    });

    gsap.to('.ambient-inner', { rotation: 220, ease: 'none',
      scrollTrigger: { trigger: '.impact', start: 'top bottom', end: 'bottom top', scrub: true } });
  }

  /* ---------------------------------------------------------------------
     EVENTS — vertical scroll drives a horizontal rail (desktop),
     native swipe rail on touch. Cards straighten and their photos drift.
  --------------------------------------------------------------------- */
  function initEvents(mm) {
    var rail = $('#eventsRail'), pin = $('#eventsPin');
    var cards = $$('.event-card');
    var bar = $('#railBar'), countEl = $('#railCount'), hint = $('#railHintText');
    if (!rail || !pin) return;
    var n = cards.length;
    if (countEl) countEl.textContent = '1 / ' + n;

    mm.add('(min-width: 881px)', function () {
      var dist = function () { return Math.max(0, rail.offsetWidth - pin.clientWidth); };
      var railTween = gsap.to(rail, {
        x: function () { return -dist(); }, ease: 'none',
        scrollTrigger: {
          trigger: '#events', start: 'top top', end: function () { return '+=' + (dist() + 200); },
          pin: pin, scrub: true, anticipatePin: 1, invalidateOnRefresh: true,
          refreshPriority: 10,          // measure the pin first so triggers further down the page see its spacing
          onUpdate: function (self) {
            if (bar) bar.style.transform = 'scaleX(' + self.progress.toFixed(4) + ')';
            if (countEl) countEl.textContent = (1 + Math.round(self.progress * (n - 1))) + ' / ' + n;
            if (hint) hint.textContent = self.progress > 0.96 ? 'Keep going down' : 'Keep scrolling';
          }
        }
      });

      cards.forEach(function (card) {
        gsap.fromTo(card, { rotate: 3, y: 36 }, { rotate: 0, y: 0, ease: 'none',
          scrollTrigger: { trigger: card, containerAnimation: railTween, start: 'left 100%', end: 'left 78%', scrub: true } });
        var m = $('.media img, .media .icon-tile svg', card);
        if (m) gsap.fromTo(m, { xPercent: -6 }, { xPercent: 6, ease: 'none',
          scrollTrigger: { trigger: card, containerAnimation: railTween, start: 'left 100%', end: 'right 0%', scrub: true } });
      });
    });

    mm.add('(max-width: 880px)', function () {
      gsap.from(cards, { y: 50, opacity: 0, duration: 1, ease: 'power3.out', stagger: 0.1,
        scrollTrigger: { trigger: rail, start: 'top 85%', once: true } });
    });
  }

  /* ---------------------------------------------------------------------
     TEAM
  --------------------------------------------------------------------- */
  function initTeam(mm) {
    /* --- Helper to shuffle core team cards while keeping Mansi & Surya adjacent --- */
    function shuffleCoreCards(container, selector) {
      if (!container) return;
      var cards = Array.from(container.querySelectorAll(selector));
      if (!cards.length) return;
      var mansi = cards.find(function (c) { return (c.textContent || '').includes('Mansi Yadav'); });
      var surya = cards.find(function (c) { return (c.textContent || '').includes('Surya Narayan'); });
      var pair = (mansi && surya) ? [mansi, surya] : (mansi ? [mansi] : (surya ? [surya] : []));
      var others = cards.filter(function (c) { return c !== mansi && c !== surya; });

      for (var i = others.length - 1; i > 0; i--) {
        var j = Math.floor(Math.random() * (i + 1));
        var tmp = others[i];
        others[i] = others[j];
        others[j] = tmp;
      }

      var insertIdx = Math.floor(Math.random() * (others.length + 1));
      var ordered = others.slice(0, insertIdx).concat(pair, others.slice(insertIdx));
      ordered.forEach(function (card) { container.appendChild(card); });
    }

    /* --- Main page: featured team marquee --- */
    var marqueeTrack = $('#teamMarqueeTrack');
    if (marqueeTrack) {
      // Keep NSS Officer and Heads leading, randomize core members with Surya beside Mansi
      var allCards = Array.from(marqueeTrack.querySelectorAll('.team-featured-card'));
      var officer = allCards.filter(function (c) {
        var role = (c.querySelector('.tf-role') || {}).textContent || '';
        return role.includes('Officer');
      });
      var studentLeaders = allCards.filter(function (c) {
        var role = (c.querySelector('.tf-role') || {}).textContent || '';
        return role.includes('Head');
      });
      var coreCards = allCards.filter(function (c) {
        var role = (c.querySelector('.tf-role') || {}).textContent || '';
        return !role.includes('Head') && !role.includes('Officer');
      });

      if (coreCards.length) {
        var mansiF = coreCards.find(function (c) { return (c.textContent || '').includes('Mansi Yadav'); });
        var suryaF = coreCards.find(function (c) { return (c.textContent || '').includes('Surya Narayan'); });
        var pairF = (mansiF && suryaF) ? [mansiF, suryaF] : (mansiF ? [mansiF] : (suryaF ? [suryaF] : []));
        var othersF = coreCards.filter(function (c) { return c !== mansiF && c !== suryaF; });

        for (var i = othersF.length - 1; i > 0; i--) {
          var j = Math.floor(Math.random() * (i + 1));
          var tmp = othersF[i];
          othersF[i] = othersF[j];
          othersF[j] = tmp;
        }

        var insertIdx = Math.floor(Math.random() * (othersF.length + 1));
        var orderedCore = othersF.slice(0, insertIdx).concat(pairF, othersF.slice(insertIdx));
        marqueeTrack.innerHTML = '';
        officer.concat(studentLeaders, orderedCore).forEach(function (c) { marqueeTrack.appendChild(c); });
      }

      // Duplicate the cards for seamless infinite scroll
      var original = marqueeTrack.innerHTML;
      marqueeTrack.innerHTML = original + original;

      // Reveal animation
      var featuredCards = $$('.team-featured-card');
      gsap.set(featuredCards, { y: 60, opacity: 0 });
      ScrollTrigger.batch(featuredCards, {
        start: 'top 92%', once: true,
        onEnter: function (b) { gsap.to(b, { y: 0, opacity: 1, duration: 1, ease: 'power4.out', stagger: 0.12 }); }
      });
    }

    /* --- Team page: member cards batch reveal --- */
    var coreGrid = $('.team-core-grid');
    if (coreGrid) {
      shuffleCoreCards(coreGrid, '.team-member-card');
    }

    var memberCards = $$('.team-member-card');
    if (memberCards.length) {
      gsap.set(memberCards, { y: 80, opacity: 0, rotate: 2.5 });
      ScrollTrigger.batch(memberCards, {
        start: 'top 92%', once: true,
        onEnter: function (b) { gsap.to(b, { y: 0, opacity: 1, rotate: 0, duration: 1.2, ease: 'power4.out', stagger: 0.09 }); }
      });

      if (fine) {
        memberCards.forEach(function (card) {
          card.addEventListener('mousemove', function (e) {
            var r = card.getBoundingClientRect();
            var x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
            gsap.to(card, { rotationX: -y * 9, rotationY: x * 9, transformPerspective: 700, duration: 0.5, ease: 'power2.out', overwrite: 'auto' });
          });
          card.addEventListener('mouseleave', function () {
            gsap.to(card, { rotationX: 0, rotationY: 0, duration: 0.9, ease: 'elastic.out(1,0.6)', overwrite: 'auto' });
          });
        });
      }
    }

    /* --- Team page: leader cards reveal --- */
    var leaderCards = $$('.team-leader-card');
    if (leaderCards.length) {
      gsap.set(leaderCards, { y: 60, opacity: 0 });
      ScrollTrigger.batch(leaderCards, {
        start: 'top 92%', once: true,
        onEnter: function (b) { gsap.to(b, { y: 0, opacity: 1, duration: 1.2, ease: 'power4.out', stagger: 0.15 }); }
      });
    }
  }

  /* ---------------------------------------------------------------------
     TESTIMONIALS — smooth infinite marquee
  --------------------------------------------------------------------- */
  function initTestimonials() {
    var quoteTrack = $('#quoteTrack');
    var quoteMarquee = $('#quoteMarquee');
    if (!quoteTrack) return;

    // Duplicate cards so each half has 8 cards for a seamless loop across all screens
    var orig = quoteTrack.innerHTML;
    var doubleOrig = orig + orig;
    quoteTrack.innerHTML = doubleOrig + doubleOrig;

    if (!reduce && quoteMarquee) {
      gsap.from(quoteMarquee, {
        y: 60, opacity: 0, duration: 1.2, ease: 'power4.out',
        scrollTrigger: { trigger: quoteMarquee, start: 'top 92%', once: true }
      });
    }
  }

  /* ---------------------------------------------------------------------
     JOIN
  --------------------------------------------------------------------- */
  function initJoin() {
    gsap.to('#stepsLine', { scaleY: 1, ease: 'none',
      scrollTrigger: { trigger: '#joinSteps', start: 'top 75%', end: 'bottom 62%', scrub: true } });
    $$('.step').forEach(function (s) {
      gsap.set(s, { opacity: 0.25, x: -22 });
      ScrollTrigger.create({
        trigger: s, start: 'top 78%',
        onEnter: function () { s.classList.add('on'); gsap.to(s, { opacity: 1, x: 0, duration: 0.9, ease: 'power3.out' }); },
        onLeaveBack: function () { s.classList.remove('on'); gsap.to(s, { opacity: 0.25, x: -22, duration: 0.6 }); }
      });
    });
    gsap.from('#socialRow a', { scale: 0, opacity: 0, duration: 0.8, stagger: 0.09, ease: 'back.out(2.4)',
      scrollTrigger: { trigger: '#socialRow', start: 'top 94%', once: true } });
    gsap.fromTo('#joinWheel', { yPercent: -18, scale: 0.85 }, { yPercent: 18, scale: 1.12, ease: 'none',
      scrollTrigger: { trigger: '.join', start: 'top bottom', end: 'bottom bottom', scrub: true } });
  }

  /* ---------------------------------------------------------------------
     FOOTER — sits fixed underneath; the page lifts away and the big
     line rises letter by letter while the wheel turns.
  --------------------------------------------------------------------- */
  function initFooter() {
    var foot = $('#footer');
    function setH() { doc.style.setProperty('--footer-h', foot.offsetHeight + 'px'); }
    setH();
    ScrollTrigger.addEventListener('refreshInit', setH);

    $$('#footerBig .fl').forEach(function (line) {
      var txt = line.textContent;
      line.textContent = '';
      Array.prototype.forEach.call(txt, function (c) {
        var s = document.createElement('span');
        s.className = 'ch';
        s.textContent = c === ' ' ? '\u00A0' : c;
        line.appendChild(s);
      });
    });
    var chars = $$('#footerBig .ch');
    gsap.set(chars, { yPercent: 118 });
    var tl = gsap.timeline({ defaults: { ease: 'none' },
      scrollTrigger: { trigger: '#footerReveal', start: 'top 90%', end: 'bottom bottom', scrub: true } });
    tl.to(chars, { yPercent: 0, stagger: 0.035, duration: 1, ease: 'power3.out' }, 0)
      .fromTo('#footerWheel', { rotation: -220 }, { rotation: 0, duration: 1.6 }, 0);
  }

  /* ---------------------------------------------------------------------
     INSTAGRAM PREVIEW MODAL
     Loads Instagram's own embed only when a card is opened.
  --------------------------------------------------------------------- */
  function initModal() {
    var dlg = $('#igModal');
    var triggers = $$('.event-card[data-ig]');
    if (!triggers.length) return;

    if (!dlg || typeof dlg.showModal !== 'function') {
      triggers.forEach(function (card) {
        $$('.ev-preview, .media-hit', card).forEach(function (b) {
          b.addEventListener('click', function () {
            window.open('https://www.instagram.com/p/' + card.getAttribute('data-ig') + '/', '_blank', 'noopener');
          });
        });
      });
      return;
    }

    var frame = $('#igFrame'), openLink = $('#igOpen'), shell = $('#igShell');
    var lastFocus = null;

    function openPost(code, from) {
      lastFocus = from;
      frame.src = 'https://www.instagram.com/p/' + code + '/embed/';
      openLink.href = 'https://www.instagram.com/p/' + code + '/';
      dlg.showModal();
      if (lenis) lenis.stop();
      if (!reduce) gsap.fromTo(shell, { y: 46, opacity: 0, scale: 0.95 }, { y: 0, opacity: 1, scale: 1, duration: 0.8, ease: 'power4.out' });
    }
    function done() {
      dlg.close();
      frame.src = 'about:blank';
      if (lenis && !navLinks.classList.contains('open')) lenis.start();
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    function closeModal() {
      if (!dlg.open) return;
      if (reduce) done();
      else gsap.to(shell, { y: 30, opacity: 0, duration: 0.35, ease: 'power2.in', onComplete: done });
    }

    triggers.forEach(function (card) {
      $$('.ev-preview, .media-hit', card).forEach(function (b) {
        b.addEventListener('click', function () { openPost(card.getAttribute('data-ig'), b); });
      });
    });
    $('#igClose').addEventListener('click', closeModal);
    dlg.addEventListener('click', function (e) { if (e.target === dlg || e.target.id === 'igWrap') closeModal(); });
    dlg.addEventListener('cancel', function (e) { e.preventDefault(); closeModal(); });
  }

  /* ---------------------------------------------------------------------
     REDUCED-MOTION PATH: no smooth scroll, no loader, everything visible.
  --------------------------------------------------------------------- */
  function initReduced() {
    $$('.hl[data-hl]').forEach(function (h) { h.classList.add('on'); });
    $$('[data-eyebrow]').forEach(function (e) { e.classList.add('in'); });
    $$('[data-count]').forEach(function (n) { n.textContent = (parseInt(n.getAttribute('data-count'), 10) || 0).toLocaleString('en-IN'); });
    $$('[data-fill-bar]').forEach(function (b) { b.style.transform = 'scaleX(' + b.getAttribute('data-fill-bar') + ')'; });
    var s = $('#stepsLine'); if (s) s.style.transform = 'scaleY(1)';
    $$('.step').forEach(function (st) { st.classList.add('on'); });
    $$('#footerBig .fl').forEach(function () { /* text is already in place */ });
    window.addEventListener('scroll', function () { nav.classList.toggle('scrolled', window.scrollY > 40); }, { passive: true });
    var f = $('#footer'); if (f) doc.style.setProperty('--footer-h', f.offsetHeight + 'px');
    window.addEventListener('resize', debounce(function () { doc.style.setProperty('--footer-h', f.offsetHeight + 'px'); }, 200));
  }

  /* ---------------------------------------------------------------------
     If anything above throws, make sure the page still shows.
  --------------------------------------------------------------------- */
  function panic(err) {
    if (window.console) console.error('[NSS site] animation setup failed — showing plain page.', err);
    try { ScrollTrigger.getAll().forEach(function (t) { t.kill(); }); gsap.globalTimeline.clear(); } catch (e) { /* noop */ }
    doc.classList.add('no-gsap');
    doc.classList.remove('is-loading');
    var pre = $('#preloader'); if (pre) pre.style.display = 'none';
    $$('.line > span, .hero-sub, .hero-actions, .team-featured-card, .team-member-card, .team-leader-card, .impact-item, .step, .event-card, .quote-rail, [data-fade], #heroMedia, #heroWheel, #siteNav, #aboutDuo, #footerBig .ch')
      .forEach(function (el) { gsap.set(el, { clearProps: 'all' }); });
    $$('.hl[data-hl]').forEach(function (h) { h.classList.add('on'); });
    $$('[data-eyebrow]').forEach(function (e) { e.classList.add('in'); });
    $$('[data-count]').forEach(function (n) { n.textContent = n.getAttribute('data-count'); });
    $$('[data-fill-bar]').forEach(function (b) { b.style.transform = 'scaleX(' + b.getAttribute('data-fill-bar') + ')'; });
    clearTimeout(window.__plFailsafe);
  }

  /* ---------------------------------------------------------------------
     BOOT
  --------------------------------------------------------------------- */
  function boot() {
    try {
      setupMenu();
      setupLenis();
      setupAnchors();
      initModal();
      initTestimonials();

      if (reduce) { initReduced(); doc.classList.remove('is-loading'); clearTimeout(window.__plFailsafe); return; }

      var mm = gsap.matchMedia();
      heroInitialState();
      initCursor();
      initHeroScroll();
      initFireflies();
      initNav();
      initGrowth(mm);
      initMarquee();
      initTextReveals();
      initAbout(mm);
      initImpact();
      initEvents(mm);
      initTeam(mm);
      initJoin();
      initFooter();

      ScrollTrigger.addEventListener('refresh', function () { /* layout settled */ });
      window.addEventListener('load', function () { setTimeout(function () { ScrollTrigger.refresh(); }, 250); });
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { ScrollTrigger.refresh(); });

      runLoader(heroIntro);
    } catch (err) {
      panic(err);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
