# Critique protocol — would you actually send it?

Tiers 1-3 tell you the video is well-formed, matches its spec, and can be read frame by
frame. This tier decides whether it earns its runtime, and it cannot be scripted.

**Watch it once at full speed, then stop and open the contact sheet.** The first pass is the
only time you will ever see it as your audience does; spend it watching, not analysing. Every
question below is easier against the grid than against playback.

Remember what you are simulating: **the viewer cannot scroll back.** In Slack's muted
autoplay they will miss things silently, at speed, with no way to tell you they did.

---

## Pass 1 — One pass, no sound, no context

The realistic condition: muted autoplay in a feed, no email body, no preamble.

1. **Do you know what this is about within five seconds?** The hook resolves in the first
   beat or the scroll already happened. `copy_gate.py` warns past 5s.
2. **Does it work with the sound off?** If narration carries a load the captions do not, most
   of your audience gets a different video from the one you made.
3. **Did any beat pass before you finished reading it?** Look for it on the sheet: more than
   ~6 headline words, or a diagram plus a headline plus a sub in one cell.
4. **Can you state the ask?** One ask. If you can name two, the viewer will act on neither.
5. **What do you remember?** Write it down before looking again. If what you remember is not
   what the video was for, the emphasis is wrong regardless of how it looks.

The most common failure here is a video that is completely clear *to its author*, who has
the spec in their head and cannot un-know it.

## Pass 2 — Does it earn its runtime?

Say the spine out loud in one sentence: *"Today X, which costs Y, so we propose Z — here it
is running, here is what it is worth, do W."*

- **Which beat could you delete without losing the argument?** There is almost always one.
  Delete it and watch again; if nothing is missing, it stays deleted.
- **Does any beat need the word "and" to describe it?** That is two beats.
- **Is there a beat that only restates the previous one?** Adjacent near-identical cells on
  the sheet are the tell — either the beat is held too long or it is a duplicate.
- **Does the middle sag?** Openings and endings get attention; the middle is where dead time
  hides.
- **Would you cut it to 30 seconds if forced?** Name what you would cut. Then ask why it is
  still in.

**Cut before you polish.** The most common structural failure in this lab's history is a
two-minute video that should have been forty seconds — see anti-pattern 12.

## Pass 3 — Frame-by-frame, against the sheet

The defects here are invisible at playback speed because attention is on the motion.

| Look for | Usual cause |
|---|---|
| Type colliding with type, or with a diagram edge | a long headline at a small viewport |
| A cell that is nearly empty | a beat mid-transition, or motion that resolves too late |
| A cell that is a wall of text | a deck slide pasted into a video |
| A number that looks rounder than the evidence | `Math.round()` on a measured figure (#3) |
| `Your Brand`, or a missing logo | brand preset with no `logoLeftFile` (#7) |
| A guide outline | `--guides` burned into the pixels (#8) |
| Inconsistent colour between beats | a scene type not using the brand tokens |
| A product surface that implies a run happened | the `snowsight` seam (#4) |
| Anything an outsider should not read | internal vocabulary (#11) |

**On the last two, be specific rather than comfortable.** Depict the surface; do not imply
the execution. If a figure came from a demo or a labelled dataset, caption it *on the scene* —
the email is not what gets forwarded.

## Pass 4 — Honesty of the claim

Separate from craft, and the only pass where being wrong has consequences outside the video.

- **Can every number on screen be sourced?** Name the source for each, out loud.
- **Is a modelled or illustrative figure labelled as one, on the frame?**
- **Does a range get presented as a point estimate?** `$150–300K` is honest; `$300K` is not
  the same claim.
- **Does the visual imply a measurement the data does not support?** A count-up implies
  precision; a full meter implies a cap was reached.
- **Would you be comfortable if the subject of the video watched it with you?** If a beat
  only works when they are not in the room, cut it.

## Pass 5 — Multi-perspective

For anything going outside. Run each as a separate pass; the value is in the disagreement,
and cross-perspective agreement is the strongest priority signal.

| Perspective | Asks |
|---|---|
| **Cold viewer in a feed** | What is this? Why should I care? What do you want? |
| **The subject** | Is this how I would describe my own situation? Is anything overstated? |
| **The sceptic** | Which claim is weakest, and which number would I challenge first? |
| **The editor** | Which beat goes, and what does the video lose? |
| **The engineer** | Does this render deterministically, and can it be regenerated from the spec? |

Then synthesise: what to protect (name it, so the next edit does not destroy it), what is
weak, and cut/fix/add in priority order. **Verify each claimed defect by reproducing it** —
a critique is a hypothesis, including yours.

---

## Deciding what to change

- **Cut first.** Removing a beat is nearly always cheaper and better than fixing one.
- **Fix the spec, never the output.** Anything you hand-fix in a rendered file is something
  you will hand-fix forever. If it cannot be regenerated, it is not done.
- **One change at a time, then re-run the whole gauntlet.** A copy fix routinely changes
  runtime, which invalidates the `--expect-sec` you were checking against.
- **Re-render should differ only by the change.** Seed anything random; otherwise you cannot
  tell what else moved.
- **Stop when critique produces preferences rather than causes.** "I would have used a
  different green" is not a finding.

## What not to do

- Do not declare it done because it rendered. That is tier 1 of four.
- Do not treat a green gauntlet as good. It means well-formed, full-length, correctly
  encoded and audible — none of which is the same as worth sending.
- Do not review by watching it once (#13). You will catch none of Pass 3.
- Do not judge loudness by listening, or delivery by opening it in a local player. Both are
  measured, and both have already shipped broken.
- Do not add motion or music to rescue a video whose argument is not working. The problem is
  the argument.
- Do not trust your own read after the fifth viewing. You have learned it; the viewer has
  not.
