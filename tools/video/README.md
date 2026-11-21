# Demo video renderer

Renders `docs/demo_preview.html` frame by frame into a 4K and a 1080p MP4.
The page's own auto-play is used: start card for 2.5 s, the 7 tour stops, the 5 cases, then the last frame held for 3 s.

## Re-render

```sh
cd tools/video
npm install            # once; installs playwright-core 1.64.1 (Chromium build 1248)
node render.js --work /path/to/scratch/dir
```

Output goes to `media/trustgap_demo_4k.mp4` and `media/trustgap_demo_1080p.mp4`.
The scratch dir holds lossless segments while rendering (a few GB) and is emptied at the end.
On an M1 Pro the full 60 fps run takes about 15 to 20 minutes with the default 6 workers.

Useful options:

- `--fps 30` renders half the frames; the files get a `_30fps` suffix.
- `--workers N` sets the number of parallel browser processes.
- `--range 120:128 --png DIR` writes PNG frames for a time range (seconds) instead of a video, for checking motion.
- `--crf 15`, `--crf-1080 18`, `--preset slow` control the H.264 encodes.
- `--start-hold 2500`, `--end-hold 3000` set the start card hold and the final hold in ms.
- `--keep` keeps the lossless segments.

If Playwright cannot find the browser, run `npx playwright-core install chromium-headless-shell` once.

## How time is controlled

`virtual-clock.js` is injected before the page's scripts run.
It replaces `performance.now`, `Date`, `setTimeout`, `setInterval` and `requestAnimationFrame` with one virtual clock.
The renderer moves that clock to the exact time of each frame (`frame / fps`), firing due timers in order.
Before and after every timer callback, all CSS transitions and keyframe animations from `document.getAnimations()` are paused and given `currentTime = now - start`, where `start` is the virtual time at which the animation first appeared.
So CSS motion follows the same clock as the page's JavaScript, and every frame is an exact, repeatable rendering of that moment.
Frames are captured with CDP `Page.captureScreenshot` at 1920x1080 CSS pixels and device scale factor 2.
Each worker renders a contiguous range of frames after fast-forwarding the clock from 0, so all workers see identical state; frames are byte-identical whichever worker renders them.

The script also injects render-only CSS and two small tweaks (see `RENDER_CSS` in `render.js` and `virtual-clock.js`):

- hides the Play, Back, Next, Restart and CC buttons and the start card's button and keyboard hint, and balances the top bar so the stepper stays centred;
- hides scrollbars and the cursor;
- replaces the idle caption "Press Start to begin ..." with "A guided tour and five cases. Everything here is simulated.";
- settles the fade-in transitions of the very first render, so frame 0 shows the finished start card;
- clicks Play after the start hold.

If the page is restyled, check these selectors still match (`.ctrls`, `.top`, `#card .btn`, `#card .ch`, `#bPlay`, `#caption`, `window.__demo`).
