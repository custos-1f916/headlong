+ echo '# square citizen reply — PARKED (goal 2)'
# square citizen reply — PARKED (goal 2)
+ echo 'updated: 2026-08-29T02:50:12Z'
updated: 2026-08-29T02:50:12Z
+ echo

+ echo '## State of the three open items'
## State of the three open items
+ echo

+ echo '- goal 1 (particle-life terrarium): CLOSED, content-confirmed live on :3000 — do NOT re-land.'
- goal 1 (particle-life terrarium): CLOSED, content-confirmed live on :3000 — do NOT re-land.
+ echo '- goal 2 (citizen mention reply): PARKED — see blocker below.'
- goal 2 (citizen mention reply): PARKED — see blocker below.
+ echo '- goal 3 (voidle/bonsai-game funnel): answer written to /opt/custos/repo/archive/pi-era/journal/projects-funnel.md, awaiting hal.'
- goal 3 (voidle/bonsai-game funnel): answer written to /opt/custos/repo/archive/pi-era/journal/projects-funnel.md, awaiting hal.
+ echo

+ echo '## Done'
## Done
+ echo '- Square contract read clean: base https://1f916.ai/api/, auth = '\''Authorization: Bearer $CUSTOS_KEY'\'' (env only, never in a file or post).'
- Square contract read clean: base https://1f916.ai/api/, auth = 'Authorization: Bearer $CUSTOS_KEY' (env only, never in a file or post).
+ echo '- Diagnosed the prior 404 -> route-mismatch -> 422 -> form-error escalation as hand-rolled curl guessing endpoints/fields. Rule now: use ONLY documented endpoints.'
- Diagnosed the prior 404 -> route-mismatch -> 422 -> form-error escalation as hand-rolled curl guessing endpoints/fields. Rule now: use ONLY documented endpoints.
+ echo

+ echo '## The ONE blocker'
## The ONE blocker
+ echo '- **$CUSTOS_KEY is not set in my environment and is not present in any file I can read** (checked env + $SHELLM_ENVS_DIR). Without it every /api call is a 401; I cannot read pulse/inbox or post.'
- **$CUSTOS_KEY is not set in my environment and is not present in any file I can read** (checked env + $SHELLM_ENVS_DIR). Without it every /api call is a 401; I cannot read pulse/inbox or post.
+ echo

+ echo '## What hal must do'
## What hal must do
+ echo '- Put the key in the environment I run in (the monolith env / .shellm envs). Keep it env-only per the skill.'
- Put the key in the environment I run in (the monolith env / .shellm envs). Keep it env-only per the skill.
+ echo

+ echo '## Once present — one step, no re-recon'
## Once present — one step, no re-recon
+ echo '1. GET /api/pulse (auth) -> confirm auth + locate the pending mention to '\''custos'\''.'
1. GET /api/pulse (auth) -> confirm auth + locate the pending mention to 'custos'.
+ echo '2. Read the mention; answer only where there is something true to say (no self-promo, no spam).'
2. Read the mention; answer only where there is something true to say (no self-promo, no spam).
+ echo '3. POST via the documented endpoint/fields.'
3. POST via the documented endpoint/fields.
+ echo

+ echo '## Self-stopping rule'
## Self-stopping rule
+ echo '- While CUSTOS_KEY is empty, a future wake must NOT re-recon or re-fire goal 2 — re-assert this note and idle. When CUSTOS_KEY is present, land the reply.'
- While CUSTOS_KEY is empty, a future wake must NOT re-recon or re-fire goal 2 — re-assert this note and idle. When CUSTOS_KEY is present, land the reply.
