# The idle callback that never slept

*A debugging story about one line of GTK code that pinned a whole CPU core — and
why, for a while, it seemed to happen for two different reasons at once.*

This document is written for someone comfortable with Python but new to GTK and
its plumbing (GLib, GObject, the "main loop"). You do not need to know those
libraries going in; the necessary pieces are explained as we meet them in
[The full story](#the-full-story) below. The three sections before it — key
lessons, the blow-by-blow isolation record, and a "cursed knowledge" list — are
the distilled takeaways.

---

## Key lessons learnt

These are written to travel: they apply to any long-running program — a data
pipeline, a web service, a daemon, a scheduled job — and to debugging in general,
not just the GTK app they came from.

1. **"Busy" and "doing work" are not the same thing. Measure the difference.**
   A process at 100% CPU is not necessarily *making progress*; it may be
   spinning uselessly. The most clarifying question in any high-CPU
   investigation is "is it actually *accomplishing* anything while it burns that
   core?" So measure a unit of *useful output* — rows processed, requests
   served, frames drawn — alongside raw CPU. When consumption is high but output
   is zero, you have a spin, and you have just ruled out every explanation that
   assumed real work was happening.

2. **Prefer diagnostics that eliminate whole categories.** Not every
   measurement is equally worth taking. The valuable ones cut the space of
   possible causes in half (or to nothing) in a single shot, rather than
   confirming one suspect at a time. Here, "it is consuming a core but drawing
   nothing" killed *every* rendering explanation at once — worth far more than
   testing each visual feature individually. When you design a test, ask "which
   answer rules out the most?", not "which answer matches my current hunch?"

3. **Make sure your tools can see the layer the bug lives in.** Investigative
   tools each observe one stratum of a system — application code, a runtime, a
   library, the OS, the network, the hardware — and are blind to the others. A
   tool aimed at the wrong layer returns answers that are technically true and
   completely useless ("it's in the main loop"; "the query returned"; "the
   request was sent"). When a result is suspiciously vague, that vagueness is
   often the tool telling you the action is happening one level down; switch to
   an instrument that sees that level before you theorise further.

4. **Your measuring tools are part of the system; account for their footprint.**
   A probe that changes what it observes can manufacture the very reading you
   fear — so treat every instrument as a suspect until you have checked it
   against a known-healthy baseline.

5. **A value returned to a framework can be a control signal, not just data.**
   In many systems — schedulers, event loops, iterators, comparators, retry and
   plugin hooks, even HTTP status handlers — what your function *returns* steers
   the caller's behaviour, sometimes invisibly. Before handing a function you
   did not write (a library method, a bound method) to such a framework, check
   what it returns *and* what the framework does with that return value: a value
   that is perfectly sensible for the function's own purpose can mean something
   entirely different to the code calling it.

> A direct, honest answer to a question that came up — *did I know the specific
> gotcha behind this bug before starting?* In the abstract, yes: the rule
> involved (covered in [the full story](#the-full-story)) is well documented and
> I could have recited it. But knowing a rule and *noticing you have just
> violated it* are different skills. The offending line read so naturally that
> the question never surfaced while writing it. That gap — between textbook
> knowledge and situational recognition — is exactly why lessons 1–4 (measure
> output, eliminate categories, aim your tools, distrust a noisy instrument)
> matter more than accumulating trivia: they catch the mistakes your knowledge
> failed to prevent.

---

## Isolation testing record

A timeline of the debugging, each entry linking to the exact point in the
[full conversation transcript](../vibe-coding/2026-07-25-claude.md). Read top to
bottom, it is a case study in how a wrong hypothesis (rendering) survived far
too long, and how narrowing measurements eventually forced the truth out.

1. **Initial report** _([33. User request](../vibe-coding/2026-07-25-claude.md#33-user-request)):_ The app sits at ~13% CPU even after all thumbnails are generated. *Next:* added passive diagnostics — a frame-rate monitor and a pointer to GTK's interactive inspector.

2. **First diagnostics come back empty** _([35. User request](../vibe-coding/2026-07-25-claude.md#35-user-request)):_ Neither the frame monitor nor `GTK_DEBUG=interactive` shows anything obvious, but `btop` shows some I/O and 25 threads. *Next:* explained that the I/O figures were cumulative (not a live rate) and 25 threads is normal for GTK4; fixed a genuine but unrelated config-write-on-every-event inefficiency.

3. **The decisive observation** _([37. User request](../vibe-coding/2026-07-25-claude.md#37-user-request)):_ CPU is mild (1–5%) browsing the library, but jumps to 12–13% the moment a photo is opened in the viewer — **and stays there** after returning. *Next:* theorised the viewer's full-size image was being re-scaled every frame; switched it to an immutable GPU texture and cleared it on the way out.

4. **That fix does nothing** _([39. User request](../vibe-coding/2026-07-25-claude.md#39-user-request)):_ The spike persists even with `GSK_RENDERER=cairo` (ruling out the GL driver). The user proposes a `--no-filmstrip` switch to isolate the filmstrip. *Next:* added the switch.

5. **Filmstrip exonerated** _([41. User request](../vibe-coding/2026-07-25-claude.md#41-user-request)):_ `--no-filmstrip` changes nothing. *Next:* made the frame monitor report only when actually painting, and added a main-thread stack sampler.

6. **Frame + stack output** _([43. User request](../vibe-coding/2026-07-25-claude.md#43-user-request)):_ The crucial clue that the continuous repaints (when they happened at all) were on the *gallery* page, not the viewer. *Next:* added switches to disable thumbnail shadows and to render thumbnails as plain images.

7. **A Python-only py-spy capture** _([45. User request](../vibe-coding/2026-07-25-claude.md#45-user-request)):_ 100% of time in `Gio.Application.run` with 0% in app Python. Interpreted (correctly) as "the cost is in C, not our code" — but the plain profiler could not say *which* C. *Next:* kept chasing rendering hypotheses.

8. **The pushback that turned the case** _([47. User request](../vibe-coding/2026-07-25-claude.md#47-user-request)):_ Shadows and plain-thumbnails switches change nothing; the user notes frame logs are silent when idle yet CPU stays high, and asks the key question — is this even UI-related? Could we have a switch to disable *all* background I/O? *Next:* conceded rendering was likely a dead end; added `QDVC_NO_BACKGROUND` and a main-loop probe.

9. **The probe screams** _([49. User request](../vibe-coding/2026-07-25-claude.md#49-user-request)):_ With background work disabled, CPU still spikes, and the loop probe reports the main loop dispatching an idle **~430,000×/sec**. *Next:* (mistakenly) read this as a stuck animation transition and disabled the page crossfade. **This probe was itself flawed — see cursed knowledge.**

10. **Crossfade removed, still spinning** _([51. User request](../vibe-coding/2026-07-25-claude.md#51-user-request)):_ The user reports it now clearly (thank you) as **100% of a single core**, and that with the probe enabled it spikes *immediately at launch*, not just on viewer-open. *Next:* began suspecting the probe itself.

11. **Confirmed independent of animation** _([53. User request](../vibe-coding/2026-07-25-claude.md#53-user-request)):_ No "transition RUNNING" tag ever appears; both animation theories die. *Next:* rewrote the probe to be **passive** — a once-per-second timeout reading CPU time and real paint count, incapable of spinning the loop itself.

12. **The honest instrument speaks** _([55. User request](../vibe-coding/2026-07-25-claude.md#55-user-request)):_ `CPU 100% of one core, 0 paints/sec` — a busy loop that draws nothing — plus a **native** `py-spy dump --native` showing the hot stack as `g_main_context_iteration → libffi → gi/_gi → gi_callable_info_*`: a Python callback dispatched by the main loop, over and over.

13. **Root cause** _([56. LLM response](../vibe-coding/2026-07-25-claude.md#56-llm-response)):_ `GLib.idle_add(self._pic_area.grab_focus)` in the viewer-open path. `grab_focus` returns `True`; `idle_add` re-runs any truthy callback; the idle source re-armed forever. Fixed by wrapping it to return `False`.

---

## Cursed knowledge

In the spirit of [immich's cursed knowledge](https://immich.app/cursed-knowledge):
things we now know and rather wish we didn't.

- **The one-line infinite loop:** `GLib.idle_add(widget.grab_focus)` fires forever and pins a CPU core at 100%. `grab_focus()` returns `True` on success, and GLib re-runs any idle callback that returns truthy. The fix is `return False`. The bug is invisible at the call site because the offending return value belongs to a method you didn't write.

- **Busy, blind, and idle all at once:** A GTK app doing *nothing* can burn a full core, draw zero pixels, and show 0% in every Python profiler simultaneously. All three readings are consistent and all three are true: the work is real, it's just happening in C inside the GLib main loop, invisible to Python-only tooling.

- **The thermometer had a fever:** Our own CPU probe caused the CPU problem. The first version used a self-re-arming idle callback (`return True`) to "sample" the loop — which is precisely the thing that spins the loop. For a while we were, in effect, measuring our own thermometer's body heat. Passive probes (a periodic timeout reading a counter) don't have this problem; some questions still need active probes, so the real rule is *characterise your probe against a known-good baseline*.

- **btop's I/O columns lie about "now":** `btop`'s per-process IO/R and IO/W columns are cumulative totals since the process started, not live rates. A few hundred KiB sitting there is not "ongoing I/O"; it's the running total, holding still. We briefly chased phantom disk activity because of this.

- **Twenty-five threads is fine, actually:** ~25 threads for an idle GTK4 app is normal, not a leak. The GL/render pool, the GIO worker pool and GLib workers are mostly parked. Thread *count* is not thread *activity*.

- **"100% CPU" is a trick question:** "100% CPU" from a system monitor may mean 100% of *one core* on a many-core machine (here ~12.5% of eight). The wording matters: a single pegged core is the fingerprint of a single-threaded busy loop, which is a very different search than "the whole machine is loaded".

---

## The full story

The narrative walkthrough, from symptom to fix, with the concepts explained as
they arise.

### 1. The symptom

QDVC Tadaima is a desktop photo browser. It behaved perfectly — until you opened
a photo in the full-screen viewer. From that moment, one CPU core sat at 100%
and stayed there, even after you navigated back to the library and did nothing
at all. Closing and reopening the app reset it; opening a photo brought it
straight back.

That "stays high after you go back, doing nothing" part is the important clue.
Idle software should use ~0% CPU. Something was *running continuously* with no
visible work to show for it.

### 2. A crash course in the GTK "main loop"

A desktop application spends almost all of its life waiting. Waiting for you to
move the mouse, press a key, resize a window. The mechanism that does this
waiting is called the **main loop**, and in GTK it comes from a library called
GLib. Conceptually it is this:

```python
while app_is_running:
    event = wait_for_something_to_happen()   # sleeps here, using no CPU
    dispatch(event)                          # handle it, then loop again
```

The crucial word is **sleeps**. When nothing is happening, `wait_for_something_
to_happen()` genuinely blocks — the operating system parks the process and gives
the CPU to someone else. A healthy idle GTK app therefore uses no CPU at all.
(Under the hood that blocking call is a system call named `poll()`, which is
where our profiler will eventually point us.)

You can register three kinds of "please call my function" requests with the loop:

- **event handlers** — "call me when the user clicks this button";
- **timeouts** — "call me every 500 milliseconds";
- **idle callbacks** — "call me as soon as you have a spare moment."

This story is about that third kind, and about one small, easy-to-miss rule
that governs all of them.

### 3. The rule that bites everyone once

When you give GLib a function to call, GLib needs to know afterwards: *should I
keep this registration, or throw it away?* It decides based on **what your
function returns**:

- return a **falsy** value (`False`, or `None` because your function just ends)
  → GLib removes the callback. It runs **once**.
- return a **truthy** value (`True`) → GLib **keeps** the callback and will call
  it again.

This is deliberate and useful. A repeating timeout returns `True` to keep
ticking; a one-shot returns `False` to fire once and vanish. GLib even provides
named constants, `GLib.SOURCE_CONTINUE` (`True`) and `GLib.SOURCE_REMOVE`
(`False`), precisely because the bare booleans are easy to get wrong.

Now hold that rule in mind while we look at the actual line of code that caused
all the trouble.

### 4. The bug, in one line

When you open a photo, the app wants keyboard focus to land on the image (so the
arrow keys page through photos) rather than on the "Back" button. Moving focus
right in the middle of building a new screen can be unreliable, so a common
trick is to defer it by a hair: "GLib, once you have a spare moment, put the
focus on the picture." That was written as:

```python
GLib.idle_add(self._pic_area.grab_focus)
```

`idle_add` is "call this at the next spare moment." `self._pic_area.grab_focus`
is the function to call. Looks innocent. It is not.

Here is the trap. `grab_focus` is a GTK method, and **it returns a value**: `True`
if it successfully moved the focus. We did not write that return value — it comes
from deep inside GTK — but GLib does not care who wrote it. GLib applies the rule
from [§3](#3-the-rule-that-bites-everyone-once) mechanically:

> The callback returned `True`, so keep it and call it again.

So the sequence became:

1. Loop has a spare moment → calls `grab_focus`.
2. `grab_focus` moves focus, returns `True`.
3. GLib sees `True` and thinks "keep this idle callback."
4. An idle callback is now permanently "ready to run," so the loop **never has
   nothing to do**. It calls `grab_focus` again immediately.
5. Go to 2, forever.

That is a `while True:` loop wearing a disguise. Nobody wrote a loop; the loop
*emerged* from one method's return value colliding with one library's rule. The
focus gets "grabbed" hundreds of thousands of times per second, achieving
nothing after the first time, and the core pegs at 100%.

The fix is to make our intent explicit — run once — by wrapping the call so the
callback returns `False`:

```python
def _focus_pic_once():
    self._pic_area.grab_focus()
    return False          # tell GLib: you are done, remove me

GLib.idle_add(_focus_pic_once)
```

One `return False`. That is the entire fix.

> **Takeaway for your own code:** never hand a GTK/GObject method *directly* to
> `GLib.idle_add` or `GLib.timeout_add`. Many such methods return a truthy value
> for their own reasons, and that value will silently re-arm the callback. Always
> wrap it in a small function that returns `False` (or `GLib.SOURCE_REMOVE`).

### 5. Why it looked like a rendering problem for so long

Worth a brief, honest detour, because the *wrong* turns are instructive.

The symptom — high CPU in a GUI app — screams "it's redrawing the screen too
much." We spent several rounds chasing that: the image widget, a fade animation
between pages, drop shadows on thumbnails, the little filmstrip of photos. Each
was a plausible way to make GTK repaint continuously, and each was wrong.

What finally cut through it was measuring two things instead of guessing:

- **Frames per second.** We had the app report how often it actually repainted.
  The answer while the CPU was pinned: **zero**. A busy loop that draws nothing
  cannot be a rendering problem. That one number retired every visual theory.
- **A native stack trace.** An ordinary Python profiler only sees Python code,
  and it reported the program was "inside the main loop" — technically true but
  useless, like being told a stuck car is "on the road." A *native* profiler
  (`py-spy --native`) unwinds the underlying C libraries too, and it pointed at
  a callback being dispatched from the main loop through GObject's function-call
  bridge. That is the fingerprint of "the loop keeps calling a registered
  function," which sent us straight to the `idle_add` calls — and to the one
  passing a method that returned `True`.

The lesson: when CPU is high, **measure whether it is drawing (frames) and
measure the native stack, before theorising about causes.** Two cheap
measurements would have saved most of the investigation.

### 6. The two profilers, and why only one could see the bug

It is worth being concrete about why our first profiler capture was unhelpful
and the second was decisive, because the distinction recurs everywhere.

A Python program that uses GTK is really two programs stacked: your Python on
top, and a tall pile of C libraries (GTK, GLib, GObject, GDK, the graphics
driver) underneath. A **Python-only** profiler samples the Python call stack. If
the interesting activity is down in C — as almost all "the framework is doing
something" bugs are — it sees only the last Python frame before the code
descended into C, which for a GUI app is always the same uninformative line:
"we called the main loop." Every such bug looks identical through that lens.

A **native** profiler samples the real machine-level stack, C frames included.
That is what turned `Gio.Application.run` (useless) into
`g_main_context_iteration → libffi → gi/_gi → gi_callable_info_*` (the answer):
the loop was repeatedly crossing the bridge from C back into a Python callback.
That bridge — `libffi` and the `gi` layer — is the machinery
[PyGObject](https://pygobject.gnome.org/) uses to let Python and C call each
other; seeing it hot, on every sample, in a loop, is the signature of exactly
our bug.

### 7. The fix, and the audit around it

The one-line fix is in [§4](#4-the-bug-in-one-line). But finding one instance of
a mistake should always prompt the question "are there others?" So every other
`GLib.idle_add`/`timeout_add` in the code was checked: did each callback return
a falsy value (run once) or could any of them return truthy (run forever)? All
of the app's own callbacks already returned `False`/`None`. The only offender
was the single case of passing an external method — `grab_focus` — whose return
value we didn't control. That is why the generalised rule ("never pass a foreign
method straight to a scheduler") is more valuable than the specific fix: it is
the class of bug, not the instance.

### 8. The puzzle: why did it sometimes spike *immediately*, before the viewer?

This is the question that (rightly) nagged the developer, and it has a precise
answer.

The real bug in [§4](#4-the-bug-in-one-line) only arms itself **when you open
the viewer** — that is the only place `idle_add(grab_focus)` runs. So on a normal
launch, sitting in the library, CPU was correctly low. Good.

But during debugging we ran the app with a switch, `QDVC_LOOP_DEBUG=1`, that was
supposed to *measure* how busy the main loop was. And with that switch on, the
CPU pegged at 100% **immediately at launch, before touching the viewer.** That
seems to contradict everything above.

The resolution: **that early version of the measuring tool had the exact same
bug it was trying to detect.** To sample how often the loop was idle, the first
version of the probe registered an idle callback that deliberately returned
`True` so it would keep running:

```python
def idle_counter():
    self._loop_ticks += 1
    return True          # re-arm on purpose, to "sample" the loop
GLib.idle_add(idle_counter, priority=GLib.PRIORITY_DEFAULT_IDLE)
```

Read [§3](#3-the-rule-that-bites-everyone-once) again and you can see the problem
instantly: a callback that returns `True` is never removed, so it makes the loop
spin at full speed — *by itself, from the moment the app starts,* regardless of
the viewer. The instrument was not observing the fire; it was pouring on petrol.

So during that phase there were, confusingly, **two independent ways to peg the
core**:

| Situation | What was spinning | When it started |
|---|---|---|
| Normal run, after opening a photo | the real bug: `idle_add(grab_focus)` | on viewer-open |
| Any run with the old `QDVC_LOOP_DEBUG=1` | the probe's own `return True` idle | immediately, at launch |

That is why the timing seemed to make no sense: two different causes with two
different triggers were being read as one. It also explains a subtler thing the
developer noticed — that the debug numbers themselves looked suspicious (hundreds
of thousands of "idle dispatches per second"). They were real, but they were
mostly the probe measuring the spin *it* was causing.

The measuring tool was rewritten to be honest. Instead of registering an idle
callback (which perturbs the very thing it measures), the new version uses a
plain once-per-second timeout and simply reads how much CPU time the process has
consumed in the last wall-clock second, plus how many real repaints happened:

```
[tadaima][loop] CPU 100% of one core, 0 paints/sec   ← busy loop, not drawing
[tadaima][loop] CPU   0% of one core, 0 paints/sec   ← healthy idle
```

A timeout that fires once a second and returns quickly does not keep the loop
awake, so it can watch the patient without being the disease. With that honest
instrument, the picture finally matched reality: idle in the library, 100% with
0 paints after opening a photo — a non-drawing busy loop — which is exactly the
`grab_focus` idle from [§4](#4-the-bug-in-one-line).

A caveat, since this is meant to teach and not to preach: the tidy conclusion
"passive probes good, active probes bad" is too strong. Plenty of real problems
can *only* be caught by active instrumentation — you sometimes must inject load,
fire synthetic events, or hold a resource to reproduce a fault. The defensible
version of the lesson is narrower: **you cannot always know in advance that a
probe perturbs the system, so treat every instrument as a suspect until you've
checked its reading against a known-healthy baseline.** Had the probe been run
once against an app known to be idle, its absurd "430,000/sec even at rest"
would have outed it immediately. The fix is not "avoid active probes"; it is
"calibrate your probe on a control."

### 9. What to remember

The generalised version of these now lives up top in
[Key lessons learnt](#key-lessons-learnt); the GTK-specific essentials are:

1. **A GTK app at rest should use ~0% CPU**, because its main loop blocks in
   `poll()` until something happens. Steady CPU on an idle screen means
   something is preventing that sleep.

2. **`idle_add`/`timeout_add` callbacks live or die by their return value.**
   Falsy → run once. Truthy → run forever. This is the single most common GLib
   footgun.

3. **Never pass a GTK method straight to `idle_add`/`timeout_add`.** Its return
   value is not yours to control and may be truthy. Wrap it; return `False`
   (or `GLib.SOURCE_REMOVE`).
