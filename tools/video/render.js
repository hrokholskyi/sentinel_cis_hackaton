#!/usr/bin/env node
// Deterministic frame-by-frame video renderer for docs/demo_preview.html.
// See README.md for the method and the command line.
'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn, fork } = require('child_process');
const { pathToFileURL } = require('url');

const REPO = path.resolve(__dirname, '..', '..');
const CLOCK_JS = fs.readFileSync(path.join(__dirname, 'virtual-clock.js'), 'utf8');

// CSS added at render time. Hides presenter-only controls and keeps the top bar balanced.
const RENDER_CSS = `
.ctrls{display:none!important}
.top{grid-template-columns:23rem 1fr 23rem!important}
#card .btn,#card .ch{display:none!important}
.scroll{scrollbar-width:none!important}
.scroll::-webkit-scrollbar{display:none!important}
*{cursor:none!important;caret-color:transparent!important}
`;
const IDLE_CAPTION = 'A guided tour and five cases. Everything here is simulated.';

function parseArgs(argv) {
  const o = {
    src: path.join(REPO, 'docs', 'demo_preview.html'),
    out: path.join(REPO, 'media'),
    work: path.join(os.tmpdir(), 'trustgap-video-work'),
    fps: 60,
    workers: 6,
    startHold: 2500,
    endHold: 3000,
    crf4k: 15,
    crf1080: 18,
    preset: 'slow',
    range: null, // [fromSec, toSec] for a test render
    png: null, // write PNG frames to this folder instead of encoding (test mode)
    name: 'trustgap_demo',
    keep: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i], v = () => argv[++i];
    if (a === '--src') o.src = path.resolve(v());
    else if (a === '--out') o.out = path.resolve(v());
    else if (a === '--work') o.work = path.resolve(v());
    else if (a === '--fps') o.fps = Number(v());
    else if (a === '--workers') o.workers = Number(v());
    else if (a === '--start-hold') o.startHold = Number(v());
    else if (a === '--end-hold') o.endHold = Number(v());
    else if (a === '--crf') o.crf4k = Number(v());
    else if (a === '--crf-1080') o.crf1080 = Number(v());
    else if (a === '--preset') o.preset = v();
    else if (a === '--range') o.range = v().split(':').map(Number);
    else if (a === '--png') o.png = path.resolve(v());
    else if (a === '--name') o.name = v();
    else if (a === '--keep') o.keep = true;
    else if (a === '--help' || a === '-h') { console.log(fs.readFileSync(path.join(__dirname, 'README.md'), 'utf8')); process.exit(0); }
    else throw new Error('unknown argument ' + a);
  }
  return o;
}

async function openPage(o) {
  const { chromium } = require('playwright-core');
  const browser = await chromium.launch({
    headless: true,
    args: ['--hide-scrollbars', '--force-color-profile=srgb', '--disable-lcd-text', '--font-render-hinting=none'],
  });
  const ctx = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,
    reducedMotion: 'no-preference',
    colorScheme: 'dark',
  });
  const cfg = { css: RENDER_CSS, idleCaption: IDLE_CAPTION, startHoldMs: o.startHold };
  await ctx.addInitScript({ content: 'window.__VC_CONFIG=' + JSON.stringify(cfg) + ';\n' + CLOCK_JS });
  // No network: the page is self-contained, block anything that is not the file itself.
  await ctx.route(/^https?:/, (r) => r.abort());
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') console.error('[page]', m.text()); });
  page.on('pageerror', (e) => console.error('[pageerror]', e.message));
  await page.goto(pathToFileURL(o.src).href, { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready.then(() => true));
  const ok = await page.evaluate(() => !!(window.__vc && window.__demo));
  if (!ok) throw new Error('virtual clock or window.__demo missing');
  return { browser, page };
}

// Finds the moment the last beat finishes, on the virtual clock.
async function probe(o) {
  const { browser, page } = await openPage(o);
  const r = await page.evaluate(() => {
    const last = window.__demo.beats() - 1;
    for (let t = 0; t < 30 * 60 * 1000; t += 10) {
      window.__vc.advance(t);
      const i = window.__demo.info();
      if (i.b === last && i.done) return { doneAt: t, beats: last + 1 };
    }
    return null;
  });
  await browser.close();
  if (!r) throw new Error('demo never finished');
  return r;
}

function ffmpeg(args, opts) {
  return spawn('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args], { stdio: ['pipe', 'inherit', 'inherit'], ...opts });
}
function waitExit(p) {
  return new Promise((res, rej) => p.on('exit', (c) => (c === 0 ? res() : rej(new Error('ffmpeg exit ' + c)))));
}

// Worker: renders frames [from, to) into one lossless RGB segment (or PNG files).
async function worker(job) {
  const o = job.o;
  const { browser, page } = await openPage(o);
  const cdp = await page.context().newCDPSession(page);
  // Emulation is per CDP session: without this, captures from this session come out at 1x.
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 2, mobile: false });
  const frameMs = (k) => (k * 1000) / o.fps;
  await page.evaluate((t) => window.__vc.advance(t), frameMs(job.from));
  let enc = null;
  if (!o.png) {
    enc = ffmpeg(['-f', 'image2pipe', '-framerate', String(o.fps), '-c:v', 'png', '-i', '-',
      '-c:v', 'libx264rgb', '-preset', 'ultrafast', '-qp', '0', '-pix_fmt', 'rgb24', '-threads', '2', job.file]);
  }
  const t0 = Date.now();
  for (let k = job.from; k < job.to; k++) {
    await page.evaluate((t) => window.__vc.advance(t), frameMs(k));
    const shot = await cdp.send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true, fromSurface: true });
    const buf = Buffer.from(shot.data, 'base64');
    if (o.png) {
      const ms = Math.round(frameMs(k));
      fs.writeFileSync(path.join(o.png, `f${String(k).padStart(6, '0')}_${ms}ms.png`), buf);
    } else if (!enc.stdin.write(buf)) {
      await new Promise((r) => enc.stdin.once('drain', r));
    }
    if (process.send && (k - job.from) % 30 === 29) process.send({ progress: k - job.from + 1 });
  }
  if (enc) { enc.stdin.end(); await waitExit(enc); }
  await browser.close();
  return { frames: job.to - job.from, ms: Date.now() - t0 };
}

async function main() {
  if (process.argv[2] === '--worker') {
    const job = JSON.parse(process.argv[3]);
    const r = await worker(job);
    process.send({ done: r });
    return;
  }
  const o = parseArgs(process.argv.slice(2));
  const T0 = Date.now();
  fs.mkdirSync(o.work, { recursive: true });
  if (o.png) fs.mkdirSync(o.png, { recursive: true });

  const p = await probe(o);
  const totalMs = p.doneAt + o.endHold;
  let from = 0, to = Math.round((totalMs * o.fps) / 1000);
  if (o.range) { from = Math.round(o.range[0] * o.fps); to = Math.min(to, Math.round(o.range[1] * o.fps)); }
  const n = to - from;
  console.log(`beats ${p.beats}, last beat done at ${(p.doneAt / 1000).toFixed(2)} s, video ${(totalMs / 1000).toFixed(2)} s`);
  console.log(`rendering frames ${from}..${to - 1} (${n} frames at ${o.fps} fps) with ${o.workers} workers`);

  const W = Math.max(1, Math.min(o.workers, n));
  const jobs = [];
  for (let w = 0; w < W; w++) {
    const a = from + Math.floor((n * w) / W), b = from + Math.floor((n * (w + 1)) / W);
    jobs.push({ o, from: a, to: b, file: path.join(o.work, `seg_${String(w).padStart(2, '0')}.mkv`) });
  }
  const progress = new Array(W).fill(0);
  const tick = setInterval(() => {
    const d = progress.reduce((x, y) => x + y, 0);
    const el = (Date.now() - T0) / 1000;
    process.stdout.write(`  ${d}/${n} frames, ${el.toFixed(0)} s elapsed\n`);
  }, 30000);
  const results = await Promise.all(jobs.map((job, w) => new Promise((res, rej) => {
    const c = fork(__filename, ['--worker', JSON.stringify(job)], { stdio: 'inherit' });
    c.on('message', (m) => { if (m.progress) progress[w] = m.progress; if (m.done) res(m.done); });
    c.on('exit', (code) => { if (code) rej(new Error(`worker ${w} exit ${code}`)); });
  })));
  clearInterval(tick);
  const tRender = (Date.now() - T0) / 1000;
  const per = results.reduce((s, r) => s + r.ms / r.frames, 0) / results.length;
  console.log(`frames done in ${tRender.toFixed(1)} s wall, ${per.toFixed(0)} ms per frame per worker, ${((tRender * 1000) / n).toFixed(1)} ms per frame overall`);
  if (o.png) return;

  const list = path.join(o.work, 'segments.txt');
  fs.writeFileSync(list, jobs.map((j) => `file '${j.file}'`).join('\n') + '\n');
  fs.mkdirSync(o.out, { recursive: true });
  const color = ['-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv'];
  const common = ['-f', 'concat', '-safe', '0', '-i', list, '-an'];
  const sw = 'accurate_rnd+full_chroma_int';
  const suffix = o.fps === 60 ? '' : `_${o.fps}fps`;
  const out4k = path.join(o.out, `${o.name}_4k${suffix}.mp4`);
  const out1080 = path.join(o.out, `${o.name}_1080p${suffix}.mp4`);
  const x264 = (crf) => ['-c:v', 'libx264', '-profile:v', 'high', '-preset', o.preset, '-crf', String(crf),
    '-pix_fmt', 'yuv420p', '-g', String(o.fps * 2), '-r', String(o.fps), '-movflags', '+faststart', ...color];
  const pts = `setpts=N/(${o.fps}*TB)`;
  const T1 = Date.now();
  const e1 = ffmpeg([...common, '-vf', `${pts},scale=flags=lanczos+${sw}:out_color_matrix=bt709:out_range=tv,format=yuv420p`, ...x264(o.crf4k), out4k]);
  const e2 = ffmpeg([...common, '-vf', `${pts},scale=1920:1080:flags=lanczos+${sw}:out_color_matrix=bt709:out_range=tv,format=yuv420p`, ...x264(o.crf1080), out1080]);
  e1.stdin.end(); e2.stdin.end();
  await Promise.all([waitExit(e1), waitExit(e2)]);
  console.log(`encoded in ${((Date.now() - T1) / 1000).toFixed(1)} s: ${out4k}, ${out1080}`);
  if (!o.keep) for (const j of jobs) fs.rmSync(j.file, { force: true });
  if (!o.keep) fs.rmSync(list, { force: true });
  console.log(`total ${((Date.now() - T0) / 1000).toFixed(1)} s`);
}

main().catch((e) => { console.error(e); process.exit(1); });
