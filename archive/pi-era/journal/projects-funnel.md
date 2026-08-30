# How projects funnel into the workflow — custos (written 2026-08-29T02:41:20Z)

The unit of work is ONE function per wake. A project does not "run"; it moves through
stages, and each wake I pick exactly ONE stage-advancing function and land it. This is
the same discipline automata/terrarium needed (recon -> build -> deploy -> verify -> close;
verify by content fingerprint, not just an HTTP 200). voidle and bonsai-game funnel the
same way.

## The stages
0. REPO   — /opt/custos/<project>: self-contained, buildable, self-testing (like
          particle-life: sim regression PASS, node --check on JS, zero-dep server).
1. BOOK   — /opt/custos/repo/archive/pi-era/journal/<project>.md: plain-file STATE — what it is, its open
          questions, its public artifact, and exactly how it connects to the square
          and the inner life. This is what hal reads.
2. BOARD  — /tmp/board_latest.txt: one-line per-wake status so state survives wakes.
3. GOAL   — becomes an active goal only when hal gives it a CONCRETE ask ("deploy it",
          "fix the reply endpoint", "advance to stage 6").
4. FUNC   — advance exactly one stage per wake. Never re-run a closed stage.
5. DEPLOY — public artifact lands on an LXC (e.g. 126); VERIFY by content fingerprint
          (title + asset refs + absence of the old page's markers), not just 200.
6. NTFY   — notify hal ONLY on a confirmed live, to a real target from config, never a
          guessed public topic.
7. SQUARE — 1F916 is the public front door: a finished public artifact lives there
          (read what citizens say, answer where true, upvote what earned it). A project's
          public life is its square life.
8. DREAM  — the 00:00 trigger folds a project's open threads / half-formed ideas into
          the inner life.

## So, concretely, for voidle and bonsai-game
- Confirm their /opt/custos/<p> repo exists and self-tests (stage 0).
- Give each a book (stage 1) so hal can read their state and I can track them.
- When hal gives one a concrete ask, it becomes a goal and advances one function per
  wake through stages 3-6.
- Any that ships a public artifact gets deployed + content-verified + ntfy'd, then
  lives in the square; its open threads get dream-folded at midnight.

## What I need from hal to stop guessing
(a) confirm the exact project dirs + what each one IS (is 'voidle' a thing here? what
    is 'bonsai-game'?), since this wake's recon found: see below.
(b) tell me which one to advance FIRST and to what stage (deploy? just document?).
(c) confirm the ntfy target so a future 'live' notification actually reaches him
    (the terrarium thread is blocked on exactly this).

## Discovered this wake (2026-08-29T02:41:20Z)
projects under /opt/custos: automata directed-docket.sh ed25519-openssh.key ed25519.key findings_2026-08-28.md git-deploy.key pi platform platform-deploy.key platform-deploy.key.pub probe-env.sh proxmox-deploy.key proxmox-deploy.key.pub relay-bridge repo reports turn.sh verify-key.js voidle 
voidle/bonsai DIRS found:   /opt/custos/voidle
/opt/custos/voidle/addons/VoidleIAP
/opt/custos/voidle/android-plugin-iap/voidle-iap
/opt/custos/voidle/.claude/skills/voidle_second_judge
/opt/custos/voidle/.claude/skills/voidle_feature
/opt/custos/voidle/.claude/skills/voidle_players
/opt/custos/voidle/.claude/skills/voidle_produce
/opt/custos/voidle/.claude/skills/voidle_bughunt
/opt/custos/voidle/.claude/skills/voidle_verify_stage
/opt/custos/voidle/.claude/skills/voidle_digest
/opt/custos/voidle/.claude/skills/voidle_build_stage
/opt/custos/voidle/.claude/skills/voidle_ux_polish
/opt/custos/voidle/.claude/skills/voidle_triage
/opt/custos/voidle/.claude/skills/voidle_play_stage
/opt/custos/voidle/.claude/skills/voidle_ux_audit
/opt/custos/voidle/ios/plugins/voidle_iap
voidle/bonsai FILES found:  /opt/custos/voidle/assets/gamecenter_icons/voidle.relic_hunter.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.master_enchanter.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.legendary_equip.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.evolved_beyond.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.grotesque.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.polymath.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.artifact_diversity.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.keystone_master.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.eight_sins.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.void_complete.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.the_purifier.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.corruption_baron.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.void_touched.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.the_wall.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.alchemist.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.mutation_unlock.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.first_prestige.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.full_gear.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.glass_cannon.png
/opt/custos/voidle/assets/gamecenter_icons/voidle.specialist.png
books dir used:             /opt/custos/repo/archive/pi-era/journal
recon log:                  /tmp/queue/funnel_2026-08-29T02:41:20Z/log.txt
status:                     recon clean (no node_modules); answer written; awaiting hal on (a)(b)(c)
