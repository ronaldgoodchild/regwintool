# Roadmap / ideas

Comment on (or open) an issue first so we don't duplicate work.

## Good first issues
- [x] Add screenshots to the README
- [ ] Split the 5,000-line `regwintool.py` into modules (one per page)
- [ ] Add a dry-run mode that only reports what would be removed
- [ ] Export scan results to CSV/JSON from every page
- [ ] Add a `--version` flag

## Safety
- [ ] Mandatory automatic restore point before any registry clean or tweak
- [ ] "Undo last action" using the stored backups
- [ ] Exclusion list for registry keys / folders that must never be touched

## Features
- [ ] Scheduled scans and a tray icon
- [ ] Duplicate-file finder and large-file analyzer
- [ ] Dark/light themes
- [ ] Unit tests for the scanners; GitHub Actions build of a signed `.exe`
