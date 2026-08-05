---
type: guide
title: How this works, and what you do
resource: docs/guides/COCKPIT-FOR-DOMAIN-EXPERTS.md
---
# How this works, and what you do

You are going to get substantial engineering work done by describing what you
want and answering questions about your own field. You will not write code, use
git, or type commands.

This page explains what the screen is telling you and what your part is. It is
short on purpose. Nothing here is something you have to memorise — if you only
remember two things, make them these:

> **Decide from the pane, not from the chat.**
> **Nothing you do can lose paid work.**

---

## Who does what

**You** decide things only you can decide: what "correct" means in your field,
which of two readings of an ambiguous requirement is right, whether a result is
good enough to accept.

**The assistant** you talk to picks the approach, starts the work, watches it,
explains what is happening, and asks you questions. It cannot approve anything —
not by choice, but because the system does not give it a way to. Approvals only
happen at a keyboard, by a person.

**The workers** are separate programs that do the engineering. They do not talk
to you. They ask questions only by stopping and putting the question on screen.

---

## The screen

You will see three areas.

**CHAT** — where you talk. This is your home. Everything you say happens here.

**ACTIVITY** — what is being worked on right now, one line at a time. It always
shows something. Blank never means broken; the line always tells you which of
"thinking", "waiting for you", or "finished" is true. Where a tool reports its
own progress you get it in full:

```
  [####------]  40%  step 2 of 5  reading the manifest
```

Where a tool cannot report progress, you get a fact rather than a guess —
`still producing output — 8.4 KB, last write 3s ago`, or, if it has gone quiet,
`no new output for 14 m`. That second one is worth mentioning in the chat.

**MISSION** — the status board, and the one part of the screen no one writes by
hand. It is generated straight from the system's own files, so it cannot flatter
or round off. When the chat and MISSION disagree, **MISSION is right** — say so
and it will be looked into.

MISSION opens with one line that tells you where things stand without reading
anything else:

> `step 3 of 8  -  running  -  14 m  -  a decision is 2 steps away`

Finished steps collapse into a count (`5 finished`) so the board stays short.
Anything running, anything that needs you, anything that went wrong, and
anything sent back for rework is always shown in full — the quiet steps are the
ones that get folded away.

Those words on MISSION mean exactly this:

| Word | Meaning |
|---|---|
| **running** | being worked on now |
| **waiting** | queued, nothing spending |
| **sent back for rework (1 of 2)** | a checker rejected the work and sent it back; it can do this at most twice |
| **needs you** | stopped for a decision or a question — your turn |
| **done** | finished |
| **stopped with a problem** | something went wrong; ask the assistant |

MISSION also shows spend, like `agent tasks used 9 of 25`. An "agent task" is one
unit of work by one worker. That is the number you agreed to before anything
started. You may see `no envelope` next to some tasks — that just means one of
the tools cannot report its own token usage. It is not an error and nothing is
wrong.

---

## The four things you actually do

### 1. Say what you want

Plain language. You do not need to know what is possible; ask for the outcome
and you will be told what it would cost.

### 2. Agree to a budget

Before anything spends you get a short card — on MISSION, not just in the chat —
that says what the work looks like and what it is allowed to cost:

```
  2 steps of work  -  1 automatic check  -  1 decision from you
  ceiling: 25 agent tasks. The run stops itself at that number.
  prior runs of this flow: 3 - they used about 11 agent tasks and 14m
```

Say yes or no. The "prior runs" line is counted from real runs on this machine,
not estimated, and if there are none it says so rather than guessing. You can
always ask for a smaller version first.

### 3. Answer questions about your field

Sometimes the work reaches a point only you can settle — which dataset, which
convention, which of two readings is intended. You will get the question in
plain language in the chat, **and** the exact words the system used will appear
on screen in the ACTIVITY area, so you can see nothing was lost in translation.
Answer in the chat, not at that card — the card is only there to be read. If the
two do not match, the card is the one that counts, and say so.

You will be asked to confirm your answer before it is used. Please take that
seriously, because:

> **Answers are effectively permanent.** Changing one later means redoing the
> work that was based on it, and paying for it again.

If you are not sure, say so. "I don't know, who would?" is a good answer.

### 4. Approve or reject the result

This is the important one, and the only place you use the keyboard for anything
but talking.

A new pane opens showing **the actual thing** — the real list of changes, the
real document, the real numbers. Not a summary of it. Read that.

Near the bottom, before the prompt, it also tells you two things that decide how
much care this needs:

```
  scale of the change: 4 files - 2 edited, 1 new, 1 DELETED
  if this turns out wrong: git checkout -- docs/ restores everything
```

If something is deleted, it is called out on its own line. If the flow cannot
say how to undo it, that line reads **`not stated by this flow`** — which is
itself worth knowing before you say yes.

Then type **`a`** to approve or **`r`** to reject, and press Enter. Those are the
only two answers the prompt takes.

- **`r` is not a failure.** Rejecting is a normal, useful answer, and costs
  nothing but the time already spent. If the pane looks wrong, or you cannot
  tell whether it is right, reject and say why.
- **After you reject you get one more question: "in one line, what was wrong?"**
  Answer it if you can — those words are written down as yours and are what the
  work gets fixed against. Press Enter to skip if you would rather just talk.
- **If the pane shows no evidence**, something is wrong with how the job was
  built. The safe answer is `r`, and say what you saw.
- **If a banner says `IRREVERSIBLE`**, this cannot be undone by the system.
  Read the pane twice. There is no rush and no penalty for rejecting.

A short note on why the pane matters: the assistant may also describe the result
in chat, and it is usually right. But it is a description. The pane is the thing
itself, and you are the only safeguard against a confident, wrong description.
That is the whole reason you are asked to look.

---

## Saying STOP

Type **STOP** in the chat at any time. Work halts, nothing new starts, and you
will be told what was spent. You do not need a reason and you will not be
argued with.

---

## Nothing is lost

Closing your laptop, closing a pane, or the assistant crashing does **not**
destroy finished work. The work and the assistant are separate; either can stop
without harming the other.

To pick up again — after a break, a crash, or overnight — **double-click
`start-cockpit.cmd`**. That is the only starting point, and it is the same one
every time. It will tell you where things stood and whether it is safe to carry
on. It works that out mechanically; it is never a guess, and never something you
have to judge.

You may see a message like *"still working"*. That means a job kept running
while your screen was off. That is normal and good.

---

## When something feels wrong

You do not need to diagnose anything. These are all complete, useful reports:

- "MISSION says needs you but I don't see a question."
- "The pane and the chat are telling me different things."
- "It's been quiet for a long time."
- "I don't understand what I'm approving."
- "This number doesn't look like what we agreed."

The last two are the most valuable things you can say. **Not understanding what
you are approving is a defect in the work, not in you** — the pane is supposed
to be readable by you, and if it is not, that gets fixed.

---

## What you never have to do

Write or read code. Use git. Type a command. Decide whether something is safe to
restart. Remember which files matter. Know what a "run directory" is. Work out
what a number means.

If you are ever asked to do one of those, something has gone wrong upstream —
say so, and it will be corrected.
