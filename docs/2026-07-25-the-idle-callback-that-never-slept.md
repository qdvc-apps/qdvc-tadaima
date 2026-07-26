# The idle callback that never slept

*A debugging story about one line of GTK code that pinned a whole CPU core — and
why, for a while, it seemed to happen for two different reasons at once.*

This document is written for someone comfortable with Python but new to GTK and
its plumbing (GLib, GObject, the "main loop"). You do not need to know those
libraries going in; the necessary pieces are explained as we meet them.

---

## 1. The symptom

QDVC Tadaima is a desktop photo browser. It behaved perfectly — until you opened
a photo in the full-screen viewer. From that moment, one CPU core sat at 100%
and stayed there, even after you navigated back to the library and did nothing
at all. Closing and reopening the app reset it; opening a photo brought it
straight back.

That "stays high after you go back, doing nothing" part is the important clue.
Idle software should use ~0% CPU. Something was *running continuously* with no
visible work to show for it.

---

## 2. A crash course in the GTK "main loop"

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

---

## 3. The rule that bites everyone once

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

---

## 4. The bug, in one line

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
from §3 mechanically:

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

---

## 5. Why it looked like a rendering problem for so long

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

---

## 6. The puzzle: why did it sometimes spike *immediately*, before the viewer?

This is the question that (rightly) nagged you, and it has a precise answer.

The real bug in §4 only arms itself **when you open the viewer** — that is the
only place `idle_add(grab_focus)` runs. So on a normal launch, sitting in the
library, CPU was correctly low. Good.

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

Read §3 again and you can see the problem instantly: a callback that returns
`True` is never removed, so it makes the loop spin at full speed — *by itself,
from the moment the app starts,* regardless of the viewer. My instrument was not
observing the fire; it was pouring on petrol.

So during that phase there were, confusingly, **two independent ways to peg the
core**:

| Situation | What was spinning | When it started |
|---|---|---|
| Normal run, after opening a photo | the real bug: `idle_add(grab_focus)` | on viewer-open |
| Any run with the old `QDVC_LOOP_DEBUG=1` | the probe's own `return True` idle | immediately, at launch |

That is why the timing seemed to make no sense: two different causes with two
different triggers were being read as one. It also explains a subtler thing you
noticed — that the debug numbers themselves looked suspicious (hundreds of
thousands of "idle dispatches per second"). They were real, but they were mostly
the probe measuring the spin *it* was causing.

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
`grab_focus` idle from §4.

There is a general moral here, well known in the sciences and worth internalising
as a programmer: **an instrument that disturbs the system it measures can
manufacture the very reading you are looking for.** When a measurement seems to
confirm your fear a little too eagerly, check that the act of measuring is not
itself the cause.

---

## 7. What to remember

1. **GTK apps sleep to stay idle.** A GTK program at rest should use ~0% CPU
   because its main loop blocks in `poll()` until something happens. Steady CPU
   with an idle screen means something is preventing that sleep.

2. **`idle_add`/`timeout_add` callbacks live or die by their return value.**
   Falsy → run once. Truthy → run forever. This is the single most common GLib
   footgun.

3. **Never pass a GTK method straight to `idle_add`/`timeout_add`.** Its return
   value is not yours to control and may be truthy. Wrap it; return `False`
   (or `GLib.SOURCE_REMOVE`).

4. **Diagnose before theorising.** "High CPU in a GUI" is not necessarily a
   drawing problem. Measure frames-per-second (is it even drawing?) and take a
   *native* stack trace (what is actually running?) before forming hypotheses.

5. **Don't let your probe join the crime.** A diagnostic that changes the
   system's behaviour — especially one that itself keeps the loop busy — can
   produce readings that look like the bug. Prefer passive measurements
   (a periodic timeout reading a counter) over intrusive ones (an always-ready
   idle callback).
