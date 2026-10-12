# GitHub development workflow

The canonical private repository is `https://github.com/ukyovfx/vfx-kitsu-files-plugin`.
The default branch is `main`; implementation work is developed on `codex/*` branches and reviewed through pull requests. Keep the KitsuSync repository and its release process separate.

## Clone and branch

```powershell
git clone https://github.com/ukyovfx/vfx-kitsu-files-plugin.git
cd vfx-kitsu-files-plugin
git switch -c codex/<short-task-name>
```

For an existing feature branch, switch to it and pull with fast-forward only:

```powershell
git switch codex/vfx-files-plugin-implementation
git pull --ff-only origin codex/vfx-files-plugin-implementation
```

Commit focused changes, run the relevant local and isolated-host checks, then push normally and open a draft pull request into `main`:

```powershell
git push -u origin HEAD
gh pr create --draft --base main --head (git branch --show-current)
```

Never force-push shared branches. Keep credentials in local environment files (which are ignored), use synthetic data and FakeStorage for tests, and do not commit databases, local media, host downloads, build output, or development environments. Do not merge or release without separate authorization.
