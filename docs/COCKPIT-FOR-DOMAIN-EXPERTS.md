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
shows something. If it says `working — 4 m elapsed`, work is happening. Blank
never means broken; the line always tells you which of "thinking", "waiting for
you", or "finished" is true.

**MISSION** — the status board, and the one part of the screen no one writes by
hand. It is generated straight from the system's own files, so it cannot flatter
or round off. When the chat and MISSION disagree, **MISSION is right** — say so
and it will be looked into.

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

Before anything spends, you will get a sentence like *"up to 25 agent tasks —
shall I start?"* Say yes or no. You can ask what a task costs, and for anything
larger you can ask for a smaller version first.

### 3. Answer questions about your field

Sometimes the work reaches a point only you can settle — which dataset, which
convention, which of two readings is intended. You will get the question in
plain language **and** the exact words the system used, so you can see nothing
was lost in translation.

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

Then type **`a`** to approve or **`r`** to reject, and press Enter.

- **Never type `e`.** If anything unexpected appears, copy it into the chat.
- **`r` is not a failure.** Rejecting is a normal, useful answer, and costs
  nothing but the time already spent. If the pane looks wrong, or you cannot
  tell whether it is right, reject and say why.
- **If the pane shows no evidence**, something is wrong with how the job was
  built. The safe answer is `r`, and say what you saw.

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
