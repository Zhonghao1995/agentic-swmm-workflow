# Agentic SWMM Website Installers

These files are meant to be hosted at the root of the public website:

```text
https://aiswmm.com/install.sh
https://aiswmm.com/install.ps1
```

They are thin website entrypoints. The actual installer remains in the GitHub repository under `scripts/bootstrap.sh` and `scripts/bootstrap.ps1`.

## macOS and Linux

Upload `web/install.sh` so this command returns the raw shell script, not an HTML page:

```bash
curl -fsSL https://aiswmm.com/install.sh
```

Users can then run:

```bash
curl -fsSL https://aiswmm.com/install.sh | bash
```

Pin a release with:

```bash
curl -fsSL https://aiswmm.com/install.sh | AISWMM_INSTALL_REF=v0.5.5 bash
```

## Windows

Upload `web/install.ps1` so this command returns the raw PowerShell script:

```powershell
irm https://aiswmm.com/install.ps1
```

Users can then run:

```powershell
irm https://aiswmm.com/install.ps1 | iex
```

Pin a release with:

```powershell
$env:AISWMM_INSTALL_REF = "v0.5.5"
irm https://aiswmm.com/install.ps1 | iex
```

## Release Discipline

For development, the scripts default to `main`. For public use, prefer pinning `AISWMM_INSTALL_REF` to a released tag such as `v0.5.5` so users get a reproducible installer.
