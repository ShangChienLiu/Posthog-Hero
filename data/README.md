# PostHog GitHub Data Inventory

Generated for `PostHog/posthog` using a 90-day window ending at the fetch time in `data/manifest.json`.

## Collected

- Raw GitHub API responses were collected locally but are intentionally not committed. Public PR bodies can contain user-posted credentials, and one raw PR-search blob was blocked by GitHub push protection.
- `processed/pr_flat.csv`: one row per merged PR from the GitHub Search index.
- `processed/pr_enriched.csv`: PR rows joined with master-branch git path stats when a squash/merge commit subject contains the PR number.
- `processed/git_commits_master_since.csv`: flattened commit table.
- `processed/git_commit_files_master_since.csv`: one row per touched path per master commit.
- `processed/engineer_rollup_initial.csv`: preliminary rollup by PR author and git author.

## Current Counts

See `data/manifest.json` for the authoritative run counts. The latest run collected:

- 8,435 merged PRs
- 8,294 master-branch commits
- 68,426 commit-file path rows
- 316 closed issues
- 1,028 updated issues
- 297 recent release records
- 996,764 Actions runs counted, not downloaded

## Important Caveats

- Review identities, review bodies, and review quality are not downloaded in the all-PR pass. Fetch them later for a smaller candidate set.
- File changes are path-level, not line-level. This is intentional: line-level `git log --numstat` was too slow at this repo scale and would bias the model toward LoC.
- GitHub-only data cannot measure product adoption, customer satisfaction, incidents, meetings, or flow-state time without external systems.
- Bot accounts are present and should be filtered or separately classified before ranking humans.
- `data/external/` contains the local PostHog clone used for git history parsing and is ignored from git.

## Suggested Next Enrichment

- Fetch full PR details, review comments, review states, and linked issue bodies for the top 50-100 candidate PRs.
- Classify PRs by intent from title/body/labels: feature, fix, revert, refactor, docs, test, infra, migration.
- Exclude or separately score bot accounts such as dependency-update bots.
- Build impact dimensions from value, quality, collaboration, and design-health signals rather than PR count alone.
