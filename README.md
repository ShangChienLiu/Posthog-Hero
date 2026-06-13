# PostHog Engineering Impact Dashboard

Dashboard: https://posthog-impact-dashboard-rho.vercel.app  
Download CSV: https://posthog-impact-dashboard-rho.vercel.app/impact_engineers.csv

This project analyzes the last 90 days of `PostHog/posthog` GitHub activity and ranks the top five most impactful engineers with an explicit, auditable scoring model.

## Approach Summary

I built a single-page engineering impact dashboard for a busy PostHog engineering leader. The goal is not to reward raw activity, commits, or lines of code. I define impact as merged work that combines four signals: valuable delivery, healthy engineering design, quality stewardship, and collaboration load.

The analysis covers `2026-03-15` through `2026-06-13` and includes `8,435` merged PRs, `68,426` commit-file rows, and GraphQL collaboration totals for all merged PRs in the window. Bot PRs are excluded from the leaderboard. The final dashboard shows the top five engineers, their component scores, evidence PRs, formulas, variable definitions, tradeoffs, what was intentionally left out, what breaks first, and what I would build next.

Top five in the current model:

| Rank | Engineer | Score | Delivery | Design | Quality | Collaboration |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | `pauldambra` | 99.6 | 99.7 | 99.2 | 99.7 | 99.7 |
| 2 | `webjunkie` | 98.7 | 98.0 | 99.7 | 99.2 | 98.0 |
| 3 | `sampennington` | 98.3 | 98.6 | 96.9 | 98.6 | 99.2 |
| 4 | `andrewm4894` | 97.5 | 97.5 | 98.6 | 96.3 | 97.5 |
| 5 | `rnegron` | 97.0 | 96.3 | 97.5 | 98.0 | 96.3 |

## What I Built And Why

PostHog is a high-autonomy engineering organization, so a useful impact model should reward more than output volume. A model based on commits, lines changed, or raw PR count would be easy to game and would miss platform work, cross-area help, and review-heavy work. This dashboard instead tries to identify engineers whose merged work creates value while also reducing complexity and helping the broader system move.

The UI is intentionally built for quick executive scanning:

- `Overview`: top five engineers, component scores, selected engineer detail, evidence PRs, and requirement coverage.
- `Formula`: scoring formula, normalization rule, and variable dictionary.
- `Decisions`: tradeoffs, exclusions, pressure failure modes, and next steps.
- CSV download: engineer-level output for external validation.

## Data Pipeline

Data is collected with local scripts and committed as static artifacts so the hosted app does not need a GitHub token in the browser.

Sources:

- GitHub REST Search for merged PRs, issues, releases, and repository metadata.
- GitHub GraphQL for PR collaboration totals: reviews, review threads, comments, commits, and closing issue count.
- Local `git` history from the `master` branch to join merged PRs to changed file paths and path categories.

Key committed files:

- `scripts/fetch_posthog_github_data.py`: fetches the 90-day GitHub and git-history dataset.
- `scripts/fetch_pr_collaboration_totals.py`: fetches GraphQL collaboration totals for every merged PR in the window.
- `scripts/build_impact_model.py`: builds the scoring model and dashboard payload.
- `data/manifest.json`: source window and row counts.
- `data/processed/pr_enriched.csv`: PR-level model input.
- `data/processed/impact_engineers.csv`: engineer-level output.
- `public/impact_engineers.csv`: downloadable hosted CSV.
- `src/data/impactData.json`: compact static dashboard dataset.

Raw GitHub API responses are intentionally not committed. Public PR bodies can contain user-posted credentials, and GitHub push protection flagged one raw PR-search blob during publication. The committed processed datasets retain the fields needed to validate and run the dashboard without publishing raw bodies.

## Impact Model

The model starts from merged PRs because merged work reached the mainline. It then uses log-scaled file scope and intent/category signals to avoid rewarding giant changes for their own sake.

### Scope

```text
S_p = ln(1 + max(F_p, 1))
```

`F_p` is the unique changed file-path count for PR `p`. Log scaling gives credit for meaningful scope but makes large PRs have diminishing returns.

### Delivery

```text
D_e = sum_p W_intent(p) * S_p
```

`W_intent` weights PR intent inferred from title prefixes and labels. Feature, fix, performance, refactor, CI, test, docs, chore, dependency, and revert work are treated differently. This is a proxy for shipped value, not a claim about revenue impact.

### Design Health

```text
A_e = sum_p S_p * (0.65*X_p + 0.25*min(K_p-1,3) + 0.20*G_p)
```

`X_p` indicates cross-boundary work outside the author's inferred home area. `K_p` is path-category breadth. `G_p` indicates stewardship work touching tests, docs, or infra. This rewards work that helps outside a narrow silo, but the home-area proxy can misclassify rotations and platform owners.

### Quality Stewardship

```text
Q_e = sum_p S_p * Q_intent_and_stewardship(p)
```

This gives credit to fixes, performance work, refactors, tests, infra, and stabilizing reverts. It is intentionally not a deep semantic code-quality analyzer.

### Collaboration

```text
C_e = sum_p ln(1 + R_p + T_p + 0.5*M_p) * sqrt(S_p)
```

`R_p` is review count, `T_p` is review-thread count, and `M_p` is conversation comments on authored PRs. This captures collaboration load carried by the authored work. It does not yet credit reviewer-side mentorship directly.

### Normalization

```text
N(x) = 100 * (count(raw < x) + 0.5*count(raw = x)) / n_humans
```

Each component score is an empirical mid-rank percentile among human engineers. I chose this over a P99 cap because a P99 cap can make one outlier appear to get `100` in every formula. The current method still lets someone rank first, but it avoids implying mathematical perfection.

### Final Engineering Impact Score

```text
EIS_e = 0.35*N(D_e) + 0.25*N(A_e) + 0.25*N(Q_e) + 0.15*N(C_e)
```

Weights keep delivery important while reserving 65% of the model for design health, quality, and collaboration.

## Key Decisions And Tradeoffs

- I used merged PRs as the unit of work because they represent changes that reached the mainline.
- I used log-scaled file paths instead of lines of code to reduce incentives for huge PRs.
- I inferred home area from recent path history because no team ownership map was available.
- I used empirical percentiles so component scores are readable and robust to outliers.
- I kept the dashboard static and precomputed so it loads quickly and does not expose GitHub credentials.

## What I Intentionally Left Out

- Lines of code, raw commits, story points, and raw file count as direct score drivers.
- Developer satisfaction, meeting load, and flow-state data because they require perceptual survey data outside GitHub.
- Incidents, deployment health, customer adoption, and revenue impact because they require production and product analytics joins.
- Deep review quality and reviewer-side mentorship because this would require comment-body analysis and reviewer identity extraction.
- Full AI-generated-code attribution. PR labels and text may mention agents, but the model does not yet separate human design from generated mechanical changes.

## What Breaks First Under Pressure

- Home-area inference breaks for rotations, platform owners, and engineers who intentionally work across boundaries.
- Review-thread counts can reward controversial PRs unless comment quality and resolution quality are analyzed.
- GitHub Search pagination needs more careful bucketing if the time window expands beyond 90 days or the repo volume increases.
- Bot detection needs maintenance as automation accounts change.
- Business impact cannot be validated from GitHub alone.

## What I Would Build Next

- Fetch review authors and review/comment bodies for the top-candidate subset to score mentorship and review quality.
- Join CODEOWNERS or team ownership metadata to replace the path-based home-area proxy.
- Join incidents, release notes, deployment data, and PostHog product analytics to validate operational and customer impact.
- Add AI-assistance detection so generated mechanical work can be separated from human design and judgment.
- Add confidence intervals or sensitivity analysis for ranking stability under different weighting choices.

## Run Locally

```bash
npm install
npm run dev
```

## Rebuild Data

```bash
python3 scripts/fetch_posthog_github_data.py --days 90
python3 scripts/fetch_pr_collaboration_totals.py
python3 scripts/build_impact_model.py
```

The fetch scripts require a GitHub token in the local environment for authenticated API access. The built dashboard does not expose or require that token.

## Validate

```bash
npm run build
npm audit --json
```

Checks run before deployment:

- `npm run build`
- `npm audit --json`
- Local browser QA at 1280x720 and mobile width
- Production HTTP checks for dashboard and CSV

## Deploy

```bash
npm run build
vercel --prod --yes
```

## Security And Repository Hygiene

The repository intentionally ignores:

- `node_modules/`
- `dist/`
- `.vercel/`
- `data/external/`
- `data/raw/`
- local logs, `.env*`, `.pem`, and `.key` files

Before pushing, I ran a high-confidence credential scan over commit candidates for common GitHub, OpenAI, AWS, Slack, and private-key patterns. No committed candidate file matched those credential patterns. A local `appmap.log` file contained environment variables and was deleted, then logs were added to `.gitignore`.
