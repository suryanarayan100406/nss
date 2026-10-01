# NSS · IIIT Naya Raipur — animated website

A static site (HTML + CSS + JS). No build step, no CDN, no internet needed to run it
(fonts and animation libraries are bundled). Only the Instagram preview pop-up needs internet.

## Run it

Double-click `index.html`, or for the most accurate behaviour serve the folder:

```bash
npx serve .          # or:  python3 -m http.server 8000
```

## Put your images in (2 minutes)

The site already points at these file names, so just copy your files in — no code changes:

| File | Goes in | Used for |
|---|---|---|
| `_DSC0704.JPG` (plantation photo) | `assets/images/` | hero, about, first event card |
| `nss_logo.png` / `nss_logo.jpg` (NSS emblem) | `assets/images/` | nav logo (it turns as you scroll) |
| `event-1.jpg`, `event-2.jpg`, `event-3.jpg` | `assets/events/` | the three Instagram event cards |

If a file is missing, the site shows a designed fallback (illustrated landscape / icon tile) instead of a broken image.

**Tip:** resize photos to about 2000px wide (hero) and 1000px (event cards), JPG quality ~80. A full-size camera
file makes the loader wait longer.

### Event photos from Instagram

Save the pictures from your unit's own posts (or use the originals from the team) and name them
`event-1.jpg` … `event-3.jpg`. Each card also has a **Preview post** button that opens Instagram's own
embed, so the real post shows even before you add the photo.

| Card | Post |
|---|---|
| event-1 | https://www.instagram.com/p/DRIMupfDM8F/ |
| event-2 | https://www.instagram.com/p/DPOR_EHjGGK/ |
| event-3 | https://www.instagram.com/p/DOG0l8kk_TN/ |

## Edit content

* **Events** — `index.html`, search for `EDIT YOUR EVENTS HERE`. Each `<article class="event-card">` is one card.
  Change the title/date/text, `data-ig="POST_CODE"` (the part after `/p/`), and the two Instagram links.
  Add or delete cards freely; the horizontal scroll length recalculates itself.
* **Team, testimonials, numbers** — same file, just edit the text. Impact numbers use `data-count="…"`
  and bars use `data-fill-bar="0.88"` (0–1).
* **Colours / fonts** — the tokens at the top of `css/style.css`.

## What's animated (all in `js/main.js`, one function per section)

| Section | Motion |
|---|---|
| Loader | NSS-style wheel draws itself in SVG (ring, 8 spokes, 24 ticks, lettering), % counter, marigold flood, curtain lift |
| Hero | photo zoom-out, line-by-line title, fireflies canvas, page slides over a pinned hero |
| Whole page | Lenis smooth scrolling, hide/show nav, active link, sapling that grows with scroll (desktop), scroll progress bar (mobile) |
| Marquee | speeds up / reverses / skews with scroll velocity |
| About | curtain image reveal + parallax, words light up as you read, 3D tilt |
| Impact | rounded top flattens on scroll, count-up numbers, bars, turning wheel |
| Events | vertical scroll drives a horizontal rail with progress bar; cards straighten, photos drift; Instagram preview modal |
| Team | staggered rise, column parallax, 3D hover tilt |
| Voices | drag / arrows / active card |
| Join | steps timeline draws with scroll, parallax wheel |
| Footer | page lifts away to reveal a footer; big line rises letter by letter |

Respects `prefers-reduced-motion` (no smooth scroll, no loader, everything visible). If JavaScript fails,
the page still shows as a normal static site.

## Inaugurating the site

The site goes live through a one-time launch ceremony — a ribbon the director cuts
on screen, a stage that lifts, and the finished website revealed underneath with a
wax seal stamped over it.

It is no longer driven from a laptop. The ceremony is served from the institute VM
at **https://nss.iiitnr.ac.in**, behind a hold page with a countdown, and only a
signed-in administrator can actually cut the ribbon. Everybody else sees the same
animation and is told, plainly and permanently on screen, that it was a preview.

Everything for that lives in [`inauguration/`](inauguration/README.md) — the
FastAPI backend, the hold page, the admin portal, the deployment scripts and the
cleanup. **Start with [`inauguration/README.md`](inauguration/README.md)**; it has
the install steps, the rehearsal runbook, and the three recovery procedures.

Nothing about the ceremony touches the website itself. The permanent pages are
plain static files served by Nginx, they need no application behind them, and when
the inauguration system is finally removed the site is exactly what it is today.

**Practising:** `python inauguration/dev/rehearse.py` plays the whole thing locally
against a mock backend. It cannot change production configuration, mark the real
inauguration complete, or run the cleanup — all three are refused outright, and
everything it writes stays in `.rehearsal/`.

## Folder map

```
index.html
team.html
event-*.html
inauguration.html   the launch ceremony page (temporary)
inauguration/       the inauguration system — backend, pages, deploy, tests
css/style.css
js/main.js
vendor/   gsap, ScrollTrigger, DrawSVGPlugin, Lenis  (bundled copies)
assets/
  fonts/    Fraunces + Work Sans (variable, latin)
  images/   your hero + emblem photos go here
  events/   event-1..3.jpg go here
```

The boundary between permanent and temporary is a directory: `inauguration/` plus
`inauguration.html` and `coming-soon.html` are the launch system, and everything
else is the site. The cleanup script's allowlist names those paths literally, so
nothing outside them can be removed.

Libraries: GSAP 3.15 (free under GreenSock's standard licence), Lenis 1.3 (MIT).
