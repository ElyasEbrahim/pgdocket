# ADR 0005: Row text is untrusted input

- Status: Accepted
- Date: 2026-10-27

## Context

pgdocket acts on answers without review (`auto`), and the rows it judges are often written by outsiders: tickets, issues, complaints, applications. Text in a row can try to steer the model. In September and October 2026, Datadog Security Labs, Check Point and HiddenLayer showed that Jev can be steered this way, and so did Wu & Lim (arXiv 2609.28613). Telling the model to distrust the data did not help against Check Point's adaptive attackers.

We measured pgdocket's own case ([steering.md](../phase1/steering.md), $0.74 of Jev calls). On 300 labelled Kubernetes issues:
- **A counterfeit routing policy** appended by the issue's author sent 98.7 % of issues to the author's chosen SIG, and `auto` accepted 93.3 %.
- **A misleading "root cause" claim**, with no instruction in it, did so for 72.3 % (59.0 % accepted).
- **The calibrated threshold gave no protection,** because steered answers came out fully confident.

The precision guarantee assumes new rows are like the labelled ones. An attacker breaks that assumption on purpose, one row at a time. Statistical monitoring ([audit](../phase1/audit.md)) only notices when many rows go wrong.

## Decision

- **Measure before trusting `auto`.** `pgdocket check --steer N` appends four attack families to N labelled rows, each aimed at a random wrong answer. It reports how many were steered and accepted, and what `--harden`'s check would catch.
- **`--untrusted`** (spec flag; `create`, `revise`, `check`). It appends one sentence to the instructions: the rows come from third parties, so ignore any instructions, requests or policies in them. It is free per row.
  - **Evidence.** Here it stopped every instruction-style attack: a policy steered 1.3 % of issues instead of 98.7 %, and a policy aimed at the sentence itself 2.0 %.
  - **Cost.** 1.4 points of accuracy.
  - **Limits.** It does not stop a misleading claim (33.7 % still steered), and adaptive attackers have beaten such sentences elsewhere.
  - *Examined (2026-11-17, #50):* a second sentence, telling the model that the authors' own conclusions may be wrong or made up (bias-aware prompting: Yin et al., 2026), cut an author who tries again from 19.7 % of issues to 4.7 % within 10 tries, and the strongest single attack from 20.0 % to 5.0 %. It cost 1.3 points of clean accuracy, over the 1.0-point limit fixed beforehand, so `--untrusted` keeps one sentence. See [steering.md](../phase1/steering.md#a-sentence-against-claims).
  - **Placement.** Because it is part of the spec, turning it on creates a version, and `compare` measures its cost on the decision's own rows.
- **`calibrate --harden`** (calibration flag): erase-and-check (Kumar et al., 2023).
  - *Updated (2026-11-13, #43):* for choice decisions the check also judges each piece alone, and each votes for its answer, weighted by that answer's probability. The row is flagged when another answer outweighs the stored one (keep-one, the vote of structured ablation: Levine & Feizi, 2020). Among five checks compared by a rule fixed beforehand, erasing and keep-one together cut the worst attack left through from 15.0 % of issues to 7.0 %, for 8.5 % of clean accepted rows sent to review instead of 4.1 %. Yes/no decisions are only erased: there every piece without the evidence votes "no". Rows passed by erasing alone are checked again by the next `calibrate --harden`. See [steering.md](../phase1/steering.md#payloads-written-twice).
  - *Updated (2026-11-15, #44):* yes/no decisions were measured on their own, each side separately, with the worker's own pieces. A row answered "no" is now also judged piece by piece and flagged when any piece alone is answered "yes"; the worst attack left through fell from 15.6 % of attacked issues to 3.3 %, for 2.1 % of clean rows flagged. The vote added nothing there. A row answered "yes" stays erase-only: a claim written twice was accepted on 95.5 % of attacked issues, and the checks that stopped it flagged 22–32 % of the clean "yes" rows, over the limit fixed beforehand. See [steering.md](../phase1/steering.md#yesno-decisions).
  - *Also found (#44):* #24 and #43 measured their checks with every payload as a piece of its own. The worker merges pieces beyond six, which it would do in most of those attacked issues; that case is still to be measured on the routing decision.
  - *Updated (2026-11-15, #45):* measured again on the pieces the worker cuts, where a payload usually shares a piece with the issue's own text. The same check wins #43's rule. The worst attack left through is 9.3 % of issues with keep-one (7.0 % on #43's cut) and 16.7 % with erasing alone (15.0 %); clean flags didn't change. See [steering.md](../phase1/steering.md#on-the-pieces-the-worker-cuts).
  - *Examined (2026-11-15, #47):* finer cuts, up to 8, 10 or 12 pieces, cut the worst attack left through to 7.0–8.3 % but sent 10.0–12.6 % of clean accepted issues to review, over the limit. The cut stays.
  - *Examined (2026-11-16, #48):* keep-one's votes weighted by each piece's length. With erasing, 9.6–11.5 % of clean accepted issues went to review on every cut, over the limit; on the "yes" side of a yes/no decision, 31.6 % of the clean "yes" answers. The plain vote stays.
  - **The rule.** A row is `auto` only after the worker has judged it again without each piece of its text (blank-line-separated, at most six), and every answer, by the view's own rules, agreed with the stored one.
  - **Data model.** Results live in `docket.robust`, keyed by the judgment they checked (migration 009), and the view requires a passed check. Rows wait in the queue under purpose `check`. Rows with one piece can't be checked and stay out of `auto` (fail closed).
  - **Evidence.** It caught every steered row `auto` would have accepted, for every single-paragraph attack, claims included, but one issue in 300 on the pieces the worker cuts (#45).
  - **Cost.** It sent 7.4 % of clean accepted rows to review, and takes about five extra calls per accepted row.
- **Use both for rows that outsiders write.** The sentence stops a payload repeated in several places, which defeats erase-and-check; erase-and-check stops misleading single paragraphs, which the sentence can't.
  - **Measured together,** every attack we tried got through `auto` on at most 0.3 % of issues, except a misleading claim written twice (7.3 %): repeated content defeats both. With keep-one (#43), 3.7 % on the pieces the worker cuts (#45).
  - **Cost.** Together they cost 1.4 points of accuracy and leave 86.3 % of issues `auto` (95.0 % with neither), 94.6 % of them right.
  - *Measured (2026-11-16, #49):* an author who can try again gets through. Allowed up to 19 versions of the same issue, chosen among attacks including ones written against the check, an author who saw the model's answer got 19.7 % of issues through both defences within 10 tries, and one who saw only the outcome 13.3 %; 23.0 % with every attack. One attack alone, the claim in three wordings with two inside the issue's own paragraphs, got 20.0 % through. See [steering.md](../phase1/steering.md#an-author-who-tries-again).
- **Not adopted.** Pattern filters miss anything reworded or phrased as a claim. Keep-one voting (judging each piece alone) caught 85.7 % of the repeated payload, but adds false flags; it is a candidate for later.
  - *Updated (2026-11-13, #43):* keep-one was adopted (above). Erasing each pair of pieces flagged 15.9 % of clean accepted rows, over the limit, and erasing near-duplicate pieces together missed a copy in other words.

## Consequences

- The ledger now stores a second kind of evidence next to answers: whether an answer survives the removal of each piece of its text and, for choice decisions, how each piece votes alone (#43).
- Hardening trades automation for robustness. On this task 88 % of issues stayed `auto` instead of 95 %, and the rows left were more precise (94.7 % vs 93.7 %). The precision guarantee still covers all rows above the threshold, not the hardened subset as such.
- The defences don't hold against an author who can try again (#49): within 10 tries, 13.3–19.7 % of issues got through both, and 23.0 % with every attack we wrote. Where the people who write the rows gain from a particular answer, `auto` should not act on it alone: keep that answer under review (`calibrate --review`), or leave the decision to people. The steering report says so.
- A "yes" on a yes/no decision is not protected against a claim written twice (#44). Where the people who write the rows gain from a "yes", it should not be automatic: `calibrate --review yes` (#46) keeps every "yes" under review and leaves the "no" band automatic.
