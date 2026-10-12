# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- `air start` bound to a loopback address now rejects requests whose `Host` header is not loopback (`localhost`, `127.0.0.1`, `::1`), closing DNS-rebinding access to the unauthenticated local API from a malicious web page. `AIR_ALLOWED_HOSTS` (comma-separated) adds names such as a reverse proxy; non-loopback binds are unchanged unless `AIR_ALLOWED_HOSTS` is set.

### Added

- Dependabot configuration for pip, npm (`web/`) and GitHub Actions (weekly).
- CI workflow running the pytest suite and the console typecheck/build on pushes to `main` and on pull requests.
- SECURITY.md describing private vulnerability reporting.
- This CHANGELOG.
