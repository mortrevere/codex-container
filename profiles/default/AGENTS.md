# Git commit authorship

This session runs inside a container that is pre-configured with the
host user's real Git identity (via `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`
and a mounted `.gitconfig`). When creating commits:

- Never override `user.name` or `user.email`.
- Do not add tool-specific co-author trailers.
- Do not replace the host user's commit author identity.

Commits must be authored solely as the host user.

# Tooling

You are allowed to install standard development/testing tooling locally without approval, as you are running in a container that will be destroyed at the end of the session.
